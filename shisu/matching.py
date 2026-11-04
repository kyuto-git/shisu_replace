"""出馬表の各馬を unified_all と照合する（build_db の xjoin 事前構築と app の通常パスで共通）。

照合は 結合データ・family・indicator の完全一致のみ。
（旧版の通常パスは LIKE 'key%' や '場所×芝・ダ×距離%' に落ちて無関係な行を拾っていた）
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping

from .csvio import norm_text
from .keys import ALL_FAMILIES, entry_key_candidates
from .wedge import WEDGE_COLUMNS

UNIFIED = "unified_all"

# unified_all から取り出してヒット行に載せる列
STAT_COLUMNS = ("件数", "勝率_val", "勝率", "連対率_val", "連対率", "複勝率_val", "複勝率",
                "2着率_val", "2着率", "3着率_val", "3着率", "掲示板率_val", "掲示板率",
                "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値") + WEDGE_COLUMNS
META_COLUMNS = ("テーブル名", "family", "odds_mode", "rank_scheme", "indicator", "region")
# ヒット行（xjoin テーブルの列もこの順）
HORSE_COLUMNS = ("日付", "レースID(新)", "レースID(新/馬番無)", "場 R", "馬番", "枠番", "枠番グループ", "騎手", "馬名")
HIT_COLUMNS = ("source_table",) + HORSE_COLUMNS + ("base", "key") + META_COLUMNS + STAT_COLUMNS

DEFAULT_LIMIT = 50
REQUIRED_UNIFIED_COLUMNS = ("結合データ", "family", "indicator", "odds_mode", "rank_scheme", "テーブル名") + WEDGE_COLUMNS


def unified_status(conn: sqlite3.Connection) -> str:
    """unified_all の状態: 'ok' / 'missing'（無い）/ 'old'（古い build_db で作った列構成。WEDGE 列が無いものも含む）。"""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (UNIFIED,)).fetchone() is None:
        return "missing"
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{UNIFIED}")')}
    return "ok" if set(REQUIRED_UNIFIED_COLUMNS) <= cols else "old"


def indicator_bases(columns: Iterable[str]) -> list[str]:
    """出馬表テーブルの列から指数名を拾う（X_偏差値 と X_順位 の両方がある X）。"""
    cols = set(columns)
    return sorted({c[: -len("_偏差値")] for c in cols
                   if c.endswith("_偏差値") and (c[: -len("_偏差値")] + "_順位") in cols})


def rank_text(v) -> str:
    """順位を '13' の形に（旧形式の '13.0' も受ける）。数値でなければ ''。"""
    t = norm_text(v)
    try:
        f = float(t)
    except ValueError:
        return ""
    return str(int(f)) if f == f and f >= 1 else ""


def entry_fields(row: Mapping, base: str) -> dict:
    """出馬表の1行（build_db で正規化済み）から、キーの部品を取り出す。"""
    return {
        "場所": norm_text(row.get("場所")),
        "芝・ダ": norm_text(row.get("芝・ダ")),
        "距離": norm_text(row.get("距離")),
        "芝(内・外)": norm_text(row.get("芝(内・外)")),
        "重量種別": norm_text(row.get("重量種別")),
        "クラス名": norm_text(row.get("クラス名")),
        "枠番グループ": norm_text(row.get("枠番グループ")),
        "騎手": norm_text(row.get("騎手")),
        "順位": rank_text(row.get(f"{base}_順位")),
        "偏差値帯": norm_text(row.get(f"{base}_偏差値帯")),
    }


def query_unified(conn: sqlite3.Connection, key: str, family: str, indicator: str,
                  limit: int = DEFAULT_LIMIT) -> list[dict]:
    cols = ", ".join(f'"{c}"' for c in META_COLUMNS + STAT_COLUMNS)
    sql = (f'SELECT {cols} FROM "{UNIFIED}" WHERE "結合データ" = ? AND "family" = ? AND "indicator" = ? '
           f'ORDER BY "複勝率_val" DESC, "連対率_val" DESC LIMIT ?')
    cur = conn.execute(sql, (key, family, indicator, limit))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def match_entry_row(conn: sqlite3.Connection, row: Mapping, bases: Iterable[str], source_table: str,
                    families: Iterable[str] = ALL_FAMILIES, cache: dict | None = None,
                    limit: int = DEFAULT_LIMIT) -> list[dict]:
    """出馬表1頭ぶんのヒット行（HIT_COLUMNS の dict）を返す。"""
    cache = {} if cache is None else cache
    families = tuple(families)
    horse = {c: norm_text(row.get(c)) for c in HORSE_COLUMNS}
    out: list[dict] = []
    for base in bases:
        fields = entry_fields(row, base)
        for fam, cands in entry_key_candidates(fields, families).items():
            for key, _scheme in cands:
                ck = (key, fam, base)
                hits = cache.get(ck)
                if hits is None:
                    hits = query_unified(conn, key, fam, base, limit)
                    cache[ck] = hits
                for h in hits:
                    rec = {"source_table": source_table, **horse, "base": base, "key": key}
                    rec.update(h)
                    out.append(rec)
    return out
