"""成績表ファイルの名前と DB テーブル名の規約。

成績表（aggregate_stats_ver2.py の出力。2026-11-10〜 master 1本につき1ファイル）:
  agg_csv/agg_master_{元ファイル名}.csv.gz
  例: agg_master_主５場（中山、東京、中京、京都、阪神）_タイムM.csv.gz
  1行 = 1結合データ。family・オッズ帯・スキームの違う成績表を縦に並べ、
  family, odds_mode, rank_scheme, indicator, region, テーブル名 の列で区別する（shisu/aggstore.py）
  （元ファイル名 = master_{地域}_{指数名}.csv.gz の「{地域}_{指数名}」。family の名前は shisu/families.py の台帳）
旧来の成績表（2026-11-09 までの出力。family × スキーム × オッズ帯ごとに1本。build_db は今も読める）:
  agg_csv/foragg_{オッズ接頭辞}{rankband_スキーム_}{family}_{元ファイル名}.csv
  例: foragg_normal_主５場（中山、東京、中京、京都、阪神）_タイムM.csv
      foragg_odds50p_rankband_s1_normal_band_札幌_ZI.csv
テーブル名: 旧来の成績表のファイル名（拡張子なし）を SQLite 用に安全化したもの。接頭辞 foragg_ は1つだけ。
  （旧版は "foragg_" + ファイル名 で foragg_foragg_... になり、画面の family 判定が壊れていた）
  今は DB に1表ずつのテーブルを作らず、unified_all の「テーブル名」列と __tables_meta__ に同じ名前を持たせる
  （xjoin・画面のリンク・表の見出しは旧来と同じ名前になる）
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
    # （名前は英数字と _ だけなので、「_」の位置で切った先頭部分を長い順に台帳と照らせば同じ結果になる。
    #  family が数千あり成績表が数十万本でも、1本あたり「_」の数だけ調べればよい）
    known = _known_set()
    cuts = [i for i, ch in enumerate(s) if ch == "_"]
    family = next((s[:i] for i in reversed(cuts) if s[:i] in known), None)
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


def _known_set() -> frozenset[str]:
    names = _families.known_names()
    global _KNOWN_CACHE
    if _KNOWN_CACHE is None or _KNOWN_CACHE[0] != names:
        _KNOWN_CACHE = (names, frozenset(names))
    return _KNOWN_CACHE[1]


_KNOWN_CACHE: tuple[tuple[str, ...], frozenset[str]] | None = None

AGG_MASTER_PREFIX = "agg_master_"
AGG_MASTER_SUFFIX = ".csv.gz"


def agg_master_filename(source_stem: str) -> str:
    """master 1本（{地域}_{指数名}）の成績表のファイル名。"""
    return f"{AGG_MASTER_PREFIX}{source_stem}{AGG_MASTER_SUFFIX}"


def parse_agg_master_name(path_or_name: str) -> str | None:
    """agg_master_{元ファイル名}.csv.gz → 元ファイル名（{地域}_{指数名}）。規約外なら None。"""
    name = os.path.basename(str(path_or_name))
    if not (name.startswith(AGG_MASTER_PREFIX) and name.endswith(AGG_MASTER_SUFFIX)):
        return None
    source = name[len(AGG_MASTER_PREFIX):-len(AGG_MASTER_SUFFIX)]
    return source or None


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
