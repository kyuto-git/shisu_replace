"""未統合の過去基本CSV（raw_csv/）を csv_files/全場_タイムM.csv・全場_対戦M.csv に統合する（最大10年分を残す）。

半年〜1年に1回、TARGET ★6タブで追加分を出したら raw_csv/ に置く（または /ingest 画面からアップロード）。
  - 種類はファイル名で決める: 「対戦」「match」→ 対戦M、「タイム」「time」「マイニング」「mining」→ タイムM
    （「対戦型マイニング」はマイニングも含むので、対戦を先に見る）。どちらでもないファイルは触らない
  - 既存の csv_files/全場_{種類}.csv と、raw_csv のファイル（更新日時の古い順）を縦につなぎ、レースID(新) が同じ馬の行は
    後のファイルの行を使う（追加抽出した新しいファイルが優先）。レース単位（16桁）の ID は 馬番 を付けて馬単位で比べる。
    レースID(新) の無い行・16桁で馬番の読めない行はそのまま残す
  - 書き出しは一時ファイルに書いてから置き換える（途中で止まっても既存の 全場_*.csv を壊さない）
  - 最大10年分を残す: 基準年（既定はデータの中の最新年。ref_year で指定もできる）の9年前以降。
    例: 2026年が基準なら 2017〜2026年を残し、2016年以前は消す。年は レースID(新) の先頭4桁（無ければ 日付S）
  - 統合したファイルは raw_csv/merged_done/ に移す（同じ名前があれば _2 などを付ける。次回また統合しない）
  - **列パッチ**: `レースID(新)` があって `指数` 列の無いCSV（前走の列だけを出した軽いCSVなど。名前は問わない）は、
    基本CSVではなく列パッチとして扱う。全部のパッチを読み、レースID(新)（18桁。16桁なら馬番を付ける）ごとに、
    パッチにある列（レースID(新) 以外）の空でない値で csv_files/全場_タイムM.csv・全場_対戦M.csv の両方の同じ馬の行を
    上書き・補完する（無い列は足す。複数のパッチで同じ馬・同じ列なら後のファイルの値）。基本CSVの統合のあとに当てる。
    全部の行が 全場_タイムM・全場_対戦M の両方に当たったパッチだけ merged_done/ に移す（片方の 全場_*.csv が無い・
    そこに無い行があるパッチは、当たった分は反映して raw_csv に残す。次に回すとまた当てる。値は同じなので害は無い）。
    指数順位 の列はタイムM・対戦M で値が違うので、パッチにあっても書き換えない
replace_csv_ver3.py は raw_csv/ に未統合のCSVがあれば、master を作る前にこれを実行する。
"""
from __future__ import annotations

import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .csvio import norm_series, read_csv_any, write_csv
from .normalize import horse_race_id

KINDS = ("タイムM", "対戦M")
MERGED_NAME = "全場_{kind}.csv"
DONE_DIR = "merged_done"
DEFAULT_YEARS = 10
RID = "レースID(新)"
Logger = Callable[[str], None]


def kind_of(name: str) -> str | None:
    """ファイル名 → 'タイムM' / '対戦M' / None。"""
    s = unicodedata.normalize("NFKC", str(name)).lower()
    if "対戦" in s or "match" in s:
        return "対戦M"
    if any(t in s for t in ("タイム", "time", "マイニング", "mining")):
        return "タイムM"
    return None


def pending_files(raw_dir: Path | str) -> list[Path]:
    """raw_csv/ の直下の CSV（merged_done/ の中は見ない）。更新日時の古い順（同じなら名前順）。"""
    d = Path(raw_dir)
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".csv" and not p.name.startswith(".")]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


