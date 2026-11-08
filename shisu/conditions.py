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

import math
import re
import unicodedata
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from . import course
from .finish import parse_finish
from .normalize import horse_race_id, split_surface_distance

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
    """数（斤量・着順など）。読めない・NaN・無限大は None。"""
    s = _WEIGHT_MARKS.sub("", _n(v)).replace("着", "").replace("kg", "")
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


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
# 前走の列を同じCSVの履歴から補う（TARGET で「前走データ読込」をせずに出した ★6 の過去CSV）
# ---------------------------------------------------------------------------
HORSE_ID = "血統登録番号"
# 補う列（★6 の見出し。別名があればそれを使う）← 1つ前の走りの列
PREV_SOURCES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("前走場所",), ("場所",)),
    (("前芝・ダ", "前芝ダ"), ("芝・ダ",)),          # 距離「ダ1200」の形なら分けて使う
    (("前距離",), ("距離",)),
    (("前走斤量",), ("斤量",)),
    (("前走人気",), ("人気",)),
    (("前走着順",), ("着順",)),
    (("前走脚質",), ("脚質",)),
    (("前走上り3F順", "前走上がり3F順"), ("上り3F順", "上がり3F順")),
    (("前走レース印３", "前走レース印3"), ("レース印３", "レース印3")),
    (("前走レースID(新)",), ("レースID(新)",)),
)
INTERVAL_NAMES = ("間隔",)
REST_NAMES = ("休み明け～戦目", "休み明け〜戦目", "休明戦目")
# 休み明け1戦目にする 間隔（週）の下限。TARGET の ★6 出力（2026年10月・2,462頭）で 休み明け～戦目=1 は 間隔 8 以上
# （中7週以上）と 2,460頭で一致した（違う2頭は前走が地方競馬）。70日（10週）で区切ると 359頭が TARGET と食い違う
REST_BREAK_INTERVAL = 8
# 間隔 = 木曜始まりの週の番号の差（連闘=1、中1週=2…）。同じ出力の 2,920頭すべてで TARGET の 間隔 と一致した
# （日数 // 7 では 13日 → 1 になるなど 792頭が食い違う）
_WEEK_ORIGIN = pd.Timestamp("2000-01-06")       # 木曜日
# 履歴から補った行の印（"1"）。TARGET が読み込んだ値と区別し、次に補うとき（raw_csv の統合で古い履歴が増えたときなど）
# 作り直せるようにする。作り直せなければ前の値を残す
PREV_FILLED_MARK = "前走補完"


