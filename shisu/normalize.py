"""結合データの部品を作る正規化ルール（過去側・出馬表側で共通）。

ここにある関数は、過去レース CSV（replace_csv_ver3.py）と出馬表（build_db.py）の
両方から呼ばれる。片側だけで値を加工すると結合データが一致しなくなるため、
キーに入る値の加工は必ずここで行う。入力は csvio.norm_text 済みの文字列を想定。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .csvio import norm_series, norm_text

# ---------------------------------------------------------------------------
# クラス名
# ---------------------------------------------------------------------------
# NFKC 後の表記で判定する（ｵｰﾌﾟﾝ→オープン、Ｇ１→G1、ＧⅡ→GII など）
CLASS_GROUPS = {
    "1・2_勝": ("500万", "1000万", "1勝", "2勝", "1勝クラス", "2勝クラス"),
    "準OP・OP": ("1600万", "3勝", "3勝クラス", "オープン", "OP", "OP(L)", "L", "(L)", "リステッド"),
    "重賞": ("G2", "G3", "GII", "GIII", "重賞"),
    "G1": ("G1", "GI"),
}
_CLASS_MAP = {raw: group for group, raws in CLASS_GROUPS.items() for raw in raws}
AGE_CLASS = "1勝・OP"  # 馬齢 ×（1・2_勝 | 準OP・OP）の統合先


def norm_class(raw) -> str:
    t = norm_text(raw)
    return _CLASS_MAP.get(t, t)


def class_series(raw: pd.Series) -> pd.Series:
    t = norm_series(raw)
    return t.map(lambda v: _CLASS_MAP.get(v, v)).astype(object)


def apply_age_class_rule(class_s: pd.Series, weight_s: pd.Series) -> pd.Series:
    """馬齢戦の 1・2_勝 / 準OP・OP を 1勝・OP にまとめる（旧仕様踏襲）。"""
    mask = (weight_s == "馬齢") & class_s.isin(["1・2_勝", "準OP・OP"])
    out = class_s.copy()
    out[mask] = AGE_CLASS
    return out


# ---------------------------------------------------------------------------
# 重量種別（キーでは 馬齢 / 定量 / ハンデ の3値。別定は定量に含める）
# ---------------------------------------------------------------------------
_WEIGHT_MAP = {"別定": "定量", "ハンデキャップ": "ハンデ"}


def norm_weight(raw) -> str:
    t = norm_text(raw)
    return _WEIGHT_MAP.get(t, t)


def weight_series(raw: pd.Series) -> pd.Series:
    return norm_series(raw).map(lambda v: _WEIGHT_MAP.get(v, v)).astype(object)


# レース名のハンデ記号。TARGET は「むらさＨ･3勝」「目黒記念ＨG2」「安土城Ｈ(L)」「北陸ＳＨ･3勝」のように
# 全角Ｈを付ける（ＳＨ・ＣＨ・ＴＨ は ステークス・カップ・トロフィー の略＋ハンデ）。NFKC 後に H となるので、
# 前が英字でない（または S・C・T の直後）で、後ろが 末尾/・/(/G/J/L/数字 の H を拾う。
# 「NHKマイルC」「HTB賞」のような英字列中の H は拾わない（旧版は H を含むだけでハンデ扱いだった）。
_HANDICAP_RE = re.compile(r"(?:(?<![A-Za-z])|(?<=[SCT]))H(?=$|[・(GJL0-9])")


def infer_entry_weight(race_name, age_limit, class_norm: str) -> str:
    """出馬表に重量種別の列が無いときの推定（過去側は TARGET の 重量種別 列。別定は定量にまとめる）。

    - レース名にハンデ記号 → ハンデ
    - 年限定に「上」（3歳以上・4歳以上）→ 定量（定量・別定）
    - 2歳・3歳限定のオープン・リステッド（クラス 準OP・OP）→ 定量（TARGET では 別定。アイビーＳ(L)・萩Ｓ(L)・もみじＳ）
    - それ以外の 2歳・3歳限定（新馬・未勝利・1勝・重賞・G1）→ 馬齢（菊花賞・秋華賞なども TARGET は 馬齢）
    - 年限定が空 → 判定不能として ""（重量種別を含むキーは作らない）
    2・3歳限定の別定重賞などは過去側（定量）とずれる。正確にするには TARGET の出馬表出力に
    重量種別の列を追加する（列があればそちらを優先する）。
    """
    if _HANDICAP_RE.search(norm_text(race_name)):
        return "ハンデ"
    age = norm_text(age_limit)
    if not age:
        return ""
    if "上" in age:
        return "定量"
    if class_norm == "準OP・OP":
        return "定量"
    return "馬齢"


# ---------------------------------------------------------------------------
# 距離・距離レンジ
# ---------------------------------------------------------------------------
def distance_series(raw: pd.Series) -> pd.Series:
    """'1600m' → '1600'。数値の '1600.0' も '1600' にする。"""
    t = norm_series(raw).str.replace(r"m$", "", regex=True)
    return t.map(_int_like).astype(object)


def _int_like(v: str) -> str:
    if re.fullmatch(r"\d+\.0+", v):
        return v.split(".")[0]
    return v


# (場所, 芝・ダ, 芝(内・外) の条件 or None, 対象距離, 置換後距離, 置換後の内外 or None)
# 札幌芝・中京芝のまとめは旧版でコメントアウトされていたため入れていない。
DISTANCE_RULES = (
    ("福島", "芝", None, ("1700", "1800"), "17-18", None),
    ("新潟", "芝", "内", ("1200", "1400"), "12-14", None),
    ("新潟", "芝", "外", ("1600", "1800"), "16-18", None),
    ("新潟", "芝", "内", ("2000", "2200", "2400"), "20-24", None),
    ("新潟", "芝", "外", ("3000", "3200"), "30-32", None),
    ("中山", "ダ", None, ("2400", "2500"), "24-25", None),
    ("東京", "芝", None, ("1600", "1800"), "16-18", None),
    ("東京", "芝", None, ("2300", "2400"), "23-24", None),
    ("東京", "ダ", None, ("1300", "1400"), "13-14", None),
    ("中京", "ダ", None, ("1800", "1900"), "18-19", None),
    ("京都", "芝", "内", ("1400", "1600"), "14-16", None),
    ("京都", "芝", "外", ("1400", "1600", "1800"), "14-18", None),
    ("京都", "芝", "内", ("2000",), "2000", "内外"),
    ("京都", "芝", "外", ("2000",), "2000", "内外"),
    ("京都", "芝", "外", ("2200", "2400"), "22-24", None),
    ("京都", "芝", "外", ("3000", "3200"), "30-32", None),
    ("京都", "ダ", None, ("1800", "1900"), "18-19", None),
    ("阪神", "芝", None, ("1600", "1800"), "16-18", None),
    ("阪神", "芝", None, ("2400", "2600"), "24-26", None),
    ("阪神", "芝", None, ("3000", "3200"), "30-32", None),
    ("小倉", "芝", None, ("1700", "1800"), "17-18", None),
)


def apply_distance_rules(place: pd.Series, surface: pd.Series, dist: pd.Series,
                         inner: pd.Series) -> tuple[pd.Series, pd.Series]:
    """距離レンジ統合。判定は元の値に対して行う（ルールの適用順に依存しない）。"""
    new_dist = dist.astype(object).copy()
    new_inner = inner.astype(object).copy()
    for pl, sf, io, dists, nd, ni in DISTANCE_RULES:
        cond = (place == pl) & (surface == sf) & dist.isin(dists)
        if io is not None:
            cond &= (inner == io)
        new_dist[cond] = nd
        if ni is not None:
            new_inner[cond] = ni
    return new_dist, new_inner


# ---------------------------------------------------------------------------
# 枠番グループ（9頭以下・不明は ""）
# ---------------------------------------------------------------------------
def frame_group(waku, heads) -> str:
    w = _to_int(waku)
    n = _to_int(heads)
    if w is None or n is None:
        return ""
    if 10 <= n <= 13:
        return "内枠" if 1 <= w <= 5 else ("外枠" if 6 <= w <= 8 else "")
    if 14 <= n <= 16:
        return "内枠" if 1 <= w <= 4 else ("外枠" if 5 <= w <= 8 else "")
    if 17 <= n <= 18:
        if 1 <= w <= 3:
            return "内枠"
        if 4 <= w <= 6:
            return "中枠"
        if 7 <= w <= 8:
            return "外枠"
    return ""


def frame_group_series(waku: pd.Series, heads: pd.Series) -> pd.Series:
    return pd.Series([frame_group(w, h) for w, h in zip(waku, heads)], index=waku.index, dtype=object)


def _to_int(v):
    t = norm_text(v)
    try:
        return int(float(t))
    except (TypeError, ValueError):
        return None


def int_series(raw: pd.Series) -> pd.Series:
    """'16' / '16.0' → 16（nullable Int64）。"""
    return pd.to_numeric(norm_series(raw), errors="coerce").round().astype("Int64")


# ---------------------------------------------------------------------------
# 芝(内・外)
# ---------------------------------------------------------------------------
# 出馬表の「外回り」列から作る。京都・新潟の芝は内回り/外回りでコースが別扱い
# （距離レンジのルールも内外で分かれる）なので、外回りでなければ「内」とする。
INNER_OUTER_PLACES = ("京都", "新潟")
_OUTER_TOKENS = {"外", "外回り", "True", "true", "1"}
_INNER_TOKENS = {"内", "内回り", "False", "false", "0"}


def entry_inner_outer(place: str, surface: str, gaimawari) -> str:
    g = norm_text(gaimawari)
    if g in _OUTER_TOKENS:
        return "外"
    if surface == "芝" and place in INNER_OUTER_PLACES:
        return "内"
    if g in _INNER_TOKENS and surface == "芝":
        return "内"
    return ""


# ---------------------------------------------------------------------------
# レースID・日付
# ---------------------------------------------------------------------------
def split_surface_distance(surface: pd.Series, distance: pd.Series) -> tuple[pd.Series, pd.Series]:
    """芝・ダ 列が無い・空で、距離 が「ダ1200」「芝2000」の形（★6タブの過去レースCSV）なら分ける。
    独立した 芝・ダ 列がある形（従来の形）はそのまま。障害は「障」。戻り値は (芝・ダ, 距離) の文字列。"""
    s = norm_series(surface)
    d = norm_series(distance)
    m = d.str.extract(r"^(芝|ダ|障)(.+)$")
    has = m[0].notna()
    s = s.where(~(has & (s == "")), m[0])
    d = d.where(~has, m[1])
    return s.fillna("").astype(object), d.fillna("").astype(object)


def race_id_without_horse(rid: pd.Series, given: pd.Series | None = None) -> pd.Series:
    """レースID(新/馬番無)（16桁）。列があればそれを使う。無ければ レースID(新) から作る:
    18桁 → 先頭16桁 / 16桁（もともとレース単位）→ そのまま / それ以外 → 末尾2桁を落とす（旧来の扱い）。
    （16桁のレース単位IDから末尾2桁を落とすと R が消えて、同じ日・場の全レースが1レース扱いになる）
    """
    rid = norm_series(rid)
    legacy = rid.where(rid.str.len() > 2, "").str.slice(0, -2)
    derived = rid.str.slice(0, 16).where(rid.str.fullmatch(r"\d{18}"), rid.where(rid.str.fullmatch(r"\d{16}"), legacy))
    if given is None:
        return derived.astype(object)
    g = norm_series(given)
    return g.where(g != "", derived).astype(object)


def horse_race_id(rid: pd.Series, uma: pd.Series) -> pd.Series:
    """レースID(新) を馬単位（18桁）にそろえる。16桁（レース単位）のIDには 馬番2桁 を付ける。

    TARGET の出力設定によってはレースID(新) がレース単位になる。そのままだと同じレースの馬が
    同じIDを持ち、レースID で突き合わせる処理で別の馬同士が掛け合わされる（旧版の band 系で
    件数・勝率が膨らんだ不具合の原因）。馬番が読めない行はそのまま返す。
    """
    rid = norm_series(rid)
    num = int_series(uma)
    ok = rid.str.fullmatch(r"\d{16}") & num.between(1, 99).fillna(False).astype(bool)
    pad = num.astype("float").map(lambda v: "" if v != v else f"{int(v):02d}")
    return rid.where(~ok, rid + pad).astype(object)


def race_date(rid: pd.Series) -> pd.Series:
    """レースID(新) の先頭8桁（YYYYMMDD）。形式が違えば ""。"""
    rid = norm_series(rid)
    head = rid.str.slice(0, 8)
    return head.where(head.str.fullmatch(r"(19|20)\d{6}"), "").astype(object)


def race_year(date: pd.Series) -> pd.Series:
    return date.astype(str).str.slice(0, 4).where(date != "", "").astype(object)


def shinba_mask(class_raw: pd.Series) -> pd.Series:
    return norm_series(class_raw).str.contains("新馬", na=False)


def nonempty(s: pd.Series) -> np.ndarray:
    return (s.astype(str) != "").to_numpy()
