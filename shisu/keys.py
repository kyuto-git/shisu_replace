"""結合データ（集計キー）の窓口。定義と組み立ては shisu/families.py（family の台帳）にある。

旧来の名前（ALL_FAMILIES・FAMILY_LABELS など）は、使う family の台帳から引く関数にした
（selected_families.json で family を入れ替えられるようにしたため）。
"""
from __future__ import annotations

from .families import (COURSE, COURSE_FIELDS as HEAD_FIELDS, INNER_FIELD, SEP, Family, active, active_names,
                       build_keys, entry_key_candidates, entry_keys, get, inner_variants, label, known_names)
from .hensachi import RANK_SCHEMES

SCHEMES: tuple[str, ...] = tuple(RANK_SCHEMES)


def all_families() -> tuple[str, ...]:
    """使う family の名前（台帳の並び）。"""
    return active_names()


def is_band_family(name: str) -> bool:
    f = get(name)
    return bool(f and f.is_band)


def family_label(name: str, scheme: str = "") -> str:
    lab = label(name)
    return f"{lab}（{scheme}）" if scheme else lab


__all__ = ["COURSE", "HEAD_FIELDS", "INNER_FIELD", "SEP", "SCHEMES", "Family", "active", "all_families", "build_keys",
           "entry_key_candidates", "entry_keys", "family_label", "get", "inner_variants", "is_band_family",
           "known_names"]
