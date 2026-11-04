"""WEDGE（= WIN + EDGE）と年度加重（Phase 3 の下準備。集計への組み込みは次のステップ）。

WIN  = (1着数×3.0 + 2着数×1.5 + 3着数×1.0) / (件数×3.0)
EDGE = 成功数 / 件数。成功 = 単勝オッズ帯ごとの期待着順以内に入ったこと
       （オッズは 100倍超を 100倍にクリップ）
         1.0〜1.4倍 → 1着 / 1.5〜2.9 → 1着 / 3.0〜4.9 → 2着以内 / 5.0〜9.9 → 3着以内
         10.0〜19.9 → 4着以内 / 20.0〜49.9 → 6着以内 / 50.0〜100.0 → 8着以内
目安: 1.00以上=トップ級、0.62前後=平均、0.50未満=下位

年度加重: 年別 WEDGE に、古い順で次のウェイトを掛けて合算（adjusted_WEDGE）
  5年: [0.06, 0.11, 0.18, 0.25, 0.40]（6年以上は直近5年）/ 4年: [0.12, 0.19, 0.27, 0.42]
  3年以下: linspace(0.1, 0.5, n) を合計1に正規化
件数の少ない年を除く閾値 min_year_count は結合データの粒度に合わせて調整する（既定 0＝除外しない）。

入力は aggregate と同じ素材（着順・単勝オッズ）を想定。着順は shisu.finish.parse_finish の finish_num。
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


def edge_success(odds: pd.Series, finish_num: pd.Series) -> pd.Series:
    """1頭ごとの EDGE 成功フラグ（1/0）。オッズか着順が欠損なら <NA>。"""
    th = pd.to_numeric(odds, errors="coerce").map(edge_threshold)
    fin = pd.to_numeric(finish_num, errors="coerce")
    ok = th.notna() & fin.notna()
    flag = (fin <= th).astype("Int64")
    return flag.where(ok, pd.NA).astype("Int64")


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
                   min_year_count: int = 0) -> float:
    """年別 WEDGE の加重平均。件数 < min_year_count の年は除いてからウェイトを決める。"""
    items = []
    for y, v in yearly_wedge.items():
        if v is None or (isinstance(v, float) and v != v):
            continue
        if yearly_count is not None and min_year_count and yearly_count.get(y, 0) < min_year_count:
            continue
        items.append((int(y), float(v)))
    if not items:
        return float("nan")
    items.sort()
    items = items[-MAX_YEARS:]
    w = year_weights(len(items))
    return float(np.dot(w, [v for _, v in items]))


def wedge_from_frame(df: pd.DataFrame, finish_col: str = "finish_num", odds_col: str = "単勝オッズ") -> dict:
    """1グループ（分母に入る行だけ）の WIN / EDGE / WEDGE。"""
    fin = pd.to_numeric(df[finish_col], errors="coerce")
    n = int(len(df))
    n1, n2, n3 = int((fin == 1).sum()), int((fin == 2).sum()), int((fin == 3).sum())
    succ = edge_success(df[odds_col], fin)
    n_edge = int(succ.notna().sum())
    win = win_score(n1, n2, n3, n)
    edge = edge_score(int(succ.fillna(0).sum()), n_edge)
    return {"件数": n, "WIN": win, "EDGE": edge, "WEDGE": wedge(win, edge) if n and n_edge else float("nan")}
