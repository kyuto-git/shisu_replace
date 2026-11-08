"""レース内の偏差値・順位・帯の計算（過去側・出馬表側で共通）。

compute_indicator() の規則:
  0. 台帳で zero_missing の指数（安井のランク・5ステータス、想定前半・想定後半）は 0 を欠損にし、
     元の順位列を使わずに値から順位を計算する（TARGET の順位は 0 の馬も数えているため）
  1. 指数の欠損（数値化できない値）が同一レースで 2頭以上 → そのレースはこの指数では使わない
     1頭だけ → その馬の順位は空（偏差値は欠損なので付かない。ZI だけは下の特例で埋まる）
  2. ZI 特例: 0 または欠損を、同レースの非ゼロ平均で置き換える
  3. 小さいほど良い指数（展開・想定前半/後半）は符号反転。ただし元の順位があれば、偏差値の向きは順位に合わせる
     （1位の馬がレースの最大値なら大きいほど上。rank_direction_votes）
  4. 新馬除外の指数（ZI・吉馬6種）は新馬戦の馬を除外
  5. 有効値が2頭未満、または全頭が同じ値（標準偏差0。未取得で全頭0のときなど）
     → そのレースはこの指数では使わない（旧版の出馬表側は全頭50にしていた）
  6. 偏差値 = (x − レース平均) / 母標準偏差 × 10 + 50 を四捨五入（numpy の丸め）
  7. 順位は元データの順位列を優先（0. の指数を除く）。無い（またはそのレースに1頭も無い）ときは同値同順位（method=min）で計算する
     （同梱サンプルで TARGET の 指数順位 と照合すると、ZI・オメガ指数は min と100%一致した）。
     ZI を値から順位付けするときは 0 の馬を最下位（同順位）にする
  値は NFKC・空白除去してから数値化する（全角数字・全角マイナスを欠損にしない）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .csvio import norm_series
from .indicators import IndicatorSpec

HENSACHI_BANDS = ((71, "71以上"), (66, "66~70"), (61, "61~65"), (51, "51~60"), (41, "41~50"))
HENSACHI_BAND_LOW = "40以下"

# 順位帯（指数順位・前走着順・前走人気・前走上がり3F順で共通の8区分）
#   s1 = 細区分5帯（排他）: 1 / 2~3 / 4~5 / 6~9 / 10~
#   s2 = 広区分3帯（重複あり）: 1~3 / 1~5 / 6~   … 2位の馬は 1~3 と 1~5 の両方に入る
# 帯の名前（単位つき）は band_labels() で付ける。指数順位なら「2~3位」、前走着順なら「前走2~3着」
RANK_SCHEMES: dict[str, tuple[tuple[int, int | None], ...]] = {
    "s1": ((1, 1), (2, 3), (4, 5), (6, 9), (10, None)),
    "s2": ((1, 3), (1, 5), (6, None)),
}
# 帯の名前の付け方: (前置き, 単位)
BAND_UNITS = {"rank": ("", "位"), "finish": ("前走", "着"), "pop": ("前走", "人気"), "agari": ("前走上り", "位")}


def _band_text(lo: int, hi: int | None, kind: str) -> str:
    pre, unit = BAND_UNITS[kind]
    if hi is None:
        return f"{pre}{lo}{unit}~"
    if lo == hi:
        return f"{pre}{lo}{unit}"
    return f"{pre}{lo}~{hi}{unit}"


def band_labels(n, scheme: str, kind: str = "rank") -> list[str]:
    """順位・着順など（1以上の整数）→ その帯の名前のリスト。s1 は1つ、s2 は1〜2つ（重複あり）。数でなければ []。"""
    if n is None:
        return []
    try:
        if pd.isna(n):
            return []
        r = int(float(n))
    except (TypeError, ValueError):
        return []
    if r < 1:
        return []
    return [_band_text(lo, hi, kind) for lo, hi in RANK_SCHEMES[scheme] if r >= lo and (hi is None or r <= hi)]


def all_band_labels(scheme: str, kind: str = "rank") -> list[str]:
    return [_band_text(lo, hi, kind) for lo, hi in RANK_SCHEMES[scheme]]


def hensachi_band(v) -> str:
    if v is None or pd.isna(v):
        return ""
    v = float(v)
    for lo, label in HENSACHI_BANDS:
        if v >= lo:
            return label
    return HENSACHI_BAND_LOW


def rank_band(rank, scheme: str) -> str:
    """表示用の順位帯（s2 で2つに入るときは「1~3位・1~5位」）。キーは band_labels() で1つずつ作る。"""
    return "・".join(band_labels(rank, scheme, "rank"))


def compute_indicator(values: pd.Series, race_key: pd.Series, spec: IndicatorSpec,
                      shinba: pd.Series | None = None,
                      given_rank: pd.Series | None = None,
                      last: pd.Series | None = None) -> pd.DataFrame:
    """1指数ぶんの偏差値・順位・帯を返す（index は values と同じ）。

    last: 値から順位を付けるとき最下位（同順位）にする馬（外部指数のファイルに無く 0 で埋めた馬。TARGET と同じ扱い）。

    返す列: 偏差値(float), 偏差値帯, 順位(Int64), 順位帯_s1, 順位帯_s2,
            race_excluded(bool: レースごと除外), shinba_excluded(bool), usable(bool)
    """
    idx = values.index
    key = race_key.astype(str)
    x = pd.to_numeric(norm_series(values), errors="coerce").astype(float)
    if spec.zero_missing:           # 0 = 値なし（TARGET が取り込んでいない馬を 0 で出すため）
        x = x.where(x != 0)
        given_rank = None           # TARGET の順位は 0 の馬も数えているので使わず、値から計算する
    given = pd.to_numeric(norm_series(given_rank), errors="coerce") if given_rank is not None else None
    # 偏差値の向き: TARGET の順位があれば、それと同じ向きにする（1位の馬がレースの最大値か最小値か）。
    # 台帳の reverse（小さいほど良い）は順位が無いときの既定。両側とも同じ規則なので、片側だけ向きが変わることはない
    reverse = spec.reverse
    direction = "spec"
    if given is not None:
        votes = rank_direction_votes(x.where(x != 0) if spec.zi else x, given, key)
        if votes["high"] != votes["low"]:
            reverse = votes["low"] > votes["high"]
            direction = "rank"
    raw = x

    miss = x.isna()
    miss_cnt = miss.groupby(key).transform("sum")
    race_missing = miss_cnt >= 2
    single_missing = miss & (miss_cnt == 1)

    if spec.zi:
        nonzero = x.where(x != 0)
        nz_mean = nonzero.groupby(key).transform("mean")
        x = x.where(~(x.isna() | (x == 0)), nz_mean)

    if reverse:
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

    at_last = (last.reindex(idx).fillna(False).astype(bool) if last is not None
               else pd.Series(False, index=idx))
    if spec.zi:
        # ZI の順位は 0 の馬を最下位（同順位）にする。TARGET の順位と同じ（平均で埋めた値では順位を付けない）
        valid = raw.where((raw != 0) & ~at_last)
        if reverse:
            valid = -valid
        value_rank = valid.groupby(key).rank(ascending=False, method="min")
        bottom = valid.notna().groupby(key).transform("sum") + 1
        value_rank = value_rank.where(valid.notna(), bottom.where((raw == 0) | at_last))
    else:
        # 外部指数のファイルに無い馬（last）は、値の向きに関係なく最下位（小さいほど良い指数でも 0 を1位にしない）
        present = x.where(~at_last)
        value_rank = present.groupby(key).rank(ascending=False, method="min")
        bottom = present.notna().groupby(key).transform("sum") + 1
        value_rank = value_rank.where(~at_last, bottom.where(at_last & x.notna()))
    if given is not None:
        # 元の順位を使う。ただし順位が1頭も無いレース（順位の欄が空のファイルのレースなど）は値から付ける
        no_given = given.isna().groupby(key).transform("all")
        rank = given.where(~no_given, value_rank)
    else:
        rank = value_rank
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
    out.attrs["reverse"] = bool(reverse)
    out.attrs["direction"] = direction       # "rank"（TARGET の順位から決めた）/ "spec"（台帳の既定）
    return out


def rank_direction_votes(x: pd.Series, rank: pd.Series, key: pd.Series) -> dict:
    """レースごとに、順位1の馬の値がレースの最大か最小か（{"high": 最大のレース数, "low": 最小のレース数}）。
    全頭同値・値の無いレースは数えない。"""
    d = pd.DataFrame({"x": x.to_numpy(), "r": rank.to_numpy(), "k": key.to_numpy()}).dropna(subset=["x"])
    if d.empty:
        return {"high": 0, "low": 0}
    g = d.groupby("k")["x"]
    d["mx"], d["mn"] = g.transform("max"), g.transform("min")
    top = d[(d["r"] == 1) & (d["mx"] > d["mn"])]
    hi = set(top.loc[top["x"] == top["mx"], "k"])
    lo = set(top.loc[top["x"] == top["mn"], "k"])
    return {"high": len(hi - lo), "low": len(lo - hi)}


def fmt_int(v) -> str:
    """Int64/float の整数値を '13' の形の文字列に。欠損は ''。"""
    if v is None or pd.isna(v):
        return ""
    return str(int(round(float(v))))


def int_text_series(s: pd.Series) -> pd.Series:
    return s.map(fmt_int).astype(object)


__all__ = ["compute_indicator", "rank_direction_votes", "hensachi_band", "rank_band", "band_labels", "all_band_labels", "RANK_SCHEMES",
           "BAND_UNITS", "fmt_int", "int_text_series", "np"]
