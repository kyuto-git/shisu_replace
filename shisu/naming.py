"""成績表ファイルの名前と DB テーブル名の規約。

成績表（aggregate_stats_ver2.py の出力。オッズ帯ごとに1本）:
  agg_csv/foragg_{オッズ接頭辞}{rankband_スキーム_}{family}_{元ファイル名}.csv
  例: foragg_normal_主５場（中山、東京、中京、京都、阪神）_タイムM.csv
      foragg_odds50p_rankband_s1_normal_band_札幌_ZI.csv
  （元ファイル名 = master_{地域}_{指数名}.csv.gz の「{地域}_{指数名}」。family の名前は shisu/families.py の台帳）
テーブル名: 成績表のファイル名（拡張子なし）を SQLite 用に安全化したもの。接頭辞 foragg_ は1つだけ。
  （旧版は "foragg_" + ファイル名 で foragg_foragg_... になり、画面の family 判定が壊れていた）
family・オッズ帯・指数名などはテーブル名から推測せず、__tables_meta__ と unified_all の列に持たせる。
"""
from __future__ import annotations

import os
import re

from . import families as _families
from .indicators import parse_source_name
from .keys import SCHEMES
from .odds import LEGACY_MODES, ODDS_MODES, mode_prefix

FORAGG_PREFIX = "foragg_"


def file_stem(path: str) -> str:
    """拡張子（.csv / .csv.gz）を除いたファイル名。"""
    name = os.path.basename(str(path))
    for ext in (".csv.gz", ".csv"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return os.path.splitext(name)[0]


def foragg_tag(family: str, mode: str = "normal", scheme: str = "") -> str:
    return f"{mode_prefix(mode)}{('rankband_' + scheme + '_') if scheme else ''}{family}"


def agg_filename(family: str, mode: str, scheme: str, source_stem: str) -> str:
    return f"{FORAGG_PREFIX}{foragg_tag(family, mode, scheme)}_{source_stem}.csv"


def parse_foragg_stem(stem: str) -> dict | None:
    """ファイル名（拡張子なし）→ {mode, scheme, family, source, region, indicator}。

    オッズ接頭辞が無ければ mode='normal'。規約に合わない名前・旧仕様（odds5p など）は None。
    """
    s = stem
    if not s.startswith(FORAGG_PREFIX):
        return None
    s = s[len(FORAGG_PREFIX):]
    mode = "normal"
    for m in sorted(ODDS_MODES[1:] + LEGACY_MODES, key=len, reverse=True):
        if s.startswith(m + "_"):
            mode = m
            s = s[len(m) + 1:]
            break
    if mode in LEGACY_MODES:
        return None
    scheme = ""
    m = re.match(r"rankband_(s\d+)_", s)
    if m:
        scheme = m.group(1)
        s = s[m.end():]
        if scheme not in SCHEMES:
            return None
    # family の名前は台帳（初期セット＋読み込んだ selected_families.json / families.json）から長い順に当てる
    family = next((f for f in sorted(_families.known_names(), key=len, reverse=True) if s.startswith(f + "_")), None)
    if family is None:
        return None
    if _families.get(family).uses_schemes != bool(scheme):
        return None
    source = s[len(family) + 1:]
    if not source:
        return None
    region, indicator = parse_source_name(source)
    return {"mode": mode, "scheme": scheme, "family": family, "source": source,
            "region": region, "indicator": indicator}


def sanitize_identifier(name: str) -> str:
    """SQLite のテーブル名用。\\w（日本語を含む）以外を '_' に。先頭が数字なら t_ を付ける。"""
    s = re.sub(r"[^\w]", "_", name)
    if re.match(r"^\d", s):
        s = "t_" + s
    return s


def table_name_for_foragg(path_or_stem: str) -> str:
    t = sanitize_identifier(file_stem(path_or_stem))
    return t if t.startswith(FORAGG_PREFIX) else FORAGG_PREFIX + t


ENTRIES_PREFIX = "entries_"
XJOIN_PREFIX = "entries_xjoin_"
ENTRIES_ALL = "entries_all"
_RESERVED_ENTRY_STEMS = {"all"}


def table_name_for_entries(path_or_stem: str) -> str:
    t = sanitize_identifier(file_stem(path_or_stem))
    if t.startswith(ENTRIES_PREFIX):
        t = t[len(ENTRIES_PREFIX):]
    if t in _RESERVED_ENTRY_STEMS or t.startswith("xjoin_"):
        t = "f_" + t
    return ENTRIES_PREFIX + t


def xjoin_table_for(entries_table: str) -> str:
    return XJOIN_PREFIX + entries_table[len(ENTRIES_PREFIX):]
