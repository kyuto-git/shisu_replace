"""オッズ帯（過去レースの確定単勝オッズで master の馬を絞り込む4パターン）。

  normal    : 絞り込みなし
  odds10u   : 10倍未満
  odds10_50 : 10倍以上 50倍未満
  odds50p   : 50倍以上
（旧仕様の odds5p＝5倍以上 は廃止）
"""
from __future__ import annotations

import pandas as pd

ODDS_MODES: tuple[str, ...] = ("normal", "odds10u", "odds10_50", "odds50p")
ODDS_LABELS = {"normal": "通常", "odds10u": "10倍未満", "odds10_50": "10〜50倍", "odds50p": "50倍以上"}
ODDS_RANK = {m: i for i, m in enumerate(ODDS_MODES)}   # 画面の表示順
LEGACY_MODES = ("odds5p",)                               # 旧仕様。build_db は取り込まない
ODDS_BREAKS = (10.0, 50.0)                               # odds10u / odds10_50 / odds50p の境目（集計の設定のハッシュにも入る）


def odds_mask(odds: pd.Series, mode: str) -> pd.Series:
    """mode に入る行。オッズ欠損の行は normal 以外には入らない。"""
    return odds_mask_numeric(pd.to_numeric(odds, errors="coerce"), mode)


def odds_mask_numeric(o: pd.Series, mode: str) -> pd.Series:
    """odds_mask の数値版（オッズを数にしたもの。欠損は NaN）。"""
    if mode == "normal":
        return pd.Series(True, index=o.index)
    lo, hi = ODDS_BREAKS
    if mode == "odds10u":
        return (o < lo).fillna(False)
    if mode == "odds10_50":
        return ((o >= lo) & (o < hi)).fillna(False)
    if mode == "odds50p":
        return (o >= hi).fillna(False)
    raise ValueError(f"unknown odds mode: {mode}")


def odds_bucket(odds: pd.Series) -> pd.Series:
    """1頭ごとのオッズ帯（master 用）。欠損は ''。"""
    o = pd.to_numeric(odds, errors="coerce")
    lo, hi = ODDS_BREAKS
    out = pd.Series("", index=o.index, dtype=object)
    out[o < lo] = "odds10u"
    out[(o >= lo) & (o < hi)] = "odds10_50"
    out[o >= hi] = "odds50p"
    return out


def mode_prefix(mode: str) -> str:
    return "" if mode == "normal" else f"{mode}_"