@dataclass
class MergeResult:
    kind: str
    out_path: Path
    files: list[Path] = field(default_factory=list)      # 統合した raw_csv のファイル
    before: int = 0                                       # 既存の 全場_{種類}.csv の行数
    added_rows: int = 0                                   # raw_csv のファイルの行数の合計
    replaced: int = 0                                     # レースID(新) が重なって後の行に置き換えた数
    dropped_old: int = 0                                  # 10年より古くて消した行
    no_year: int = 0                                      # 年の分からない行（残す）
    after: int = 0
    years: tuple[int, int] | None = None                  # 残した年の範囲
    moved: list[Path] = field(default_factory=list)

    def summary(self) -> str:
        span = f"{self.years[0]}〜{self.years[1]}年" if self.years else "年の分からない行だけ"
        return (f"{self.kind}: 既存 {self.before} 行 ＋ 追加 {len(self.files)} 本 {self.added_rows} 行 → "
                f"{self.after} 行（{span}。重複の置き換え {self.replaced} 行・10年より古い行の削除 {self.dropped_old} 行）"
                f" → {self.out_path.name}")


PATCH_KIND = "列パッチ"


def _columns(path: Path) -> list[str] | None:
    """CSV の見出し（読めなければ None）。cp932 → utf-8-sig → utf-8 の順。レースID(新) が見えなければ UTF-8 でも読み直す。"""
    found = None
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            cols = [str(c).strip() for c in pd.read_csv(path, encoding=enc, dtype=str, nrows=0).columns]
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
        if RID in cols:
            return cols
        found = found or cols
    return found


def file_role(path: Path | str) -> str | None:
    """raw_csv のファイルの扱い: 'タイムM' / '対戦M'（基本CSV）・PATCH_KIND（列パッチ）・None（分からない）。
    レースID(新) があって 指数 列の無いCSVは、名前に関係なく列パッチ。それ以外は名前で種類を決める。"""
    cols = _columns(Path(path))
    if cols is not None and RID in cols and "指数" not in cols:
        return PATCH_KIND
    return kind_of(Path(path).name)


@dataclass
class PatchResult:
    """列パッチ（§3.9）の結果。"""
    kind: str = PATCH_KIND
    files: list[Path] = field(default_factory=list)            # 当てたパッチ（読めたもの）
    complete: list[Path] = field(default_factory=list)         # 全部の行が 全場_タイムM・全場_対戦M の両方に当たったパッチ
    rows: int = 0                                               # パッチの行（18桁の レースID(新) がある行）
    bad_ids: int = 0                                            # レースID(新) が18桁にならない行（当てない）
    missing: dict[str, int] = field(default_factory=dict)      # 全場_*.csv → その中に無い（当たらない）パッチの行
    updated: dict[str, int] = field(default_factory=dict)      # 全場_*.csv → 値が変わった行
    columns: dict[str, dict[str, int]] = field(default_factory=dict)   # 全場_*.csv → 列 → 値が変わったセル
    moved: list[Path] = field(default_factory=list)

    @property
    def unmatched(self) -> int:
        return max(self.missing.values(), default=0)

    def summary(self) -> str:
        targets = "・".join(f"{name} 更新 {n} 行" for name, n in self.updated.items()) or "当てる 全場_*.csv がありません"
        cols: list[str] = []
        for per in self.columns.values():
            cols += [c for c in per if c not in cols]
        extra = "".join(f"・{name} に無い {n} 行" for name, n in self.missing.items() if n)
        extra += f"・レースID(新) が18桁でない {self.bad_ids} 行" if self.bad_ids else ""
        return (f"前走・追加列パッチを反映しました: {targets}（パッチ {len(self.files)} 本 {self.rows} 行{extra}。"
                f"対象列: {'・'.join(cols) if cols else 'なし（値の変わる列なし）'}）")


PATCH_PROTECTED = (RID, "指数", "指数順位")         # パッチで書き換えない列（キーと、タイムM・対戦M で値の違う列）


def _blank(s: pd.Series) -> pd.Series:
    return s.astype(object).where(s.notna(), "").astype(str).str.strip() == ""


def _col_norm(name) -> str:
    return unicodedata.normalize("NFKC", str(name)).strip()


