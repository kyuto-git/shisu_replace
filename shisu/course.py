"""コース属性・輸送距離のマスタ（shisu/data/）と、そこからの引き当て。

  コース属性_帯つき_v1_0_20261106.csv  場・芝ダ・距離・内外 → R値帯・直線帯・初角帯・CFS帯・F係数帯・クワドラント
  輸送距離マスタ_v1_0_20261028.csv     所属（美浦/栗東）× 競馬場 → 輸送距離の帯（北海道は滞在）
  quadrant_link_v1_0_20261023.csv      今走×前走コースの加点・減点の資料（今は参照用。集計には使っていない）

コースの引き当て（resolve）:
  1. (場所, 芝・ダ, 距離, 内外) が完全に一致する行
  2. 無ければ、同じ (場所, 芝・ダ, 距離) の行が1つだけならそれ（内外が分からない・TARGET と表記が違うとき。
     例: 新潟芝1000 は TARGET では「内」、マスタでは「直」。中山芝1200 は外回りしかない）
  3. 2つ以上ある（京都芝1400/1600・新潟芝2000 の内外）で内外が分からなければ引けない（空）
"""
from __future__ import annotations

import functools
import unicodedata
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"
COURSE_FILE = "コース属性_帯つき_v1_0_20261106.csv"
TRANSPORT_FILE = "輸送距離マスタ_v1_0_20261028.csv"
QUADRANT_LINK_FILE = "quadrant_link_v1_0_20261023.csv"

JRA_PLACES = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
LEFT_HANDED = frozenset({"東京", "中京", "新潟"})                      # 左回り。ほかの7場は右回り
SLOPED = frozenset({"東京", "中山", "阪神", "福島", "中京"})           # 直線に坂あり。ほかの5場は坂なし
QUADRANTS = ("S", "H", "T", "O", "ST", "OT", "SH", "OH", "N")
# 帯（マスタの値そのまま。画面の並び順にも使う）
R_BANDS = ("200以上", "180-200", "160-180", "140-160", "120-140", "100-120")
STRAIGHT_BANDS = ("短(-299)", "中(300-399)", "長(400-)")
CORNER_BANDS = ("かなり短い(-149)", "短い(150-249)", "やや短い(250-299)", "普通(300-399)", "長い(400-)")
LEVEL_BANDS = ("高", "中", "低")
ATTR_COLUMNS = {"R値帯": "R値帯", "直線帯": "直線帯", "初角帯": "初角帯", "CFS帯": "CFS帯", "F係数帯": "F係数帯",
                "今走クワドラント": "クワドラント_台帳"}
_INVALID = {"", "対象外", "（分類なし）", "（コース詳細に無い）"}


def _n(v) -> str:
    return unicodedata.normalize("NFKC", str(v if v is not None else "")).strip()


@functools.lru_cache(maxsize=1)
def course_table() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / COURSE_FILE, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    for c in df.columns:
        df[c] = df[c].map(_n)
    df["距離"] = df["距離"].map(lambda v: str(int(float(v))) if v else "")
    return df


@functools.lru_cache(maxsize=1)
def _course_index() -> tuple[dict, dict]:
    exact, by_dist = {}, {}
    for row in course_table().to_dict("records"):
        attrs = {k: ("" if row.get(src, "") in _INVALID else row.get(src, "")) for k, src in ATTR_COLUMNS.items()}
        attrs["course_key"] = row["course_key"]
        exact[(row["場"], row["芝ダ"], row["距離"], row["内外"])] = attrs
        by_dist.setdefault((row["場"], row["芝ダ"], row["距離"]), []).append(attrs)
    return exact, by_dist


def _dist_text(v) -> str:
    s = _n(v)
    try:
        return str(int(float(s)))
    except ValueError:
        return ""


def resolve_course(place, surface, distance, inner="") -> dict | None:
    """コースの属性（ATTR_COLUMNS の名前 → 値。分類の無い項目は ''）。引けなければ None。"""
    key3 = (_n(place), _n(surface), _dist_text(distance))
    if not all(key3):
        return None
    exact, by_dist = _course_index()
    hit = exact.get(key3 + (_n(inner),))
    if hit is not None:
        return hit
    cands = by_dist.get(key3, [])
    return cands[0] if len(cands) == 1 else None


def resolve_courses(place: pd.Series, surface: pd.Series, distance: pd.Series, inner: pd.Series) -> pd.DataFrame:
    """resolve_course のベクトル版。列は ATTR_COLUMNS の名前と course_key（引けない行は ''）。"""
    keys = pd.DataFrame({"p": place.map(_n), "s": surface.map(_n), "d": distance.map(_dist_text),
                         "i": inner.map(_n)}, index=place.index)
    cache: dict[tuple, dict | None] = {}
    rows = []
    for t in keys.itertuples(index=False, name=None):
        if t not in cache:
            cache[t] = resolve_course(*t)
        rows.append(cache[t])
    cols = list(ATTR_COLUMNS) + ["course_key"]
    out = pd.DataFrame([r if r is not None else {} for r in rows], index=place.index, columns=cols)
    return out.fillna("").astype(object)


def ambiguous_courses() -> set[tuple[str, str, str]]:
    """内外が分からないと引けない (場所, 芝・ダ, 距離)。"""
    _exact, by_dist = _course_index()
    return {k for k, v in by_dist.items() if len(v) > 1}


@functools.lru_cache(maxsize=1)
def transport_table() -> dict[tuple[str, str], str]:
    df = pd.read_csv(DATA_DIR / TRANSPORT_FILE, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    out = {}
    for row in df.to_dict("records"):
        stable, place, band = _n(row["所属"]), _n(row["競馬場"]), _n(row["帯"])
        if stable in ("美浦", "栗東") and place in JRA_PLACES and band:
            out[(stable, place)] = band
    return out


def transport_band(stable, place) -> str:
    """所属（美浦/栗東）× 競馬場 → 輸送距離の帯。地方・外国・マスタに無い組み合わせは ''。"""
    return transport_table().get((_n(stable), _n(place)), "")


def distance_band(surface, distance) -> str:
    """芝: 短1000-1399 / マイル1400-1799 / 中1800-2299 / クラシック2300-2599 / 長2600-
       ダ: 短1000-1399 / マイル1400-1799 / 中1800-2099 / 長2100-"""
    s, d = _n(surface), _dist_text(distance)
    if s not in ("芝", "ダ") or not d:
        return ""
    d = int(d)
    if d < 1000:
        return ""
    if s == "芝":
        bounds = ((1400, "芝短"), (1800, "芝マイル"), (2300, "芝中"), (2600, "芝クラシック"))
        last = "芝長"
    else:
        bounds = ((1400, "ダ短"), (1800, "ダマイル"), (2100, "ダ中"))
        last = "ダ長"
    for hi, label in bounds:
        if d < hi:
            return label
    return last


def turn_direction(place) -> str:
    p = _n(place)
    if p not in JRA_PLACES:
        return ""
    return "左" if p in LEFT_HANDED else "右"


def slope(place) -> str:
    p = _n(place)
    if p not in JRA_PLACES:
        return ""
    return "坂あり" if p in SLOPED else "坂なし"
