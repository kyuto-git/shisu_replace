"""集計キーに使う条件（22種）の導出。過去側（TARGET ★6タブの過去レースCSV）と出馬表側（TARGET 出馬表）で共通。

両側の列名の違いは standardize_history / standardize_entries で吸収し、同じ derive() を通す
（過去側と出馬表側でキーの部品が必ず同じ値になるようにするため）。

条件の列（derive の戻り値。値が分からない馬は ''。'' の部品があるキーは作らない）:
  時期          開催月: 12-2月 / 3-5月 / 6-9月 / 10-11月
  輸送距離      所属（美浦/栗東。地方・外国は空）× 場所: 110km以内 / 111-350 / 351-500 / 501-600 / 600km超 / 滞在
  間隔帯        TARGET の「間隔」（週数。連闘=1、2=中1週…）: 連闘 / 中2-3 / 中4-6 / 中7-9 / 中10-20 / 中21週以上
  叩き          休み明け〜戦目: 休み明け(1戦) / 叩き2戦 / 叩き3戦 / 叩き4戦 / 叩き5戦以上
  R値帯・直線帯・初角帯・CFS帯・F係数帯・今走クワドラント   コース属性マスタ（shisu/course.py）
  左右          左（東京・中京・新潟）/ 右
  左右遷移      前走 → 今走: 左→左 / 右→左 / 左→右 / 右→右（前走が JRA 以外なら空）
  距離帯        芝短・芝マイル・芝中・芝クラシック・芝長 / ダ短・ダマイル・ダ中・ダ長
  坂            坂あり（東京・中山・阪神・福島・中京）/ 坂なし
  前走クワドラント  前走のコースのクワドラント（前走の内外が分からない京都芝1400/1600・新潟芝2000 は、
                    前走レースID の内外を過去データから引く。引けなければ空）
  斤量帯        当日斤量（減量記号を除く）: 51以下 / 51.5-53 / 53.5-55 / 55.5-57 / 57.5-59 / 59.5以上
  斤量増減      今走 − 前走: 同じ / +0.5~+1.5 / +2kg以上 / -0.5~-1.5 / -2kg以下
  牝馬限定      牝馬限定 / 混合
  距離増減      今走 − 前走: 同距離 / 延長200 / 延長400 / 延長400超 / 短縮200 / 短縮400 / 短縮400超
  前走着順・前走人気・前走上り3F順   1以上の整数（帯はキーを作るときに s1/s2 で付ける。上り3F順の 0 は空）
  前走脚質      逃げ / 先行 / 中団 / 後方（出馬表の 逃/先/中/後 も同じに。マクリ・不明は空）
  前走R印3      HHH~HH / H / M / S / SS~SSS
"""
from __future__ import annotations

import re
import unicodedata
from typing import Mapping

import pandas as pd

from . import course

CONDITION_COLUMNS = ("時期", "輸送距離", "間隔帯", "叩き", "R値帯", "直線帯", "初角帯", "CFS帯", "F係数帯",
                     "今走クワドラント", "左右", "左右遷移", "距離帯", "坂", "前走クワドラント", "斤量帯", "斤量増減",
                     "牝馬限定", "距離増減", "前走着順", "前走人気", "前走上り3F順", "前走脚質", "前走R印3")
# 帯をキーのときに付ける条件（値は整数）と、その帯の名前の種類（hensachi.BAND_UNITS）
BANDED_CONDITIONS = {"前走着順": "finish", "前走人気": "pop", "前走上り3F順": "agari"}
STD_COLUMNS = ("日付", "場所", "芝・ダ", "距離_raw", "芝(内・外)", "所属", "間隔", "休明戦目", "斤量", "前走斤量",
               "牝馬限定", "前走場所", "前走芝ダ", "前走距離", "前走内外", "前走RID16", "前走着順", "前走人気",
               "前走上り3F順", "前走脚質", "前走R印3")

PLACE_CHAR = {"札": "01", "函": "02", "福": "03", "新": "04", "東": "05", "中": "06", "名": "07", "京": "08",
              "阪": "09", "小": "10"}
_WEIGHT_MARKS = re.compile(r"[◇△▲★☆*＊\s]")
_STYLE = {"逃げ": "逃げ", "逃": "逃げ", "先行": "先行", "先": "先行", "中団": "中団", "中": "中団", "後方": "後方", "後": "後方",
          "差し": "", "追込": ""}
_R3 = {"HHH": "HHH~HH", "HH": "HHH~HH", "H": "H", "M": "M", "S": "S", "SS": "SS~SSS", "SSS": "SS~SSS"}


