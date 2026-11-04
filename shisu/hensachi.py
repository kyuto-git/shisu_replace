"""レース内の偏差値・順位・帯の計算（過去側・出馬表側で共通）。

compute_indicator() の規則:
  1. 指数の欠損（数値化できない値）が同一レースで 2頭以上 → そのレースはこの指数では使わない
     1頭だけ → その馬の順位は空（偏差値は欠損なので付かない。ZI だけは下の特例で埋まる）
  2. ZI 特例: 0 または欠損を、同レースの非ゼロ平均で置き換える
  3. 小さいほど良い指数（タイムM・展開・想定前半/後半）は符号反転
  4. 新馬除外の指数（ZI・吉馬6種）は新馬戦の馬を除外
  5. 有効値が2頭未満、または全頭が同じ値（標準偏差0。未取得で全頭0のときなど）
     → そのレースはこの指数では使わない（旧版の出馬表側は全頭50にしていた）
  6. 偏差値 = (x − レース平均) / 母標準偏差 × 10 + 50 を四捨五入（numpy の丸め）
  7. 順位は元データの順位列を優先。無ければ同値同順位（method=min）で計算
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import IndicatorSpec

HENSACHI_BANDS = ((71, "71以上"), (66, "66~70"), (61, "61~65"), (51, "51~60"), (41, "41~50"))
HENSACHI_BAND_LOW = "40以下"

# 順位帯（過去側の集計スキーム。出馬表側も同じ定義で帯を作るので必ず一致する）
RANK_SCHEMES: dict[str, tuple[tuple[int, int | None, str], ...]] = {
    "s1": ((1, 3, "1~3位"), (4, 6, "4~6位"), (7, 9, "7~9位"), (10, None, "10位~")),
    "s2": ((1, 5, "1~5位"), (6, 10, "6~10位"), (11, None, "11位~")),
}


def hensachi_band(v) -> str:
    if v is None or pd.isna(v):
        return ""
    v = float(v)
    for lo, label in HENSACHI_BANDS:
        if v >= lo:
            return label
    return HENSACHI_BAND_LOW


def rank_band(rank, scheme: str) -> str:
    if rank is None or pd.isna(rank):
        return ""
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return ""
    for lo, hi, label in RANK_SCHEMES[scheme]:
        if r >= lo and (hi is None or r <= hi):
            return label
    return ""


def compute_indicator(values: pd.Series, race_key: pd.Series, spec: IndicatorSpec,
                      shinba: pd.Series | None = None,
                      given_rank: pd.Series | None = None) -> pd.DataFrame:
    """1指数ぶんの偏差値・順位・帯を返す（index は values と同じ）。

    返す列: 偏差値(float), 偏差値帯, 順位(Int64), 順位帯_s1, 順位帯_s2,
            race_excluded(bool: レースごと除外), shinba_excluded(bool), usable(bool)
    """
    idx = values.index
    key = race_key.astype(str)
    x = pd.to_numeric(values, errors="coerce").astype(float)

    miss = x.isna()
    miss_cnt = miss.groupby(key).transform("sum")
    race_missing = miss_cnt >= 2
    single_missing = miss & (miss_cnt == 1)

    if spec.zi:
        nonzero = x.where(x != 0)
        nz_mean = nonzero.groupby(key).transform("mean")
        x = x.where(~(x.isna() | (x == 0)), nz_mean)

    if spec.reverse:
        x = -x

    if spec.exclude_shinba and shinba is not None:
        shin = shinba.reindex(idx).fillna(False).astype(bool)
    else:
        shin = pd.Series(False, index=idx)
    x = x.where(~shin)

    grp = x.groupby(key)
    cnt = grp.transform("count")
    mean = grp.transform("mean")
    std = grp.transform("std", ddof=0)
    race_invalid = (cnt < 2) | std.isna() | (std <= 1e-12)
    race_excluded = race_missing | race_invalid

    ok = ~race_excluded & x.notna()
    hensachi = ((x - mean) / std * 10.0 + 50.0).where(ok).round(0)

    if given_rank is not None:
        rank = pd.to_numeric(given_rank, errors="coerce")
    else:
        rank = x.groupby(key).rank(ascending=False, method="min")
    rank = rank.where(ok & ~single_missing)
    rank = rank.where(rank >= 1).round().astype("Int64")

    out = pd.DataFrame(index=idx)
    out["偏差値"] = hensachi
    out["偏差値帯"] = hensachi.map(hensachi_band).astype(object)
    out["順位"] = rank
    for scheme in RANK_SCHEMES:
        out[f"順位帯_{scheme}"] = rank.map(lambda r, s=scheme: rank_band(r, s)).astype(object)
    out["race_excluded"] = race_excluded.astype(bool)
    out["shinba_excluded"] = shin.astype(bool)
    out["usable"] = (ok & ~shin).astype(bool)
    return out


def fmt_int(v) -> str:
    """Int64/float の整数値を '13' の形の文字列に。欠損は ''。"""
    if v is None or pd.isna(v):
        return ""
    return str(int(round(float(v))))


def int_text_series(s: pd.Series) -> pd.Series:
    return s.map(fmt_int).astype(object)


__all__ = ["compute_indicator", "hensachi_band", "rank_band", "RANK_SCHEMES", "fmt_int", "int_text_series", "np"]
