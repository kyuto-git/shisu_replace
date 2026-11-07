"""安井サロンの月別 Excel（ステータス新バージョンYYYY_M月.xlsx）から数値系の指数を取り出す。

Excel の形（2025年11月〜2026年2月の4ファイルで確認）:
  - シート名 = 開催日の MMDD（1206, 0104 など）。ファイル名の年・月と合わせて日付にする
    （ファイルの月と違う月のシートもある。2026_2月 の 0301 など。年をまたぐときは年を1つずらす）
  - 1行目 空 / 2行目 大分類（レース情報・基本情報・ステータス・状態・馬場適性・距離適性・脚質適性・スキル）/
    3行目 列名 / 4行目以降 1頭1行。A列は空
  - 列（0始まり。A列=0）: 1 場, 2 レース, 3 条件（例「３歳未勝利　ダート1200ｍ」）, 4 枠番, 5 馬番（'01'）,
    6〜8 状態/展開/能力（◎○▲△）, 9 ランク（数値）, 10 ランクの評価（A+〜G）, 11 馬名,
    12 スピード, 14 スタミナ, 16 パワー, 18 根性, 20 賢さ（「48⑥」= 値48・順位6。「-17⑫」「⑯」だけのセルもある）,
    13・15・17・19・21 各ステータスの評価（A+〜G）, 22 体力（1〜5）, 23 やる気（0〜5）,
    24〜33 馬場・距離・脚質適性（S・A〜G）, 34〜47 スキル（直線〜冬。○印）
  列は3行目の見出しで探す（見つからなければ上の位置を使う）。

取り出す指数（台帳の正式名。shisu/indicators.py）:
  安井_ランク, 安井_スピード, 安井_スタミナ, 安井_パワー, 安井_根性, 安井_賢さ, 安井_体力, 安井_やる気
  値が無く丸数字だけのセル（「⑯」）は値を空欄にする（偏差値・順位の規則は他の指数と同じ。
  1レースに欠損2頭以上ならそのレースはその指数で使わない）。
  丸数字の順位は yasui_extract.csv に 安井_スピード_Excel順位 などとして残すだけで、照合には使わない。
  過去側は TARGET に取り込んだ「値」から順位を出す（同値は同順位・値の無い馬は順位なし）が、Excel の丸数字は
  同値でも順位を分け、値の無い馬にも順位を付けるので、出馬表側で丸数字を使うと過去側と順位がずれる
  （4ファイルでステータスごとに約1,400〜4,000頭がずれた）。出馬表側も値から計算して両側をそろえる。

出力（shisu.ingest から使う。吉馬6指数と同じく8指数ごとのフォルダ）:
  yasui/yasui_{key}/{YYYYMMDD}_yasui_{key}.csv     TARGET 取込用（レースID(新), ワーク1 の2列・cp932）
  yasui/yasui_{key}/{元ファイル名}_{key}_merged.csv マージ完全版（ベースCSVの全列。ワーク1 にその指数）
      key = rank, speed, stamina, power, konjo, kashikosa, tairyoku, yaruki
  yasui/yasui_extract.csv                          取り出した全行（1頭1行。照合できた行は レースID(新) 付き）
  （出馬表用の yasui_{YYYYMMDD}.csv は廃止。TARGET 取込用CSVを ext_indices/yasui/yasui_{key}/ に置けば、
   過去側（replace）も出馬表側（calc_entry_hensachi）も ext_indices から結び付ける。/ingest は自動で置く）
  レースID(新)（18桁）は Excel に無い（回・日が分からない）ので、ベースCSVと 日付・場所・R・馬番 で照合して付ける。
  ベースCSVの指定が無い開催日×場所は、search_dirs（ingest では ingest_in/base/ と entries_in/）の CSV から
  その行を探して使う（読めない CSV は飛ばす）。ベースのある日でも行の無い場所の馬は
  yasui_{key}/{YYYYMMDD}_yasui_{key}_noid.csv に出す。どこにも無い開催日は、TARGET 取込用の代わりに
  yasui_{key}/{YYYYMMDD}_yasui_{key}.csv を「日付, 場所, 場所コード, R, 馬番, 馬名, ワーク1」で出し、
  ベースCSVも指定するよう案内する（このファイルは レースID(新) が無いので TARGET にはそのまま取り込めない）。
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from .csvio import norm_text, write_csv
from .ingest.common import (DATE, PLACE_CODES, RID, SOURCE_FILE, WORK, Logger, Outputs, default_log,
                            find_base_files, int_text, key14, lookup, read_base, series_of, write_outputs)

NAME = "yasui"
LABEL = "安井サロン Excel"
STATUS = ("スピード", "スタミナ", "パワー", "根性", "賢さ")
# (ファイル・フォルダ名のキー, Excel の見出し, 台帳の正式名)
INDICATORS = (
    ("rank", "ランク", "安井_ランク"),
    ("speed", "スピード", "安井_スピード"),
    ("stamina", "スタミナ", "安井_スタミナ"),
    ("power", "パワー", "安井_パワー"),
    ("konjo", "根性", "安井_根性"),
    ("kashikosa", "賢さ", "安井_賢さ"),
    ("tairyoku", "体力", "安井_体力"),
    ("yaruki", "やる気", "安井_やる気"),
)
# 見出しが見つからないときの列位置（0始まり。A列=0）
DEFAULT_POSITIONS = {"場": 1, "レース": 2, "条件": 3, "枠番": 4, "馬番": 5, "状態": 6, "展開": 7, "能力": 8,
                     "ランク": 9, "馬名": 11, "スピード": 12, "スタミナ": 14, "パワー": 16, "根性": 18, "賢さ": 20,
                     "体力": 22, "やる気": 23}
HEADER_ROW = 3          # 列名の行（1始まり）
# 丸数字の順位の列名の接尾辞。calc_entry_hensachi の「元の順位列」（{列}順位 / {列}_順位）に当たらない名前にする
EXCEL_RANK_SUFFIX = "_Excel順位"
FIRST_DATA_ROW = 4
_CIRCLED = re.compile(r"^(.*?)([①-⑳㉑-㉟㊱-㊿])\s*$")
_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_WEEKDAY = "月火水木金土日"


def split_status(cell) -> tuple[str, int | None]:
    """「48⑥」→ ('48', 6) / 「-17⑫」→ ('-17', 12) / 「⑯」→ ('', 16) / 48 → ('48', None) / 空 → ('', None)。

    丸数字は NFKC で数字に変わる（⑥→6）ので、分けてから値の部分だけを正規化する。
    """
    if cell is None:
        return "", None
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return ("" if cell != cell else _fmt_number(float(cell))), None
    s = str(cell).strip()
    rank = None
    m = _CIRCLED.match(s)
    if m:
        s, rank = m.group(1), int(unicodedata.numeric(m.group(2)))
    v = re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).replace("−", "-").replace("ー", "-")
    return (v if _NUMBER.match(v) else ""), rank


def _fmt_number(f: float) -> str:
    return str(int(f)) if f == int(f) else f"{f:g}"


def plain_number(cell) -> str:
    """ランク・体力・やる気（数のセル）。数でなければ ''。"""
    v, _ = split_status(cell)
    return v


def file_year_month(name: str) -> tuple[int | None, int | None]:
    """ファイル名（macOS の濁点分離にも対応）から (年, 月)。例: ステータス新バージョン2026_1月.xlsx → (2026, 1)。"""
    s = unicodedata.normalize("NFKC", Path(name).name)
    m = re.search(r"(20\d{2})\s*[_\-年]\s*(\d{1,2})\s*月", s) or re.search(r"(20\d{2})[_\-](\d{1,2})(?!\d)", s)
    if not m:
        return None, None
    y, mo = int(m.group(1)), int(m.group(2))
    return (y, mo) if 1 <= mo <= 12 else (y, None)


def sheet_date(sheet: str, year: int | None, month: int | None) -> tuple[str, str]:
    """シート名 MMDD → ('YYYYMMDD', 注記)。読めなければ ('', 理由)。

    年はファイル名の年。シートの月がファイルの月と半年以上離れていれば年をまたいだとみなす（2025_12月 の 0104 → 2026年）。
    シートの月がファイルの月と違い、その日が火〜金曜で、ファイルの月に読み替えると土・日・月曜になるときは
    シート名の打ち間違いとみなして読み替える（2025_11月 の 1029・1030 → 11/29（土）・11/30（日））。
    """
    s = unicodedata.normalize("NFKC", str(sheet)).strip()
    m = re.fullmatch(r"(\d{2})(\d{2})", s)
    if not m:
        return "", f"シート名 {sheet} は MMDD ではありません"
    mm, dd = int(m.group(1)), int(m.group(2))
    if year is None:
        return "", "ファイル名から年が読めません（ステータス新バージョンYYYY_M月.xlsx の形にしてください）"
    y = year
    if month is not None:
        if mm - month > 6:
            y -= 1
        elif month - mm > 6:
            y += 1
    try:
        d = _dt.date(y, mm, dd)
    except ValueError:
        return "", f"シート名 {sheet} は日付として正しくありません"
    if month is not None and mm != month and 1 <= d.weekday() <= 4:
        try:
            alt = _dt.date(year, month, dd)
        except ValueError:
            alt = None
        if alt is not None and alt.weekday() in (5, 6, 0):
            return alt.strftime("%Y%m%d"), (f"シート名 {sheet} は {d:%Y-%m-%d}（{_WEEKDAY[d.weekday()]}）なので、"
                                            f"ファイルの月の {alt:%Y-%m-%d}（{_WEEKDAY[alt.weekday()]}）と読み替えました")
    return d.strftime("%Y%m%d"), ""


def _column_positions(header: tuple) -> dict[str, int]:
    names = {}
    for i, v in enumerate(header or ()):
        t = norm_text(v)
        if t and t not in names:
            names[t] = i
    return {k: names.get(k, pos) for k, pos in DEFAULT_POSITIONS.items()}


@dataclass
class SheetInfo:
    file: str
    sheet: str
    date: str
    rows: int
    note: str = ""


def read_workbook(path: Path | str, log: Logger = default_log) -> tuple[pd.DataFrame, list[SheetInfo]]:
    """1ファイル → 1頭1行の DataFrame（列は extract_columns()）。"""
    import openpyxl
    path = Path(path)
    year, month = file_year_month(path.name)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    records, infos = [], []
    try:
        for ws in wb.worksheets:
            date, note = sheet_date(ws.title, year, month)
            if not date:
                log(f"[WARN] {path.name} / {ws.title}: {note}（スキップ）")
                infos.append(SheetInfo(path.name, ws.title, "", 0, note))
                continue
            if note:
                log(f"[WARN] {path.name} / {ws.title}: {note}")
            pos = _column_positions(())
            n = 0
            for r_i, row in enumerate(ws.iter_rows(values_only=True), 1):
                if r_i == HEADER_ROW:
                    pos = _column_positions(row)
                if r_i < FIRST_DATA_ROW or row is None:
                    continue
                rec = _record(row, pos, date)
                if rec is None:
                    continue
                rec["ファイル"], rec["シート"] = path.name, ws.title
                records.append(rec)
                n += 1
            infos.append(SheetInfo(path.name, ws.title, date, n, note))
    finally:
        wb.close()
    df = pd.DataFrame(records, columns=extract_columns())
    log(f"[INFO] {LABEL} {path.name}: {len(infos)} シート・{len(df)} 頭（{year}年{month}月）")
    return df, infos


def extract_columns() -> list[str]:
    cols = [RID, "日付", "場所", "場所コード", "R", "条件", "枠番", "馬番", "馬名"]
    for _key, label, name in INDICATORS:
        cols.append(name)
        if label in STATUS:
            cols.append(f"{name}{EXCEL_RANK_SUFFIX}")
    return cols + ["ファイル", "シート"]


def _cell(row: tuple, i: int):
    return row[i] if 0 <= i < len(row) else None


def _record(row: tuple, pos: dict[str, int], date: str) -> dict | None:
    place = norm_text(_cell(row, pos["場"]))
    race = int_text(_cell(row, pos["レース"]))
    uma = int_text(_cell(row, pos["馬番"]))
    if not place or not race or not uma:
        return None
    code = PLACE_CODES.get(place, "")
    rec = {RID: "", "日付": date, "場所": place, "場所コード": code, "R": race,
           "条件": str(_cell(row, pos["条件"]) or "").strip(), "枠番": int_text(_cell(row, pos["枠番"])),
           "馬番": uma, "馬名": norm_text(_cell(row, pos["馬名"]))}
    for _key, label, name in INDICATORS:
        cell = _cell(row, pos[label])
        if label in STATUS:
            v, rank = split_status(cell)
            rec[name], rec[f"{name}{EXCEL_RANK_SUFFIX}"] = v, ("" if rank is None else str(rank))
        else:
            rec[name] = plain_number(cell)
    return rec


def extract(paths: Iterable[Path | str], log: Logger = default_log) -> pd.DataFrame:
    frames = []
    for p in paths:
        df, _ = read_workbook(p, log)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=extract_columns())
    df = pd.concat(frames, ignore_index=True)
    df["_key"] = (df["日付"] + df["場所コード"] + df["R"].map(lambda r: f"{int(r):02d}")
                  + df["馬番"].map(lambda u: f"{int(u):02d}")).where(df["場所コード"] != "", "")
    dup = df["_key"].ne("") & df.duplicated("_key", keep="last")
    if dup.any():
        log(f"[WARN] {LABEL}: 同じ 日付・場所・R・馬番 の行が {int(dup.sum())} 行あります（後のファイル・シートを使います）")
        df = df[~dup]
    unknown = df["場所コード"] == ""
    if unknown.any():
        log(f"[WARN] {LABEL}: 場所コードの分からない場所 {sorted(set(df.loc[unknown, '場所']))}（照合しません）")
    return df.reset_index(drop=True)


NO_BASE_COLUMNS = ["日付", "場所", "場所コード", "R", "馬番", "馬名", WORK]


def run(paths: Iterable[Path | str], base: pd.DataFrame | None, out_dir: Path, log: Logger = default_log,
        search_dirs: Iterable[Path | str] = (), exclude_paths: Iterable[Path | str] = ()) -> Outputs:
    """取り出して、8指数ごとのフォルダに日付別CSV（とマージ完全版）を書く。

    base: 指定されたベースCSV（read_base の結果。無ければ None）
    search_dirs: base に無い開催日のベースCSVを探すフォルダ。exclude_paths は指定済みのベースCSV（二重に読まない）
    """
    out_dir = Path(out_dir)
    df = extract(paths, log)
    res = Outputs(total=len(df))
    if df.empty:
        log(f"[WARN] {LABEL}: 取り出せた行がありません")
        return res
    days = set(df["日付"])
    need = {k[:10] for k in df["_key"] if k}             # Excel にある 開催日×場所（YYYYMMDD + 場所コード）
    base = complete_base(base, need, search_dirs, exclude_paths, log)
    bkeys = base[RID].map(key14)
    if not base.empty:
        rid_of = dict(zip(bkeys[bkeys != ""], base.loc[bkeys != "", RID]))
        df[RID] = df["_key"].map(lambda k: rid_of.get(k, "")).astype(object)
        res.matched = int((df[RID] != "").sum())
        log(f"[INFO] {LABEL}: ベースCSVと照合できた馬 {res.matched}/{len(df)}")
        _check_consistency(df, base, bkeys, log)
    path = out_dir / "yasui_extract.csv"
    write_csv(df[extract_columns()], path)
    res.files.append(path)

    base_days = days & set(base.loc[base[RID].str.len() == 18, DATE]) if not base.empty else set()
    no_base_days = sorted(days - base_days)
    by_key = df.set_index("_key")
    values: dict[str, pd.Series] = {}
    for key, _label, name in INDICATORS:
        folder = out_dir / f"yasui_{key}"
        if base_days:
            mapping = {k: v for k, v in by_key[name].items() if k and v != ""}
            values[name] = lookup(bkeys, mapping)
            res.files += write_outputs(base, values[name], folder, f"yasui_{key}", log,
                                       merged_suffix=key, dates=base_days).files
        for day in no_base_days:
            res.files.append(_write_no_base(df[df["日付"] == day], name, folder / f"{day}_yasui_{key}.csv"))
    if no_base_days:
        msg = no_base_day_message(no_base_days, df)
        log(f"[WARN] {LABEL}: {msg}")
        res.notes.append(msg)
    unmatched = df[(df[RID] == "") & df["日付"].isin(base_days)]
    if len(unmatched):          # ベースCSVのある日でも、その場所の行がどこにも無い馬は {日付}_yasui_{key}_noid.csv に出す
        for key, _label, name in INDICATORS:
            for day, g in unmatched.groupby("日付", sort=True):
                res.files.append(_write_no_base(g, name, out_dir / f"yasui_{key}" / f"{day}_yasui_{key}_noid.csv"))
        msg = unmatched_horse_message(unmatched)
        log(f"[WARN] {LABEL}: {msg}")
        res.notes.append(msg)
    return res


def _day_text(day: str) -> str:
    """'20261101' → '11/01(日)'。"""
    try:
        d = _dt.date(int(day[:4]), int(day[4:6]), int(day[6:8]))
    except ValueError:
        return day
    return f"{d.month:02d}/{d.day:02d}({'月火水木金土日'[d.weekday()]})"


def no_base_day_message(days, df: pd.DataFrame) -> str:
    """ベースCSVに存在しない開催日の案内（非開催日・シート名の日付の打ち間違いなど）。"""
    days = sorted(days)
    heads = [f"{_day_text(d)} {int((df['日付'] == d).sum())}頭" for d in days[:6]]
    more = f" ほか {len(days) - 6}日" if len(days) > 6 else ""
    return (f"ベースCSVに存在しない開催日 {len(days)}日（{'、'.join(heads)}{more}）"
            "── 非開催日・シート名の日付の打ち間違いなどの可能性があります。"
            "この日は レースID(新) を付けられないので、日付・場所・R・馬番つきの指数別CSVだけを出しました（TARGET には取り込めません）。"
            "→ その日のベースCSV（または出馬表CSV）を一緒に指定するか、ingest_in/base/・entries_in/ に置いてやり直してください")


def unmatched_horse_message(unmatched: pd.DataFrame) -> str:
    """ベースCSVに該当行が無い馬の案内（別の月のシートの混入・少頭数・ベースCSVの収録期間外など）。"""
    groups = unmatched.groupby(["日付", "場所"], sort=True).size()
    heads = [f"{_day_text(d)} {p} {n}頭" for (d, p), n in groups.head(6).items()]
    more = f" ほか {len(groups) - 6}件" if len(groups) > 6 else ""
    return (f"ベースCSVに該当行が無い馬 {len(unmatched)}頭（{'、'.join(heads)}{more}）"
            "── 別の月のシートの混入・少頭数・ベースCSVの収録期間外などの可能性があります。"
            "この馬は TARGET 取込用CSVに入れず、{日付}_yasui_{key}_noid.csv に分けました")


BASE_COLUMNS = (RID, DATE, "枠番", "馬番", "馬名", "馬名S", WORK, SOURCE_FILE)


def complete_base(base: pd.DataFrame | None, need: set[str], search_dirs: Iterable[Path | str],
                  exclude_paths: Iterable[Path | str], log: Logger = default_log) -> pd.DataFrame:
    """指定されたベースCSVに無い「開催日×場所」を、search_dirs の CSV から補う。

    need: Excel にある YYYYMMDD + 場所コード（10桁）。ファイルの順に、まだ無い開催日×場所の行だけ使う。
    補ったファイルはベースCSVの列（BASE_COLUMNS）だけにする（出馬表CSVの何十列をマージ完全版に混ぜない）。
    読めないファイル（列数のそろわない手書きのCSV など）は警告して飛ばす。
    """
    if base is None or base.empty:
        base = pd.DataFrame(columns=[RID, DATE, WORK, SOURCE_FILE])
    rid = base[RID].astype(str)
    missing = set(need) - set(rid[rid.str.len() == 18].str.slice(0, 10))
    if not missing:
        return base
    parts, used = [], []
    taken = set(base[SOURCE_FILE]) if SOURCE_FILE in base.columns else set()
    for f in find_base_files(search_dirs, exclude_paths):
        if not missing:
            break
        warn: list[str] = []
        try:
            part = read_base([f], lambda m: warn.append(m) if "読めません" in m else None, taken=taken,
                             prefixes=missing, columns=BASE_COLUMNS)
        except Exception as e:  # 候補のファイル1本のせいで全体を止めない
            warn.append(f"{type(e).__name__}: {e}")
            part = pd.DataFrame()
        for w in warn:
            log(f"[WARN] {LABEL}: ベースCSVの候補 {f.parent.name}/{f.name} を読めないので使いません（{w}）")
        if part.empty:
            continue
        got = set(part[RID].str.slice(0, 10))
        parts.append(part)
        taken |= set(part[SOURCE_FILE])
        used.append(f"{f.parent.name}/{f.name}（{_fmt_days(sorted({g[:8] for g in got}))}）")
        missing -= got
    if not parts:
        return base
    log(f"[INFO] {LABEL}: ベースCSVの指定が無い開催日・場所を、次のファイルから補いました: {', '.join(used)}")
    return pd.concat([base] + parts, ignore_index=True)


def _fmt_days(days) -> str:
    days = sorted(days)
    return ", ".join(days) if len(days) <= 6 else f"{', '.join(days[:6])} ほか {len(days) - 6} 日"


def _write_no_base(g: pd.DataFrame, name: str, path: Path) -> Path:
    """ベースCSVの無い開催日の指数別CSV（レースID(新) の代わりに 日付・場所・R・馬番）。"""
    out = pd.DataFrame({"日付": g["日付"], "場所": g["場所"], "場所コード": g["場所コード"], "R": g["R"],
                        "馬番": g["馬番"], "馬名": g["馬名"], WORK: g[name]})
    order = out.assign(_r=pd.to_numeric(out["R"], errors="coerce"), _u=pd.to_numeric(out["馬番"], errors="coerce"))
    out = out.loc[order.sort_values(["場所コード", "場所", "_r", "_u"], kind="stable").index]
    write_csv(out[NO_BASE_COLUMNS], path)
    return path


def _check_consistency(df: pd.DataFrame, base: pd.DataFrame, bkey: pd.Series, log: Logger) -> None:
    """照合できた馬の枠番・馬名がベースCSVと違えば知らせる（シートの日付違いなどの検出用）。"""
    m = df[df[RID] != ""]
    if m.empty:
        return
    b = base.assign(_key=bkey).drop_duplicates("_key").set_index("_key")
    waku = series_of(b, "枠番").map(int_text)
    diff = m[(m["枠番"] != "") & (m["_key"].map(waku).fillna("") != "") & (m["_key"].map(waku) != m["枠番"])]
    if len(diff):
        log(f"[WARN] {LABEL}: 枠番がベースCSVと違う馬 {len(diff)} 頭（例: {diff.iloc[0]['日付']} "
            f"{diff.iloc[0]['場所']}{diff.iloc[0]['R']}R {diff.iloc[0]['馬番']}番）")
    for col in ("馬名", "馬名S"):
        if col in b.columns:
            names = b[col].map(norm_text)
            bad = m[(m["_key"].map(names).fillna("") != "") & (m["_key"].map(names) != m["馬名"])]
            if len(bad):
                log(f"[WARN] {LABEL}: 馬名がベースCSVの {col} と違う馬 {len(bad)} 頭（例: {bad.iloc[0]['馬名']} / "
                    f"{names.get(bad.iloc[0]['_key'], '')}）")
            break