def _n(v) -> str:
    s = unicodedata.normalize("NFKC", str(v if v is not None else ""))
    return re.sub(r"\s+", "", s)


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    for n in names:
        if n in df.columns:
            return df[n].astype(object).where(df[n].notna(), "").map(_n)
    return pd.Series("", index=df.index, dtype=object)


def _num(v) -> float | None:
    s = _WEIGHT_MARKS.sub("", _n(v)).replace("着", "").replace("kg", "")
    try:
        return float(s)
    except ValueError:
        return None


def _int_text(v) -> str:
    f = _num(v)
    return str(int(f)) if f is not None and f == int(f) and f >= 1 else ""


def _float_text(v) -> str:
    f = _num(v)
    return "" if f is None else f"{f:g}"


def _stable(v) -> str:
    s = _n(v).strip("()[]（）［］")
    if s in ("美", "美浦"):
        return "美浦"
    if s in ("栗", "栗東"):
        return "栗東"
    return ""


def _interval_weeks(v) -> str:
    s = _n(v)
    if s == "連闘":
        return "1"
    return _int_text(s)


_KAISAI_RE = re.compile(r"^(\d{6})\s*(\d{1,2})(\D)(\d{1,2})(?:\s*(\d{1,2})R|\s+(\d{1,2}))$")


def _prev_rid16_from_kaisai(v) -> str:
    """出馬表の「前走開催」（例 '260816 2新8  1R'）→ 前走のレースID(新)先頭16桁（地方・海外などは ''）。
    TARGET の欄は固定幅なので、日が2桁だと末尾の R が切れる（'260712 1函10 11'）。R が無くても読む。"""
    m = _KAISAI_RE.match(unicodedata.normalize("NFKC", str(v or "")).strip())
    if not m:
        return ""
    yymmdd, kai, pc, day, r1, r2 = m.groups()
    r = r1 or r2
    place = PLACE_CHAR.get(pc)
    if not place:
        return ""
    return f"20{yymmdd}{place}{int(kai):02d}{int(day):02d}{int(r):02d}"


def _mare_flags(raw: pd.DataFrame, norm: pd.DataFrame, sex_cols: tuple[str, ...]) -> pd.Series:
    """牝馬限定 / 混合（両側共通の規則）。性別限定 の列があればそれ（牝 → 牝馬限定）。無ければ、レース名に「牝」がある、
    またはそのレースの全馬の性が牝なら 牝馬限定。どれも分からなければ ''。"""
    if "性別限定" in raw.columns:
        return _col(raw, "性別限定").map(lambda s: "牝馬限定" if s == "牝" else "混合")
    name = _col(raw, "レース名")
    sex = _col(raw, *sex_cols)
    rid = norm["レースID(新/馬番無)"] if "レースID(新/馬番無)" in norm.columns else norm["日付"]
    all_mare = (sex == "牝").groupby(rid).transform("all") & (sex != "").groupby(rid).transform("all")
    known = (name != "") | (sex != "")
    return pd.Series(["牝馬限定" if ("牝" in n or a) else ("混合" if k else "") for n, a, k in zip(name, all_mare, known)],
                     index=raw.index, dtype=object)


# ---------------------------------------------------------------------------
# 列名をそろえる
# ---------------------------------------------------------------------------
def standardize_history(raw: pd.DataFrame, norm: pd.DataFrame) -> pd.DataFrame:
    """過去レースCSV（★6タブ）→ STD_COLUMNS。norm は replace_csv_ver3.normalize_history の結果（場所・芝・ダ など）。"""
    out = pd.DataFrame(index=raw.index)
    out["日付"] = norm["日付"]
    for c in ("場所", "芝・ダ", "距離_raw", "芝(内・外)"):
        out[c] = norm[c]
    out["所属"] = _col(raw, "所属").map(_stable)
    out["間隔"] = _col(raw, "間隔").map(_interval_weeks)
    out["休明戦目"] = _col(raw, "休み明け～戦目", "休み明け〜戦目", "休明戦目").map(_int_text)
    out["斤量"] = _col(raw, "斤量").map(_float_text)
    out["前走斤量"] = _col(raw, "前走斤量", "前斤量").map(_float_text)
    out["牝馬限定"] = _mare_flags(raw, norm, ("性別", "性"))
    out["前走場所"] = _col(raw, "前走場所", "前場所")
    out["前走芝ダ"] = _col(raw, "前芝・ダ", "前芝ダ")
    out["前走距離"] = _col(raw, "前距離").map(_int_text)
    out["前走内外"] = _col(raw, "前走芝(内・外)", "前芝(内・外)")
    out["前走RID16"] = _col(raw, "前走レースID(新)").map(lambda s: s[:16] if re.fullmatch(r"\d{16,18}", s) else "")
    out["前走着順"] = _col(raw, "前走着順").map(_int_text)
    out["前走人気"] = _col(raw, "前走人気").map(_int_text)
    out["前走上り3F順"] = _col(raw, "前走上り3F順", "前走上がり3F順").map(_int_text)
    out["前走脚質"] = _col(raw, "前走脚質")
    out["前走R印3"] = _col(raw, "前走レース印３", "前走レース印3")
    return out[list(STD_COLUMNS)]


