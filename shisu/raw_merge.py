"""未統合の過去基本CSV（raw_csv/）を csv_files/全場_タイムM.csv・全場_対戦M.csv に統合する（最大10年分を残す）。

半年〜1年に1回、TARGET ★6タブで追加分を出したら raw_csv/ に置く（または /ingest 画面からアップロード）。
  - 種類はファイル名で決める: 「対戦」「match」→ 対戦M、「タイム」「time」「マイニング」「mining」→ タイムM
    （「対戦型マイニング」はマイニングも含むので、対戦を先に見る）。どちらでもないファイルは触らない
  - 既存の csv_files/全場_{種類}.csv と、raw_csv のファイル（更新日時の古い順）を縦につなぎ、レースID(新) が同じ馬の行は
    後のファイルの行を使う（追加抽出した新しいファイルが優先）。レース単位（16桁）の ID は 馬番 を付けて馬単位で比べる。
    レースID(新) の無い行・16桁で馬番の読めない行はそのまま残す
  - 前走の列（前走場所〜前走レースID(新)・間隔・休み明け～戦目）が全部空の行（TARGET で前走データ読込をせずに出した行）は、
    統合した全期間の 血統登録番号 ごとの履歴から補って書く（shisu.conditions.fill_previous_runs。10年より古い行を消す前に。
    補った行には 前走補完=1 の印を付け、次の統合で作り直す）。重複の置き換えで後の行が前走の列を全部空で、前の行に TARGET の
    前走の値があれば、その値を残す（TARGET の値を優先。地方・海外の前走は履歴から作れない）
  - 書き出しは一時ファイルに書いてから置き換える（途中で止まっても既存の 全場_*.csv を壊さない）
  - 最大10年分を残す: 基準年（既定はデータの中の最新年。ref_year で指定もできる）の9年前以降。
    例: 2026年が基準なら 2017〜2026年を残し、2016年以前は消す。年は レースID(新) の先頭4桁（無ければ 日付S）
  - 統合したファイルは raw_csv/merged_done/ に移す（同じ名前があれば _2 などを付ける。次回また統合しない）
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

from .conditions import PREV_FILLED_MARK, fill_previous_runs, previous_columns, previous_loaded
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
    prev_filled: int = 0                                  # 前走の列を履歴から補った行（10年の窓に残った行）
    prev_kept: int = 0                                    # 重複の置き換えで、前の行の TARGET の前走の値を残した行
    moved: list[Path] = field(default_factory=list)

    def summary(self) -> str:
        span = f"{self.years[0]}〜{self.years[1]}年" if self.years else "年の分からない行だけ"
        return (f"{self.kind}: 既存 {self.before} 行 ＋ 追加 {len(self.files)} 本 {self.added_rows} 行 → "
                f"{self.after} 行（{span}。重複の置き換え {self.replaced} 行・10年より古い行の削除 {self.dropped_old} 行"
                + (f"・前走の列を履歴から補った {self.prev_filled} 行" if self.prev_filled else "")
                + (f"・重なった行で TARGET の前走の値を残した {self.prev_kept} 行" if self.prev_kept else "") + "）"
                f" → {self.out_path.name}")


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
    # 残す（後の）行が前走を読み込まずに出した行（前走の12列が全部空）で、捨てる（前の）行に TARGET の前走の値があれば、
    # その値を残す行に写す（TARGET の値を優先する。地方・海外の前走などは履歴から作り直せない）
    res.prev_kept = _carry_previous(df, rid, has, dup)
    df = df[~dup]
    # 前走の列が空の行（TARGET で前走データ読込をせずに出した行）を、統合した全期間の 血統登録番号 の履歴から補う
    # （10年より古い行を消す前に。窓の最初の年の馬も、その前の走りから補える）
    df = fill_previous_runs(df, log=log)
    cols += [c for c in df.columns if c not in cols]
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
    if PREV_FILLED_MARK in df.columns:
        res.prev_filled = int((df[PREV_FILLED_MARK].astype(str) == "1").sum())
    # 一時ファイルに書いてから置き換える（途中で止まっても 全場_*.csv の履歴を壊さない）
    tmp = out.with_name(f".{out.name}.tmp")
    write_csv(df[cols], tmp)
    os.replace(tmp, out)
    if res.no_year:
        log(f"[WARN] {out.name}: 年の分からない行 {res.no_year} 行は残しました（レースID(新)・日付S が無い）")
    return res


def _carry_previous(df: pd.DataFrame, rid: pd.Series, has: pd.Series, dup: pd.Series) -> int:
    """重複の置き換えで、前の行の前走の12列を、前走の列が全部空の後の行に写す（df をその場で書き換える）。写した行数。"""
    cols = previous_columns(df)
    if not cols or not dup.any():
        return 0
    loaded = previous_loaded(df)
    empty = pd.Series(True, index=df.index)
    for c in cols:
        empty &= df[c].astype(str).str.strip() == ""
    donors = df.index[dup & loaded]
    if not len(donors):
        return 0
    last_donor = pd.Series(donors, index=rid[donors].to_numpy()).groupby(level=0).last()   # 同じ馬の行が3本以上なら最後の
    kept = df.index[has & ~dup & empty & rid.isin(last_donor.index)]
    if not len(kept):
        return 0
    src = last_donor.loc[rid[kept].to_numpy()].to_numpy()
    for c in cols:
        df.loc[kept, c] = df.loc[src, c].to_numpy()
    if PREV_FILLED_MARK in df.columns:
        df.loc[kept, PREV_FILLED_MARK] = ""
    return len(kept)


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
              log: Logger = print) -> list[MergeResult]:
    """raw_csv/ の基本CSV（files を渡せばそれだけ）を種類ごとに統合する。統合したファイルは merged_done/ に移す。"""
    raw_dir = Path(raw_dir)
    files = [Path(f) for f in files] if files is not None else pending_files(raw_dir)
    by_kind: dict[str, list[Path]] = {k: [] for k in KINDS}
    for p in files:
        k = kind_of(p.name)
        if k is None:
            log(f"[WARN] raw_csv/{p.name}: 名前から タイムM か 対戦M か分からないので統合しません"
                "（名前に「タイム」「マイニング」または「対戦」を入れてください）")
            continue
        by_kind[k].append(p)
    results = []
    for kind, fs in by_kind.items():
        if not fs:
            continue
        res = merge_kind(kind, fs, csv_dir, years, ref_year, log)
        if res.files and move:
            done = raw_dir / DONE_DIR
            done.mkdir(parents=True, exist_ok=True)
            for p in res.files:
                if Path(p).parent.resolve() != raw_dir.resolve():
                    continue                    # raw_csv の外のファイル（直接渡したもの）は動かさない
                dst = _unique(done / p.name)
                shutil.move(str(p), str(dst))
                res.moved.append(dst)
        if res.files:
            log(f"[INFO] 過去基本CSVを統合しました: {res.summary()}")
        results.append(res)
    return results


def safe_upload_name(name: str) -> str:
    """アップロードのファイル名（パスの部分・制御文字を除く）。"""
    s = unicodedata.normalize("NFC", str(name or "")).replace("\\", "/").split("/")[-1]
    s = re.sub(r"[\x00-\x1f\x7f:*?\"<>|]", "_", s).strip().lstrip(".")
    return s or "upload.csv"


__all__ = ["KINDS", "DEFAULT_YEARS", "DONE_DIR", "MergeResult", "kind_of", "pending_files", "merge_kind", "merge_raw",
           "safe_upload_name"]