def apply_patches(files: list[Path], csv_dir: Path | str, log: Logger = print) -> PatchResult:
    """列パッチ（files。古い順）を csv_files/全場_タイムM.csv・全場_対戦M.csv に当てる（パッチのファイルは動かさない）。
    res.complete は、全部の行が両方の 全場_*.csv に当たったパッチ（merge_raw はこれだけを merged_done/ に移す）。"""
    res = PatchResult()
    frames = []
    for p in files:
        try:
            df = read_csv_any(p)
        except Exception as e:
            log(f"[WARN] raw_csv/{p.name} を読めないのでパッチを当てません（{type(e).__name__}: {e}）")
            continue
        df.columns = [str(c).strip() for c in df.columns]
        if RID not in df.columns:
            log(f"[WARN] raw_csv/{p.name} に {RID} 列が無いのでパッチを当てません")
            continue
        df["__src__"] = len(res.files)
        frames.append(df)
        res.files.append(p)
    if not frames:
        return res
    cols: list[str] = []
    for f in frames:
        cols += [c for c in f.columns if c not in cols]
    patch = pd.concat([f.reindex(columns=cols) for f in frames], ignore_index=True)
    src = patch.pop("__src__").astype(int)
    patch = patch.astype(object).where(patch.notna(), "")
    uma = patch["馬番"] if "馬番" in patch.columns else pd.Series("", index=patch.index)
    key = horse_race_id(patch[RID], uma)
    ok = key.str.fullmatch(r"\d{18}").fillna(False).astype(bool)
    res.bad_ids = int((~ok).sum())
    res.rows = int(ok.sum())
    patch, key, src = patch[ok], key[ok], src[ok]
    protected = {_col_norm(c) for c in PATCH_PROTECTED}
    patch_cols = [c for c in patch.columns if _col_norm(c) not in protected and not str(c).startswith("Unnamed:")]
    values: dict[str, pd.Series] = {}                 # 列 → (馬 → 値)。空の値は当てない。同じ馬は後のファイルの値
    for c in patch_cols:
        has = ~_blank(patch[c])
        if has.any():
            v = pd.Series(patch.loc[has, c].to_numpy(), index=key[has].to_numpy())
            values[c] = v[~v.index.duplicated(keep="last")]
    landed = pd.Series(True, index=key.index)         # 両方の 全場_*.csv にある行
    for kind in KINDS:
        out = Path(csv_dir) / MERGED_NAME.format(kind=kind)
        if not out.exists():
            res.missing[out.name] = res.rows
            landed[:] = False
            continue
        df = read_csv_any(out)
        tuma = df["馬番"] if "馬番" in df.columns else pd.Series("", index=df.index)
        tkey = horse_race_id(df[RID], tuma) if RID in df.columns else pd.Series("", index=df.index)
        here = key.isin(set(tkey))
        res.missing[out.name] = int((~here).sum())
        landed &= here
        by_norm = {_col_norm(c): c for c in df.columns}   # 見出しの空白・全角半角のゆれは、全場_*.csv の列に合わせる
        changed_rows = pd.Series(False, index=df.index)
        per: dict[str, int] = {}
        for c, v in values.items():
            new = tkey.map(v)
            hit = new.notna()
            if not hit.any():
                continue
            name = by_norm.get(_col_norm(c), c)
            if name not in df.columns:
                df[name] = ""
                by_norm[_col_norm(name)] = name
            cur = df[name].astype(object).where(df[name].notna(), "")
            diff = hit & (cur.astype(str) != new.astype(str))
            if diff.any():
                df[name] = cur.where(~diff, new)
                per[name] = per.get(name, 0) + int(diff.sum())
                changed_rows |= diff
        res.updated[out.name] = int(changed_rows.sum())
        res.columns[out.name] = per
        if per:
            tmp = out.with_name(f".{out.name}.tmp")          # 一時ファイルに書いてから置き換える
            write_csv(df, tmp)
            os.replace(tmp, out)
    for i, p in enumerate(res.files):
        mine = src == i
        if mine.any() and landed[mine].all():
            res.complete.append(p)
        elif mine.any():
            detail = []
            for kind in KINDS:
                name = MERGED_NAME.format(kind=kind)
                if name not in res.updated:
                    detail.append(f"{name} がありません")
            n_left = int((~landed[mine]).sum())
            log(f"[WARN] 列パッチ {p.name}: {int(mine.sum())} 行のうち {n_left} 行が 全場_タイムM.csv・全場_対戦M.csv の"
                f"両方には当たっていません{('（' + '・'.join(detail) + '）') if detail else ''}。raw_csv に残します"
                "（そのレースの基本CSVを統合したあとに、もう一度回すと当たります。当たらないのが基本CSVに無いレースだけなら、"
                "このファイルは手で merged_done/ に移すか消してください）")
        else:
            log(f"[WARN] 列パッチ {p.name}: レースID(新) が18桁の行がありません。raw_csv に残します")
    if not res.updated:
        log(f"[WARN] {MERGED_NAME.format(kind='タイムM')}・{MERGED_NAME.format(kind='対戦M')} が無いので、列パッチ "
            f"{len(res.files)} 本を当てられません（基本CSVを先に統合してください。パッチは raw_csv に残します）")
    return res


