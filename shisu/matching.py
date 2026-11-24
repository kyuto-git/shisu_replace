"""出馬表の各馬を unified_all と照合する（build_db の xjoin 事前構築と app の通常パスで共通）。

照合は 結合データ・family・indicator の完全一致のみ。
（旧版の通常パスは LIKE 'key%' や '場所×芝・ダ×距離%' に落ちて無関係な行を拾っていた）
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping

from . import families as _families
from .conditions import CONDITION_COLUMNS
from .csvio import norm_text
from .families import entry_key_candidates
from .indicators import CONDITION_ONLY
from .classify import FIELD_COLUMNS as CLASSIFY_COLUMNS
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
# 区分の判定（classify の SQL 版）が読む列（件数・勝率_val・連対率_val・複勝率_val・掲示板率_val・回収値・WEDGE）も要る:
# SQLite は、表に無い列名を二重引用符で書くと文字列として読む（"勝率_val" >= 40.0 が真になる）ので、列のそろっていない
# unified_all を「ok」にしない
REQUIRED_UNIFIED_COLUMNS = (("結合データ", "family", "indicator", "odds_mode", "rank_scheme", "テーブル名") + WEDGE_COLUMNS
                            + tuple(c for c in CLASSIFY_COLUMNS.values() if c not in WEDGE_COLUMNS))


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


def match_bases(columns: Iterable[str], families: Iterable[str] | None = None) -> list[str]:
    """照合する「指数」の名前: 出馬表の指数（indicator_bases）と、指数を使わない family（tail="NONE"）があれば
    CONDITION_ONLY（条件のみ。指数に依らないので、1頭につき1回だけ照合する）。"""
    bases = indicator_bases(columns)
    names = tuple(families) if families is not None else _families.active_names()
    if any(_families.is_no_index(n) for n in names):
        bases.append(CONDITION_ONLY)
    return bases


def rank_text(v) -> str:
    """順位を '13' の形に（旧形式の '13.0' も受ける）。数値でなければ ''。"""
    t = norm_text(v)
    try:
        f = float(t)
    except ValueError:
        return ""
    return str(int(f)) if f == f and f >= 1 else ""


PART_FIELDS = ("場所", "芝・ダ", "距離", "距離_raw", "芝(内・外)", "重量種別", "クラス名", "枠番グループ", "騎手") + CONDITION_COLUMNS


def is_jump_row(row: Mapping) -> bool:
    """障害レースの行か（build_db.normalize_entries_df が 芝・ダ を「障」にする）。"""
    return norm_text(row.get("芝・ダ")) == "障"


def entry_fields(row: Mapping, base: str) -> dict:
    """出馬表の1行（build_db で正規化済み。22条件の列も持つ）から、キーの部品を取り出す。"""
    out = {c: norm_text(row.get(c)) for c in PART_FIELDS}
    if base == CONDITION_ONLY:           # 条件のみの family は順位・偏差値帯を使わない
        out["順位"] = out["偏差値帯"] = ""
        return out
    out["順位"] = rank_text(row.get(f"{base}_順位"))
    out["偏差値帯"] = norm_text(row.get(f"{base}_偏差値帯"))
    return out


# 照合結果のキャッシュの上限（件数と、持っている行数の両方）。family が数千・指数が数十だと1頭で数十万回の照合になるので、
# 上限は「照合1回ごと」に見る（1頭を回し終えてから見ると、その間は無制限に膨らむ）
CACHE_MAX_KEYS = 20_000
CACHE_MAX_ROWS = 50_000
_HIT_SELECT = ", ".join(f'"{c}"' for c in META_COLUMNS + STAT_COLUMNS)
_HIT_SQL = (f'SELECT {_HIT_SELECT} FROM "{UNIFIED}" WHERE "結合データ" = ? AND "family" = ? AND "indicator" = ? '
            f'ORDER BY "複勝率_val" DESC, "連対率_val" DESC LIMIT ?')


class HitCache(dict):
    """照合結果のキャッシュ: (結合データ, family, 指数) → ヒット行（META_COLUMNS + STAT_COLUMNS の値のタプルのリスト）。
    件数が max_keys、または持っている行数が max_rows を超えそうになったら空にしてから入れる（どんな family 数・指数数でも
    この大きさを超えない）。同じレースの馬どうしで同じキーを引き直すのを省くためのもので、空にしても結果は変わらない。"""

    def __init__(self, max_keys: int = CACHE_MAX_KEYS, max_rows: int = CACHE_MAX_ROWS):
        super().__init__()
        self.max_keys = max(1, int(max_keys))
        self.max_rows = max(1, int(max_rows))
        self.rows = 0

    def put(self, key: tuple, hits: list) -> None:
        if len(self) >= self.max_keys or self.rows + len(hits) > self.max_rows:
            self.clear()
        self[key] = hits
        self.rows += len(hits)

    def clear(self) -> None:
        super().clear()
        self.rows = 0


def _cache_put(cache: dict, key: tuple, hits: list) -> None:
    if isinstance(cache, HitCache):
        cache.put(key, hits)
        return
    if len(cache) >= CACHE_MAX_KEYS:        # ふつうの dict を渡された場合も、件数の上限は守る
        cache.clear()
    cache[key] = hits


def query_hit_rows(conn: sqlite3.Connection, key: str, family: str, indicator: str,
                   limit: int = DEFAULT_LIMIT) -> list[tuple]:
    """unified_all の完全一致の行（META_COLUMNS + STAT_COLUMNS の順の値のタプル）。"""
    return conn.execute(_HIT_SQL, (key, family, indicator, limit)).fetchall()


def query_unified(conn: sqlite3.Connection, key: str, family: str, indicator: str,
                  limit: int = DEFAULT_LIMIT) -> list[dict]:
    names = META_COLUMNS + STAT_COLUMNS
    return [dict(zip(names, tuple(r))) for r in query_hit_rows(conn, key, family, indicator, limit)]


def iter_hit_rows(conn: sqlite3.Connection, row: Mapping, bases: Iterable[str], source_table: str,
                  families: Iterable[str] | None = None, cache: dict | None = None,
                  limit: int = DEFAULT_LIMIT):
    """出馬表1頭ぶんのヒット行を、HIT_COLUMNS の順の値のタプルで1行ずつ返す（ジェネレータ）。
    1頭ぶんをまとめて持たないので、family・指数が多くてもメモリは増えない（build_db の xjoin はこれをそのまま INSERT する）。
    bases は指数の名前（match_bases の結果）。CONDITION_ONLY が入っていれば、指数を使わない family をその名前で1回だけ照合する。
    cache は HitCache（上限つき）。省略するとこの1頭の中だけのキャッシュ。"""
    cache = HitCache() if cache is None else cache
    families = tuple(families) if families is not None else _families.active_names()
    if is_jump_row(row):              # 障害レース: 過去側は集計していない（コースを含まない family でも照合しない）
        return
    horse = (source_table,) + tuple(norm_text(row.get(c)) for c in HORSE_COLUMNS)
    # 指数を使う family は指数（base）ごとに、指数を使わない family（条件のみ）は base = CONDITION_ONLY で1回だけ照合する
    indexed, plain = _families.split_no_index(families)
    for base in bases:
        fams = plain if base == CONDITION_ONLY else indexed
        if not fams:
            continue
        fields = entry_fields(row, base)
        for fam, cands in entry_key_candidates(fields, fams).items():
            for key, _scheme in cands:
                ck = (key, fam, base)
                hits = cache.get(ck)
                if hits is None:
                    hits = query_hit_rows(conn, key, fam, base, limit)
                    _cache_put(cache, ck, hits)
                if hits:
                    head = horse + (base, key)
                    for h in hits:
                        yield head + tuple(h)


def match_entry_row(conn: sqlite3.Connection, row: Mapping, bases: Iterable[str], source_table: str,
                    families: Iterable[str] | None = None, cache: dict | None = None,
                    limit: int = DEFAULT_LIMIT) -> list[dict]:
    """出馬表1頭ぶんのヒット行（HIT_COLUMNS の dict）を返す（画面のその場照合用。中身は iter_hit_rows と同じ）。"""
    return [dict(zip(HIT_COLUMNS, t)) for t in iter_hit_rows(conn, row, bases, source_table, families, cache, limit)]
