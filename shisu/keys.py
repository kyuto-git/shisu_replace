"""結合データ（集計キー）の構成。過去側も出馬表側もここで組み立てる。

キー = 場所×芝・ダ×距離×[芝(内・外)]×（family ごとの後続部品）
- 芝(内・外) は空なら省く（ダートや内外の区別がないコース）。
- 場所・芝・ダ・距離と、family の後続部品が1つでも空ならキーを作らない。
  旧版は空欄を詰めて連結していたため、順位が空の馬が「順位なし」の別グループになったり、
  空欄が 'nan' や空キーになったりしていた。
"""
from __future__ import annotations

from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .hensachi import RANK_SCHEMES, rank_band

SEP = "×"
HEAD_FIELDS = ("場所", "芝・ダ", "距離")
INNER_FIELD = "芝(内・外)"

# 後続部品。RANK=指数順位, HBAND=偏差値帯, RBAND=順位帯（スキーム s1/s2）
FAMILY_TAILS: dict[str, tuple[str, ...]] = {
    "normal": ("重量種別", "クラス名", "RANK"),
    "rankB": ("枠番グループ", "RANK"),
    "hensachiA": ("重量種別", "クラス名", "HBAND"),
    "hensachiB": ("枠番グループ", "HBAND"),
    "rankJ": ("騎手", "RANK"),
    "hensachiJ": ("騎手", "HBAND"),
    "rankBJ": ("枠番グループ", "騎手", "RANK"),
    "hensachiBJ": ("枠番グループ", "騎手", "HBAND"),
    "normalJ": ("重量種別", "クラス名", "騎手", "RANK"),
    "hensachiAJ": ("重量種別", "クラス名", "騎手", "HBAND"),
    "normal_band": ("重量種別", "クラス名", "RBAND"),
    "rankB_band": ("枠番グループ", "RBAND"),
    "rankJ_band": ("騎手", "RBAND"),
    "rankBJ_band": ("枠番グループ", "騎手", "RBAND"),
    "normalJ_band": ("重量種別", "クラス名", "騎手", "RBAND"),
}
BASE_FAMILIES: tuple[str, ...] = ("normal", "rankB", "hensachiA", "hensachiB", "rankJ", "hensachiJ",
                                  "rankBJ", "hensachiBJ", "normalJ", "hensachiAJ")
BAND_FAMILIES: tuple[str, ...] = ("normal_band", "rankB_band", "rankJ_band", "rankBJ_band", "normalJ_band")
ALL_FAMILIES: tuple[str, ...] = BASE_FAMILIES + BAND_FAMILIES
SCHEMES: tuple[str, ...] = tuple(RANK_SCHEMES)

FAMILY_LABELS = {
    "normal": "重量種別×クラス名×指数順位",
    "rankB": "枠番グループ×指数順位",
    "hensachiA": "重量種別×クラス名×偏差値帯",
    "hensachiB": "枠番グループ×偏差値帯",
    "rankJ": "騎手×指数順位",
    "hensachiJ": "騎手×偏差値帯",
    "rankBJ": "枠番グループ×騎手×指数順位",
    "hensachiBJ": "枠番グループ×騎手×偏差値帯",
    "normalJ": "重量種別×クラス名×騎手×指数順位",
    "hensachiAJ": "重量種別×クラス名×騎手×偏差値帯",
    "normal_band": "重量種別×クラス名×順位帯",
    "rankB_band": "枠番グループ×順位帯",
    "rankJ_band": "騎手×順位帯",
    "rankBJ_band": "枠番グループ×騎手×順位帯",
    "normalJ_band": "重量種別×クラス名×騎手×順位帯",
}


def is_band_family(family: str) -> bool:
    return family in BAND_FAMILIES


def _tail_value(fields: Mapping, token: str, rband: str) -> str:
    if token == "RANK":
        return str(fields.get("順位") or "")
    if token == "HBAND":
        return str(fields.get("偏差値帯") or "")
    if token == "RBAND":
        return rband or ""
    return str(fields.get(token) or "")


def build_key(fields: Mapping, family: str, rband: str = "") -> str:
    """1頭ぶんのキー。fields は正規化済みの値（順位は '13' の形の文字列）。作れなければ ''。"""
    head = [str(fields.get(f) or "") for f in HEAD_FIELDS]
    if not all(head):
        return ""
    tail = [_tail_value(fields, t, rband) for t in FAMILY_TAILS[family]]
    if not all(tail):
        return ""
    inner = str(fields.get(INNER_FIELD) or "")
    parts = head + ([inner] if inner else []) + tail
    return SEP.join(parts)


def build_key_series(df: pd.DataFrame, family: str, rband_col: str | None = None) -> pd.Series:
    """build_key のベクトル版（過去側の大量行用）。df は fields と同じ列名を持つこと。"""
    def col(name: str) -> pd.Series:
        return df[name].astype(str) if name in df.columns else pd.Series("", index=df.index)

    head = [col(f) for f in HEAD_FIELDS]
    tail = []
    for t in FAMILY_TAILS[family]:
        if t == "RANK":
            tail.append(col("順位"))
        elif t == "HBAND":
            tail.append(col("偏差値帯"))
        elif t == "RBAND":
            tail.append(col(rband_col) if rband_col else pd.Series("", index=df.index))
        else:
            tail.append(col(t))
    valid = np.ones(len(df), dtype=bool)
    for s in head + tail:
        valid &= (s != "").to_numpy()
    inner = col(INNER_FIELD)
    key = head[0] + SEP + head[1] + SEP + head[2]
    key = key + np.where(inner != "", SEP + inner, "")
    for s in tail:
        key = key + SEP + s
    return key.where(valid, "").astype(object)


def inner_variants(inner: str) -> list[str]:
    """出馬表側で試す 芝(内・外) の候補。

    分かっていれば [その値, 空]、分からなければ [空, 内, 外, 内外]。
    出馬表には京都・新潟以外の内外情報が無く、過去側（TARGET の 芝(内・外) 列）は
    阪神・中山などにも値を持つため、未知の場合は全候補を試す。
    """
    return [inner, ""] if inner else ["", "内", "外", "内外"]


def entry_key_candidates(fields: Mapping, families: Iterable[str] = ALL_FAMILIES) -> dict[str, list[tuple[str, str]]]:
    """出馬表の1頭×1指数について、family ごとの (キー, 順位帯スキーム) の候補。"""
    out: dict[str, list[tuple[str, str]]] = {}
    inner = str(fields.get(INNER_FIELD) or "")
    rank = fields.get("順位")
    for fam in families:
        cands: list[tuple[str, str]] = []
        schemes = SCHEMES if is_band_family(fam) else ("",)
        for scheme in schemes:
            rb = rank_band(_as_int(rank), scheme) if scheme else ""
            if scheme and not rb:
                continue
            for io in inner_variants(inner):
                k = build_key({**fields, INNER_FIELD: io}, fam, rb)
                if k and (k, scheme) not in cands:
                    cands.append((k, scheme))
        if cands:
            out[fam] = cands
    return out


def _as_int(v):
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return None