def _year(df: pd.DataFrame) -> pd.Series:
    rid = norm_series(df[RID]) if RID in df.columns else pd.Series("", index=df.index)
    y = rid.str.extract(r"^(\d{4})\d{4}", expand=False)
    if "日付S" in df.columns:
        ds = norm_series(df["日付S"]).str.replace(r"\D", "", regex=True).str.extract(r"^(\d{4})\d{4}", expand=False)
        y = y.fillna(ds)
    return pd.to_numeric(y, errors="coerce")


def merge_kind(kind: str, files: list[Path], csv_dir: Path | str, years: int = DEFAULT_YEARS,
               ref_year: int | None = None, log: Logger = print) -> MergeResult:
    """1種類（タイムM / 対戦M）を統合して csv_files/全場_{種類}.csv を書く（raw_csv のファイルは動かさない）。"""
    out = Path(csv_dir) / MERGED_NAME.format(kind=kind)
    res = MergeResult(kind, out)
    frames = []
    if out.exists():
        cur = read_csv_any(out)
        res.before = len(cur)
        frames.append(cur)
    for p in files:
        try:
            df = read_csv_any(p)
        except Exception as e:          # 読めないファイルは飛ばして、そのまま raw_csv に残す
            log(f"[WARN] raw_csv/{p.name} を読めないので統合しません（{type(e).__name__}: {e}）")
            continue
        if RID not in df.columns:
            log(f"[WARN] raw_csv/{p.name} に {RID} 列が無いので統合しません（TARGET ★6タブの基本CSVを置いてください）")
            continue
        frames.append(df)
        res.files.append(p)
        res.added_rows += len(df)
    if not res.files:
        return res
    cols: list[str] = []
    for f in frames:
        cols += [c for c in f.columns if c not in cols]
    df = pd.concat([f.reindex(columns=cols) for f in frames], ignore_index=True)
    df = df.astype(object).where(df.notna(), "")
    # 重複の判定は馬単位（18桁）で。レース単位（16桁）の ID は 馬番 を付けてそろえる（そのままだと1レースが1頭に潰れる）。
    # 16桁のまま馬番の読めない行は判定しない（残す）
    rid = horse_race_id(df[RID], df["馬番"] if "馬番" in df.columns else pd.Series("", index=df.index))
    has = ((rid != "") & ~rid.str.fullmatch(r"\d{16}").fillna(False)).astype(bool)
    dup = has & rid.where(has, None).duplicated(keep="last")            # 後のファイルの行を残す
    res.replaced = int(dup.sum())
    df = df[~dup]
    y = _year(df)
    res.no_year = int(y.isna().sum())
    if y.notna().any():
        end = int(ref_year) if ref_year else int(y.max())
        start = end - int(years) + 1
        old = y.notna() & (y < start)
        res.dropped_old = int(old.sum())
        df = df[~old]
        kept = y[~old].dropna()
        res.years = (int(kept.min()), int(kept.max())) if len(kept) else None
    res.after = len(df)
    # 一時ファイルに書いてから置き換える（途中で止まっても 全場_*.csv の履歴を壊さない）
    tmp = out.with_name(f".{out.name}.tmp")
    write_csv(df[cols], tmp)
    os.replace(tmp, out)
    if res.no_year:
        log(f"[WARN] {out.name}: 年の分からない行 {res.no_year} 行は残しました（レースID(新)・日付S が無い）")
    return res


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    k = 2
    while True:
        cand = dest.with_name(f"{dest.stem}_{k}{dest.suffix}")
        if not cand.exists():
            return cand
        k += 1