def standardize_entries(raw: pd.DataFrame, norm: pd.DataFrame) -> pd.DataFrame:
    """TARGET 出馬表CSV → STD_COLUMNS。norm は build_db.normalize_entries_df の結果。"""
    out = pd.DataFrame(index=raw.index)
    out["日付"] = norm["日付"]
    for c in ("場所", "芝・ダ", "距離_raw", "芝(内・外)"):
        out[c] = norm[c]
    stable = _col(raw, "所属").map(_stable)
    if "所属.1" in raw.columns:                 # 出馬表は「所属」が2列（(美) と 美浦）
        stable = stable.where(stable != "", _col(raw, "所属.1").map(_stable))
    out["所属"] = stable
    out["間隔"] = _col(raw, "間隔").map(_interval_weeks)
    out["休明戦目"] = _col(raw, "休明戦目", "休み明け～戦目").map(_int_text)
    out["斤量"] = _col(raw, "斤量").map(_float_text)
    prev_w = _col(raw, "前斤量", "前走斤量").map(_float_text)
    if "斤量差" in raw.columns:                 # 前走の斤量が無く 斤量差 だけあるとき
        diff = _col(raw, "斤量差").map(_num)
        cur = out["斤量"].map(_num)
        alt = pd.Series([("" if c is None or d is None else f"{c - d:g}") for c, d in zip(cur, diff)], index=raw.index)
        prev_w = prev_w.where(prev_w != "", alt)
    out["前走斤量"] = prev_w
    out["牝馬限定"] = _mare_flags(raw, norm, ("性", "性別"))
    out["前走場所"] = _col(raw, "前場所", "前走場所")
    out["前走芝ダ"] = _col(raw, "前芝ダ", "前芝・ダ")
    out["前走距離"] = _col(raw, "前距離").map(_int_text)
    out["前走内外"] = _col(raw, "前芝(内・外)", "前走芝(内・外)")
    rid16 = _col(raw, "前走レースID(新)").map(lambda s: s[:16] if re.fullmatch(r"\d{16,18}", s) else "")
    if "前走開催" in raw.columns:
        rid16 = rid16.where(rid16 != "", raw["前走開催"].map(_prev_rid16_from_kaisai))
    out["前走RID16"] = rid16
    out["前走着順"] = _col(raw, "前着順", "前確定着順", "前走着順").map(_int_text)
    out["前走人気"] = _col(raw, "前人気", "前走人気").map(_int_text)
    out["前走上り3F順"] = _col(raw, "前3F順", "前走上り3F順").map(_int_text)
    out["前走脚質"] = _col(raw, "前脚質", "前走脚質")
    out["前走R印3"] = _col(raw, "前レース印3", "前走レース印３", "前走レース印3")
    return out[list(STD_COLUMNS)]


# ---------------------------------------------------------------------------
# 導出（両側共通）
# ---------------------------------------------------------------------------
def season(date: str) -> str:
    m = re.fullmatch(r"\d{4}(\d{2})\d{2}", date or "")
    if not m:
        return ""
    mo = int(m.group(1))
    if mo in (12, 1, 2):
        return "12-2月"
    if 3 <= mo <= 5:
        return "3-5月"
    if 6 <= mo <= 9:
        return "6-9月"
    return "10-11月" if mo in (10, 11) else ""


def interval_band(weeks: str) -> str:
    if not weeks:
        return ""
    w = int(weeks)
    if w <= 1:
        return "連闘"
    for hi, label in ((3, "中2-3"), (6, "中4-6"), (9, "中7-9"), (20, "中10-20")):
        if w <= hi:
            return label
    return "中21週以上"


def tataki_band(n: str) -> str:
    if not n:
        return ""
    k = int(n)
    if k == 1:
        return "休み明け(1戦)"
    return f"叩き{k}戦" if k <= 4 else "叩き5戦以上"


def weight_band(w: str) -> str:
    f = _num(w)
    if f is None or f <= 0:
        return ""
    for hi, label in ((51, "51以下"), (53, "51.5-53"), (55, "53.5-55"), (57, "55.5-57"), (59, "57.5-59")):
        if f <= hi + 1e-9:
            return label
    return "59.5以上"