def _first_col(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    return next((n for n in names if n in df.columns), None)


def _blank(df: pd.DataFrame, col: str | None) -> pd.Series:
    if col is None:
        return pd.Series(True, index=df.index)
    return df[col].astype(object).where(df[col].notna(), "").map(_n) == ""


def previous_columns(df: pd.DataFrame) -> list[str]:
    """df にある前走の12列（前走場所〜前走レースID(新)・間隔・休み明け～戦目）の実際の列名。"""
    names = [n for n, _src in PREV_SOURCES] + [INTERVAL_NAMES, REST_NAMES]
    return [c for c in (_first_col(df, n) for n in names) if c]


def previous_loaded(df: pd.DataFrame) -> pd.Series:
    """TARGET が前走を読み込んだ行（前走の12列のどれかに値があり、履歴から補った印の無い行）。"""
    any_value = pd.Series(False, index=df.index)
    for c in previous_columns(df):
        any_value |= ~_blank(df, c)
    if PREV_FILLED_MARK in df.columns:
        any_value &= _col(df, PREV_FILLED_MARK) != "1"
    return any_value


def _week_no(d: pd.Series) -> pd.Series:
    return (d - _WEEK_ORIGIN).dt.days // 7


def fill_previous_runs(raw: pd.DataFrame, log: Callable[[str], None] | None = None) -> pd.DataFrame:
    """前走の列（前走場所・前芝・ダ・前距離・前走斤量・前走人気・前走着順・前走脚質・前走上り3F順・前走レース印３・
    前走レースID(新)）と 間隔・休み明け～戦目 が全部空の行（TARGET で前走を読み込まずに出した行）を、同じCSVの
    血統登録番号 ごとの履歴（日付・レースID(新) の順）の1つ前の走りから補う。戻り値は補ったコピー（attrs["prev_filled"] に件数）。

    - TARGET が前走を読み込んだ行（どれか1列でも値がある行）は触らない。空欄にも意味がある（前走が地方なら 前走人気 が空、
      デビューから休んでいなければ 休み明け～戦目 が空）ので、読み込んだ行の空欄は埋めない
    - 取消・除外（着順 取消・除外・消・外）は走っていないので「1つ前の走り」にしない・休み明けの数に入れない
      （失格・中止は走ったものとして数える）
    - 間隔 = 木曜始まりの週の番号の差（最小1＝連闘）。休み明け～戦目 = 間隔が REST_BREAK_INTERVAL 以上なら 1、それ以外は
      1つ前の走りの値 + 1（1つ前が空＝デビューから休んでいない・分からないなら空のまま。TARGET と同じ）
    - キャリア（出走数）の列があれば、今走 − 1つ前の走り が 1（間にCSVの取消・除外があればその数まで多くてよい。TARGET の
      キャリアは除外のレースも数える）のときだけ補う。それより大きければCSVに無いレース（9頭以下・障害・地方・海外など）を
      はさんでいるので、CSVの中の1つ前は本当の前走ではない。キャリア 0（デビュー）は補わない
    - 補った行には 前走補完=1 の印を付ける。印のある行は次に補うときに作り直す（作り直せなければ前の値を残す）
    - 血統登録番号・レースID(新) の列が無ければ何もしない
    """
    stats = {"blank": 0, "filled": 0, "no_history": 0, "gap": 0}
    out = raw
    if HORSE_ID not in raw.columns or "レースID(新)" not in raw.columns or raw.empty:
        out = raw.copy()
        out.attrs["prev_filled"] = stats
        return out
    targets = {names[0]: _first_col(raw, names) for names, _src in PREV_SOURCES}
    iv_col, rest_col = _first_col(raw, INTERVAL_NAMES), _first_col(raw, REST_NAMES)
    unloaded = _blank(raw, iv_col) & _blank(raw, rest_col)
    for col in targets.values():
        unloaded &= _blank(raw, col)
    marked = (_col(raw, PREV_FILLED_MARK) == "1") if PREV_FILLED_MARK in raw.columns else \
        pd.Series(False, index=raw.index)
    unloaded |= marked                               # 前に履歴から補った行は作り直す
    hid = raw[HORSE_ID].astype(object).where(raw[HORSE_ID].notna(), "").map(_n)
    uma = raw["馬番"] if "馬番" in raw.columns else pd.Series("", index=raw.index)
    rid = horse_race_id(raw["レースID(新)"], uma).astype(object)
    day = rid.str.slice(0, 8)
    if "日付S" in raw.columns:
        alt = raw["日付S"].astype(object).where(raw["日付S"].notna(), "").map(_n).str.replace(r"\D", "", regex=True)
        day = day.where(day.str.fullmatch(r"(19|20)\d{6}").fillna(False), alt.str.slice(0, 8))
    date = pd.to_datetime(day, format="%Y%m%d", errors="coerce")
    ok = (hid != "") & date.notna()
    need = unloaded & ok
    stats["blank"] = int(need.sum())
    if not need.any():
        out = raw.copy()
        out.attrs["prev_filled"] = stats
        return out
    ran = parse_finish(raw["着順"])["denom"] if "着順" in raw.columns else pd.Series(True, index=raw.index)
    career = pd.to_numeric(_col(raw, "キャリア"), errors="coerce") if "キャリア" in raw.columns else \
        pd.Series(np.nan, index=raw.index)
    need_arr = need.to_numpy()
    scope = (ok & hid.isin(set(hid[need]))).to_numpy()      # 補う行のある馬だけ見る（index が重なっていても位置で扱う）
    frame = pd.DataFrame({"hid": hid.to_numpy()[scope], "date": date.to_numpy()[scope],
                          "pos": np.arange(len(raw))[scope], "ran": ran.to_numpy().astype(bool)[scope]})
    runs = frame[frame["ran"]].drop_duplicates(subset=["hid", "date"], keep="last")
    left = frame[need_arr[frame["pos"].to_numpy()]].sort_values("date", kind="stable")
    hit = pd.merge_asof(left, runs[["hid", "date", "pos"]].rename(columns={"pos": "prev_pos", "date": "prev_date"})
                        .assign(date=lambda x: x["prev_date"]).sort_values("date", kind="stable"),
                        on="date", by="hid", allow_exact_matches=False, direction="backward")
    hit = hit.set_index("pos")
    prev_pos = hit["prev_pos"]
    # 1つ前の走りの値
    sd_col = _first_col(raw, ("芝・ダ", "芝ダ"))
    surface, dist = split_surface_distance(raw[sd_col] if sd_col else pd.Series("", index=raw.index),
                                           raw["距離"] if "距離" in raw.columns else pd.Series("", index=raw.index))
    dist = dist.map(lambda v: (re.match(r"\d+", _n(v)) or [""])[0])
    src_vals: dict[str, np.ndarray] = {}
    for names, sources in PREV_SOURCES:
        name = names[0]
        if name == "前芝・ダ":
            vals = surface
        elif name == "前距離":
            vals = dist
        elif name == "前走レースID(新)":
            vals = rid.where(rid.str.fullmatch(r"\d{18}").fillna(False), "")
        else:
            sc = _first_col(raw, sources)
            vals = raw[sc].astype(object).where(raw[sc].notna(), "") if sc else pd.Series("", index=raw.index)
        src_vals[name] = vals.astype(object).to_numpy()
    week = _week_no(date).to_numpy()
    car = career.to_numpy()
    dates = date.to_numpy()
    scratched: dict[str, np.ndarray] = {     # 馬 → 取消・除外の日付（キャリアの差を数えるときに足す）
        h: np.sort(g.to_numpy()) for h, g in frame.loc[~frame["ran"], "date"].groupby(frame.loc[~frame["ran"], "hid"])}
    hid_arr = hid.to_numpy()
    rest_given = (_col(raw, rest_col) if rest_col else pd.Series("", index=raw.index)).map(_int_text).to_numpy()
    marked_arr = marked.to_numpy()
    fill: dict[int, dict[str, str]] = {}
    prev_of: dict[int, int] = {}
    for p_cur, p_prev in prev_pos.items():
        if p_prev != p_prev:                        # NaN: CSVに前の走りが無い
            stats["no_history"] += 1
            continue
        p_prev = int(p_prev)
        c_cur, c_prev = car[p_cur], car[p_prev]
        if c_cur == c_cur and c_cur == 0:
            stats["no_history"] += 1
            continue
        if c_cur == c_cur and c_prev == c_prev:
            sc = scratched.get(hid_arr[p_cur])
            n_scr = 0 if sc is None else int(np.searchsorted(sc, dates[p_cur], "left")
                                              - np.searchsorted(sc, dates[p_prev], "right"))
            if not 1 <= c_cur - c_prev <= 1 + n_scr:
                stats["gap"] += 1
                continue
        row = {name: str(v[p_prev]) for name, v in src_vals.items()}
        row["間隔"] = str(max(1, int(week[p_cur] - week[p_prev])))
        fill[p_cur] = row
        prev_of[p_cur] = p_prev
    # 休み明け～戦目: 馬ごとに日付の順に（1つ前の走りの値が先に決まっているように）
    rest_of: dict[int, str] = {}
    order = frame.sort_values(["hid", "date"], kind="stable")["pos"].to_numpy()
    for p in order:
        if not need_arr[p]:
            rest_of[p] = rest_given[p]              # TARGET の値（空ならデビューから休んでいない・分からない）
            continue
        row = fill.get(p)
        if row is None:
            rest_of[p] = rest_given[p] if marked_arr[p] else ""     # 作り直せない印の行は前の値のまま
            continue
        if int(row["間隔"]) >= REST_BREAK_INTERVAL:
            r = "1"
        else:
            before = rest_of.get(prev_of[p], "")
            r = str(int(before) + 1) if before else ""
        rest_of[p] = r
        row["休み明け～戦目"] = r
    stats["filled"] = len(fill)
    out = raw.copy()
    if fill:
        positions = np.fromiter(fill.keys(), dtype=np.int64, count=len(fill))
        cols = [(targets[names[0]] or names[0], names[0]) for names, _src in PREV_SOURCES]
        cols += [(iv_col or INTERVAL_NAMES[0], "間隔"), (rest_col or REST_NAMES[0], "休み明け～戦目")]
        for col, key in cols:
            vals = (out[col].astype(object).where(out[col].notna(), "") if col in out.columns
                    else pd.Series("", index=out.index, dtype=object)).to_numpy(copy=True)
            vals[positions] = [fill[int(q)].get(key, "") for q in positions]
            out[col] = vals
        mark = (out[PREV_FILLED_MARK].astype(object).where(out[PREV_FILLED_MARK].notna(), "")
                if PREV_FILLED_MARK in out.columns else pd.Series("", index=out.index, dtype=object)).to_numpy(copy=True)
        mark[positions] = "1"
        out[PREV_FILLED_MARK] = mark
    out.attrs["prev_filled"] = stats
    if log and stats["blank"]:
        log(f"[INFO] 前走の列が空の行 {stats['blank']} 行: 血統登録番号 の履歴から {stats['filled']} 行を補いました"
            f"（CSVに前の走りが無い・デビュー {stats['no_history']} 行、キャリアが続かない（CSVに無いレースをはさむ）"
            f"{stats['gap']} 行は空のまま）")
    return out


# ---------------------------------------------------------------------------
# 列名をそろえる
# ---------------------------------------------------------------------------
def standardize_history(raw: pd.DataFrame, norm: pd.DataFrame, fill_previous: bool = True) -> pd.DataFrame:
    """過去レースCSV（★6タブ）→ STD_COLUMNS。norm は replace_csv_ver3.normalize_history の結果（場所・芝・ダ など）。
    前走の列が空の行は fill_previous_runs で同じCSVの履歴から補う（読み込み時に補ってあれば（attrs["prev_filled"]）しない）。"""
    if fill_previous and "prev_filled" not in raw.attrs:
        raw = fill_previous_runs(raw)
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
        diff = [_num(v) for v in _col(raw, "斤量差")]      # map だと None が NaN になるので要素ごとに
        cur = [_num(v) for v in out["斤量"]]
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
