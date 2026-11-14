"""オッズ帯（過去レースの確定単勝オッズで master の馬を絞り込む4パターン）。

  normal    : 絞り込みなし
  odds10u   : 2倍以上 10倍未満（2026-11-14〜。それまでは「10倍未満」で 1倍台も入っていた。単勝1倍台の馬が入ると
              勝率・複勝率が上振れするので外した。1.9倍以下の馬は normal にだけ入る。モード名は互換のため odds10u のまま）
  odds10_50 : 10倍以上 50倍未満
  odds50p   : 50倍以上
（旧仕様の odds5p＝5倍以上 は廃止）
"""
from __future__ import annotations

import pandas as pd

ODDS_MODES: tuple[str, ...] = ("normal", "odds10u", "odds10_50", "odds50p")
ODDS_LABELS = {"normal": "通常", "odds10u": "2〜10倍未満", "odds10_50": "10〜50倍", "odds50p": "50倍以上"}
ODDS_RANK = {m: i for i, m in enumerate(ODDS_MODES)}   # 画面の表示順
LEGACY_MODES = ("odds5p",)                               # 旧仕様。build_db は取り込まない
# オッズ帯の境目: (odds10u の下限, odds10u と odds10_50 の境目, odds10_50 と odds50p の境目)。
# 集計の設定のハッシュ（agg_settings.json・書き終わりの印）に入るので、変えると aggregate --resume が全部の master を作り直す
ODDS_BREAKS = (2.0, 10.0, 50.0)
LEGACY_BREAKS = (10.0, 50.0)                             # 2026-11-13 までの境目（odds10u = 10倍未満。下限なし）
FAV_LABEL = ODDS_LABELS["odds10u"]                      # 画面の文言（消し(2〜10倍未満) など）に使う


def odds_mask(odds: pd.Series, mode: str) -> pd.Series:
    """mode に入る行。オッズ欠損の行は normal 以外には入らない。"""
    return odds_mask_numeric(pd.to_numeric(odds, errors="coerce"), mode)


def odds_mask_numeric(o: pd.Series, mode: str) -> pd.Series:
    """odds_mask の数値版（オッズを数にしたもの。欠損は NaN）。"""
    if mode == "normal":
        return pd.Series(True, index=o.index)
    floor, lo, hi = ODDS_BREAKS
    if mode == "odds10u":
        return ((o >= floor) & (o < lo)).fillna(False)
    if mode == "odds10_50":
        return ((o >= lo) & (o < hi)).fillna(False)
    if mode == "odds50p":
        return (o >= hi).fillna(False)
    raise ValueError(f"unknown odds mode: {mode}")


def odds_bucket(odds: pd.Series) -> pd.Series:
    """1頭ごとのオッズ帯（master 用）。欠損と、どの帯にも入らない馬（odds10u の下限より低い 1倍台）は ''。"""
    o = pd.to_numeric(odds, errors="coerce")
    floor, lo, hi = ODDS_BREAKS
    out = pd.Series("", index=o.index, dtype=object)
    out[(o >= floor) & (o < lo)] = "odds10u"
    out[(o >= lo) & (o < hi)] = "odds10_50"
    out[o >= hi] = "odds50p"
    return out


def mode_prefix(mode: str) -> str:
    return "" if mode == "normal" else f"{mode}_"