def weight_change(cur: str, prev: str) -> str:
    c, p = _num(cur), _num(prev)
    if c is None or p is None or c <= 0 or p <= 0:
        return ""
    d = round(c - p, 1)
    if abs(d) < 0.25:
        return "同じ"
    if d > 0:
        return "+2kg以上" if d >= 2 - 1e-9 else "+0.5~+1.5"
    return "-2kg以下" if d <= -2 + 1e-9 else "-0.5~-1.5"


def distance_change(cur: str, prev: str) -> str:
    c, p = _num(cur), _num(prev)
    if c is None or p is None or c <= 0 or p <= 0:
        return ""
    d = int(round(c - p))
    if d == 0:
        return "同距離"
    word, a = ("延長", d) if d > 0 else ("短縮", -d)
    if a <= 200:
        return f"{word}200"
    if a <= 400:
        return f"{word}400"
    return f"{word}400超"


def derive(std: pd.DataFrame, inner_lookup: Mapping[str, str] | None = None) -> pd.DataFrame:
    """STD_COLUMNS の DataFrame → CONDITION_COLUMNS の DataFrame（index は同じ。値は文字列、分からなければ ''）。

    inner_lookup: 前走のレースID(新)先頭16桁 → 芝(内・外)。内外の分からない前走コースを引くときに使う。
    """
    out = pd.DataFrame(index=std.index)
    out["時期"] = std["日付"].map(season)
    out["輸送距離"] = [course.transport_band(s, p) for s, p in zip(std["所属"], std["場所"])]
    out["間隔帯"] = std["間隔"].map(interval_band)
    out["叩き"] = std["休明戦目"].map(tataki_band)
    attrs = course.resolve_courses(std["場所"], std["芝・ダ"], std["距離_raw"], std["芝(内・外)"])
    for c in ("R値帯", "直線帯", "初角帯", "CFS帯", "F係数帯", "今走クワドラント"):
        out[c] = attrs[c]
    lr = std["場所"].map(course.turn_direction)
    prev_lr = std["前走場所"].map(course.turn_direction)
    out["左右"] = lr
    out["左右遷移"] = [f"{a}→{b}" if a and b else "" for a, b in zip(prev_lr, lr)]
    out["距離帯"] = [course.distance_band(s, d) for s, d in zip(std["芝・ダ"], std["距離_raw"])]
    out["坂"] = std["場所"].map(course.slope)
    prev_inner = std["前走内外"]
    if inner_lookup:
        amb = course.ambiguous_courses()
        need = [((p, s, d) in amb and not i) for p, s, d, i in
                zip(std["前走場所"], std["前走芝ダ"], std["前走距離"], prev_inner)]
        prev_inner = pd.Series([inner_lookup.get(r, "") if n else i for n, r, i in
                                zip(need, std["前走RID16"], prev_inner)], index=std.index)
    prev_attrs = course.resolve_courses(std["前走場所"], std["前走芝ダ"], std["前走距離"], prev_inner)
    out["前走クワドラント"] = prev_attrs["今走クワドラント"]
    out["斤量帯"] = std["斤量"].map(weight_band)
    out["斤量増減"] = [weight_change(c, p) for c, p in zip(std["斤量"], std["前走斤量"])]
    out["牝馬限定"] = std["牝馬限定"]
    out["距離増減"] = [distance_change(c, p) for c, p in zip(std["距離_raw"], std["前走距離"])]
    out["前走着順"] = std["前走着順"]
    out["前走人気"] = std["前走人気"]
    out["前走上り3F順"] = std["前走上り3F順"].map(lambda s: s if s and int(s) >= 1 else "")
    out["前走脚質"] = std["前走脚質"].map(lambda s: _STYLE.get(s, ""))
    out["前走R印3"] = std["前走R印3"].map(lambda s: _R3.get(s, ""))
    return out[list(CONDITION_COLUMNS)].astype(object)


def inner_lookup_from(rid: pd.Series, inner: pd.Series) -> dict[str, str]:
    """過去データの (レースID(新), 芝(内・外)) → {先頭16桁: 内外}（前走の内外を引くため）。"""
    out = {}
    for r, i in zip(rid.astype(str), inner.astype(str)):
        if len(r) >= 16 and i:
            out[r[:16]] = i
    return out


INNER_FILE = "__inner_outer__.csv.gz"


def load_inner_lookup(master_dir=None) -> dict[str, str]:
    """replace_csv_ver3.py が master_csv/ に書いた前走の内外の表（レースID16 → 芝(内・外)）。無ければ {}。"""
    from pathlib import Path
    from . import config
    from .csvio import read_csv_any
    p = Path(master_dir or config.master_dir()) / INNER_FILE
    if not p.exists():
        return {}
    df = read_csv_any(p)
    if "レースID16" not in df.columns or "芝(内・外)" not in df.columns:
        return {}
    return dict(zip(df["レースID16"], df["芝(内・外)"]))