def merge_raw(raw_dir: Path | str, csv_dir: Path | str, files: Iterable[Path | str] | None = None,
              years: int = DEFAULT_YEARS, ref_year: int | None = None, move: bool = True,
              log: Logger = print) -> list:
    """raw_csv/ の基本CSV（files を渡せばそれだけ）を種類ごとに統合し、そのあと列パッチを当てる。
    統合・当てたファイルは merged_done/ に移す。戻り値は MergeResult（種類ごと）と PatchResult（パッチがあれば）。"""
    raw_dir = Path(raw_dir)
    files = [Path(f) for f in files] if files is not None else pending_files(raw_dir)
    by_kind: dict[str, list[Path]] = {k: [] for k in KINDS}
    patches: list[Path] = []
    for p in files:
        k = file_role(p)
        if k == PATCH_KIND:
            patches.append(p)
            continue
        if k is None:
            log(f"[WARN] raw_csv/{p.name}: 名前から タイムM か 対戦M か分からないので統合しません"
                "（基本CSVなら名前に「タイム」「マイニング」または「対戦」を入れてください。"
                "列パッチなら レースID(新) と補う列だけにしてください（指数 列を入れない））")
            continue
        by_kind[k].append(p)

    def move_done(res) -> None:
        if not (res.files and move):
            return
        done = raw_dir / DONE_DIR
        done.mkdir(parents=True, exist_ok=True)
        for p in res.files:
            if Path(p).parent.resolve() != raw_dir.resolve():
                continue                    # raw_csv の外のファイル（直接渡したもの）は動かさない
            dst = _unique(done / p.name)
            shutil.move(str(p), str(dst))
            res.moved.append(dst)

    results: list = []
    for kind, fs in by_kind.items():
        if not fs:
            continue
        res = merge_kind(kind, fs, csv_dir, years, ref_year, log)
        move_done(res)
        if res.files:
            log(f"[INFO] 過去基本CSVを統合しました: {res.summary()}")
        results.append(res)
    if patches:                             # 基本CSVを統合したあとに当てる（今回統合した行にも当たる）
        res = apply_patches(patches, csv_dir, log)
        if res.updated:
            log(f"[INFO] {res.summary()}")
        done_files, res.files = res.files, res.complete     # 両方に全部当たったパッチだけ merged_done/ に移す
        move_done(res)
        res.files = done_files
        results.append(res)
    return results


def safe_upload_name(name: str) -> str:
    """アップロードのファイル名（パスの部分・制御文字を除く）。"""
    s = unicodedata.normalize("NFC", str(name or "")).replace("\\", "/").split("/")[-1]
    s = re.sub(r"[\x00-\x1f\x7f:*?\"<>|]", "_", s).strip().lstrip(".")
    return s or "upload.csv"


__all__ = ["KINDS", "DEFAULT_YEARS", "DONE_DIR", "PATCH_KIND", "MergeResult", "PatchResult", "kind_of", "file_role",
           "pending_files", "merge_kind", "apply_patches", "merge_raw", "safe_upload_name"]
