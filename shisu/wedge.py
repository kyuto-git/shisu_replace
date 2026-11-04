"""WEDGE（= WIN + EDGE）と年度加重 adjusted_WEDGE。

WIN  = (1着数×3.0 + 2着数×1.5 + 3着数×1.0) / (件数×3.0)
EDGE = 成功数 / オッズのある件数。成功 = 単勝オッズ帯ごとの期待着順以内に入ったこと
       （オッズは 100倍超を 100倍にクリップ）
         1.0〜1.4倍 → 1着 / 1.5〜2.9 → 1着 / 3.0〜4.9 → 2着以内 / 5.0〜9.9 → 3着以内
         10.0〜19.9 → 4着以内 / 20.0〜49.9 → 6着以内 / 50.0〜100.0 → 8着以内
WEDGE = WIN + EDGE。目安: 1.00以上=トップ級、0.62前後=平均、0.50未満=下位

件数は aggregate と同じ「分母に入る馬」（取消・除外を除く。失格・中止は着順=頭数で失敗扱い）。
オッズが無い・1倍未満の馬は EDGE の分母から外す（WIN の分母には入る）。

年度加重 adjusted_WEDGE:
  1. 年（year）ごとに WEDGE を出す
  2. 直近5年の窓に入る年だけを使う。窓の終わりは
       window="data"（既定）: 素材ファイル全体の最終年（＝データとしての「今」）
       window="key"         : その結合データ自身の最終年
     6年以上あっても窓の外の古い年は使わない
  3. 件数 < min_year_count（既定30）の年は統計的ノイズとして除く
  4. 残った年数 m に応じて、古い順に次のウェイトで加重平均
       m=5: [0.06, 0.11, 0.18, 0.25, 0.40] / m=4: [0.12, 0.19, 0.27, 0.42]
       m≤3: linspace(0.1, 0.5, m) を合計1に正規化（m=1 なら 1.0）
  5. 残った年が無ければ空（NaN）
  あわせて、使った年数・件数の合計・最終年を返す（値の信頼度と新しさの目安）。

scalar 版（edge_threshold / adjusted_wedge / wedge_from_frame）は仕様どおりの素直な実装で、
ベクトル版 wedge_columns() の正しさをテストで照合するための基準にもなっている。
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

ODDS_CLIP = 100.0
# (下限以上, 上限未満, 成功とみなす着順の上限)
EDGE_TABLE: tuple[tuple[float, float, int], ...] = (
    (1.0, 1.5, 1),
    (1.5, 3.0, 1),
    (3.0, 5.0, 2),
    (5.0, 10.0, 3),
    (10.0, 20.0, 4),
    (20.0, 50.0, 6),
    (50.0, float("inf"), 8),
)
YEAR_WEIGHTS = {5: (0.06, 0.11, 0.18, 0.25, 0.40), 4: (0.12, 0.19, 0.27, 0.42)}
MAX_YEARS = 5
DEFAULT_MIN_YEAR_COUNT = 30
WINDOWS = ("data", "key")

WEDGE_COLUMNS = ("WIN", "EDGE", "WEDGE", "adjusted_WEDGE",
                 "adjusted_WEDGE_年数", "adjusted_WEDGE_件数", "adjusted_WEDGE_最終年")
WEDGE_FLOAT_COLUMNS = ("WIN", "EDGE", "WEDGE", "adjusted_WEDGE")
WEDGE_INT_COLUMNS = ("adjusted_WEDGE_年数", "adjusted_WEDGE_件数", "adjusted_WEDGE_最終年")

_EDGE_LOWS = np.array([lo for lo, _, _ in EDGE_TABLE])
_EDGE_THS = np.array([th for _, _, th in EDGE_TABLE], dtype=float)


# ---------------------------------------------------------------------------
# 1頭・1グループ単位（仕様どおりの素直な実装）
# ---------------------------------------------------------------------------
def edge_threshold(odds) -> float:
    """単勝オッズ → 成功とみなす着順の上限（オッズ欠損・1倍未満は NaN）。"""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return float("nan")
    if o != o or o < 1.0:
        return float("nan")
    o = min(o, ODDS_CLIP)
    for lo, hi, th in EDGE_TABLE:
        if lo <= o < hi:
            return float(th)
    return float("nan")  # pragma: no cover


def win_score(n1, n2, n3, n) -> float:
    n = float(n)
    if n <= 0:
        return float("nan")
    return (float(n1) * 3.0 + float(n2) * 1.5 + float(n3) * 1.0) / (n * 3.0)


def edge_score(successes, n) -> float:
    n = float(n)
    return float("nan") if n <= 0 else float(successes) / n


def wedge(win: float, edge: float) -> float:
    return win + edge


def year_weights(n_years: int) -> np.ndarray:
    """古い順のウェイト（合計1）。"""
    n = min(int(n_years), MAX_YEARS)
    if n <= 0:
        return np.array([])
    if n in YEAR_WEIGHTS:
        return np.array(YEAR_WEIGHTS[n], dtype=float)
    w = np.linspace(0.1, 0.5, n)
    return w / w.sum()


def adjusted_wedge(yearly_wedge: Mapping[int, float], yearly_count: Mapping[int, int] | None = None,
                   min_year_count: int = 0, window_end: int | None = None) -> float:
    """年別 WEDGE の加重平均。

    window_end（窓の終わりの年）から遡る5年だけを使い、件数 < min_year_count の年を除いてからウェイトを決める。
    window_end を省くと、渡された年の最終年を窓の終わりにする。
    """
    years = [int(y) for y, v in yearly_wedge.items() if v is not None and not (isinstance(v, float) and v != v)]
    if not years:
        return float("nan")
    end = int(window_end) if window_end is not None else max(years)
    items = []
    for y in sorted(years):
        if not (end - MAX_YEARS + 1 <= y <= end):
            continue
        if yearly_count is not None and min_year_count and yearly_count.get(y, 0) < min_year_count:
            continue
        items.append(float(yearly_wedge[y]))
    if not items:
        return float("nan")
    return float(np.dot(year_weights(len(items)), items))


def wedge_from_frame(df: pd.DataFrame, finish_col: str = "finish_num", odds_col: str = "単勝オッズ") -> dict:
    """1グループ（分母に入る行だけ）の WIN / EDGE / WEDGE。"""
    fin = pd.to_numeric(df[finish_col], errors="coerce")
    n = int(len(df))
    n1, n2, n3 = int((fin == 1).sum()), int((fin == 2).sum()), int((fin == 3).sum())
    th = pd.to_numeric(df[odds_col], errors="coerce").map(edge_threshold)
    ok = th.notna() & fin.notna()
    n_edge = int(ok.sum())
    win = win_score(n1, n2, n3, n)
    edge = edge_score(int((fin[ok] <= th[ok]).sum()), n_edge)
    return {"件数": n, "WIN": win, "EDGE": edge, "WEDGE": wedge(win, edge) if n and n_edge else float("nan")}


# ---------------------------------------------------------------------------
# ベクトル版（aggregate_stats_ver2.py で使用）
# ---------------------------------------------------------------------------
def edge_thresholds(odds: pd.Series) -> pd.Series:
    """edge_threshold のベクトル版。"""
    o = pd.to_numeric(odds, errors="coerce").astype(float)
    valid = o.notna() & (o >= 1.0)
    idx = np.searchsorted(_EDGE_LOWS, np.minimum(o.fillna(0).to_numpy(), ODDS_CLIP), side="right") - 1
    th = _EDGE_THS[np.clip(idx, 0, len(_EDGE_THS) - 1)]
    return pd.Series(np.where(valid.to_numpy(), th, np.nan), index=o.index)


def edge_success(odds: pd.Series, finish_num: pd.Series) -> pd.Series:
    """1頭ごとの EDGE 成功フラグ（1/0）。オッズか着順が欠損なら <NA>。"""
    th = edge_thresholds(odds)
    fin = pd.to_numeric(finish_num, errors="coerce")
    ok = th.notna() & fin.notna()
    return (fin <= th).astype("Int64").where(ok, pd.NA).astype("Int64")


def _scores(s: pd.DataFrame) -> pd.DataFrame:
    """n, n1, n2, n3, n_edge, n_succ の合計から WIN / EDGE / WEDGE。"""
    n = s["n"].astype(float)
    win = (s["n1"] * 3.0 + s["n2"] * 1.5 + s["n3"] * 1.0) / (n * 3.0)
    edge = s["n_succ"] / s["n_edge"].where(s["n_edge"] > 0)
    win = win.where(n > 0)
    return pd.DataFrame({"WIN": win, "EDGE": edge, "WEDGE": win + edge}, index=s.index)


def _weight_matrix() -> np.ndarray:
    m = np.zeros((MAX_YEARS + 1, MAX_YEARS))
    for k in range(1, MAX_YEARS + 1):
        m[k, :k] = year_weights(k)
    return m


_WEIGHTS = _weight_matrix()


def wedge_columns(keys: pd.Series, finish_num: pd.Series, odds: pd.Series, years: pd.Series,
                  ref_year: int | None = None, min_year_count: int = DEFAULT_MIN_YEAR_COUNT,
                  window: str = "data") -> pd.DataFrame:
    """グループ（keys）ごとの WEDGE 列。index はグループのキー、列は WEDGE_COLUMNS。

    入力はすべて同じ index（分母に入る馬だけ）。years は西暦（文字列でも可。不正値は年別集計から外す）。
    ref_year は window="data" のときの窓の終わり（省略時は years の最大値）。
    """
    if window not in WINDOWS:
        raise ValueError(f"window は {WINDOWS} のどれか: {window}")
    fin = pd.to_numeric(finish_num, errors="coerce")
    th = edge_thresholds(odds)
    ok = th.notna() & fin.notna()
    base = pd.DataFrame({
        "k": keys.to_numpy(),
        "y": pd.to_numeric(years, errors="coerce").to_numpy(),
        "n": 1,
        "n1": (fin == 1).astype(int).to_numpy(),
        "n2": (fin == 2).astype(int).to_numpy(),
        "n3": (fin == 3).astype(int).to_numpy(),
        "n_edge": ok.astype(int).to_numpy(),
        "n_succ": (ok & (fin <= th)).astype(int).to_numpy(),
    })
    sums = ["n", "n1", "n2", "n3", "n_edge", "n_succ"]
    overall = _scores(base.groupby("k", sort=True)[sums].sum())
    out = overall.copy()
    out["adjusted_WEDGE"] = np.nan
    out["adjusted_WEDGE_年数"] = 0
    out["adjusted_WEDGE_件数"] = 0
    out["adjusted_WEDGE_最終年"] = np.nan

    yb = base[base["y"].notna()]
    if not yb.empty:
        yearly = yb.groupby(["k", "y"], sort=True)[sums].sum()
        yearly = yearly.join(_scores(yearly)).reset_index()
        if window == "data":
            end = float(ref_year) if ref_year is not None else float(yb["y"].max())
            yearly["end"] = end
        else:
            yearly["end"] = yearly.groupby("k")["y"].transform("max")
        yearly = yearly[(yearly["y"] <= yearly["end"]) & (yearly["y"] > yearly["end"] - MAX_YEARS)]
        yearly = yearly[(yearly["n"] >= min_year_count) & yearly["WEDGE"].notna()]
        if not yearly.empty:
            yearly = yearly.sort_values(["k", "y"])
            m = yearly.groupby("k")["y"].transform("size").to_numpy()
            pos = yearly.groupby("k").cumcount().to_numpy()
            yearly["w"] = _WEIGHTS[m, pos]
            yearly["wv"] = yearly["w"] * yearly["WEDGE"]
            agg = yearly.groupby("k").agg(adj=("wv", "sum"), years=("y", "size"), cnt=("n", "sum"), last=("y", "max"))
            out.loc[agg.index, "adjusted_WEDGE"] = agg["adj"]
            out.loc[agg.index, "adjusted_WEDGE_年数"] = agg["years"]
            out.loc[agg.index, "adjusted_WEDGE_件数"] = agg["cnt"]
            out.loc[agg.index, "adjusted_WEDGE_最終年"] = agg["last"]
    for c in WEDGE_INT_COLUMNS:
        out[c] = pd.to_numeric(out[c], errors="coerce").round().astype("Int64")
    return out[list(WEDGE_COLUMNS)]
