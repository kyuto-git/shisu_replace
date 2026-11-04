# -*- coding: utf-8 -*-
"""SQLite（racing.db）を構築する。

--mode foragg : agg_csv/*.csv（集計結果）→ テーブル foragg_*（1ファイル1テーブル）＋ unified_all（縦結合）
--mode entries: entries_out/*.csv（偏差値付き出馬表）→ entries_*, entries_all, entries_xjoin_*（照合結果の事前計算）
--mode both   : 両方（既定）

テーブル
  __tables_meta__ : table_name, source_file, kind(foragg/entries/xjoin), family, odds_mode, rank_scheme,
                    indicator, region, n_rows。画面はテーブル名を解析せず、この表を見る
  unified_all     : 全 foragg_* の縦結合。family / odds_mode / rank_scheme / indicator / region 列付き
  __facets__      : 画面の選択肢（場所・芝・ダ・距離・芝(内・外)）
  entries_*       : 出馬表（キー部品を正規化済み。日付・レースID(新/馬番無) を付与）
  entries_xjoin_* : 出馬表の各馬 × 指数 × family × キー候補 と unified_all の完全一致照合の結果

*_val（勝率・連対率・複勝率・2着率・3着率）は、集計CSVの文字列 '頻度%（推定%）' の括弧内（推定値）で
作り直す。掲示板率_val は頻度のまま。率はすべて 0–100。

--prune は「今回作り直した種類」の古いテーブルだけを消す（--mode entries --prune で foragg_* は消えない）。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from shisu import config
from shisu.csvio import norm_series, read_csv_any
from shisu.matching import HIT_COLUMNS, STAT_COLUMNS, UNIFIED, indicator_bases, match_entry_row
from shisu.naming import (ENTRIES_ALL, ENTRIES_PREFIX, FORAGG_PREFIX, XJOIN_PREFIX, parse_foragg_stem,
                          table_name_for_entries, table_name_for_foragg, xjoin_table_for)
from shisu.normalize import (apply_age_class_rule, apply_distance_rules, class_series, distance_series,
                             entry_inner_outer, frame_group_series, infer_entry_weight, race_date,
                             race_id_without_horse, weight_series)

META = "__tables_meta__"
FACETS = "__facets__"
RATE_NAMES = ("勝率", "連対率", "複勝率", "2着率", "3着率")
FLOAT_COLUMNS = ("掲示板率_val", "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値")
META_COLUMNS = ("table_name", "source_file", "kind", "family", "odds_mode", "rank_scheme", "indicator",
                "region", "n_rows")
UNIFIED_TEXT = ("場所", "芝・ダ", "距離", "芝(内・外)", "結合データ")
UNIFIED_STATS = ("件数", "勝率", "連対率", "複勝率", "2着率", "3着率", "掲示板率",
                 "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値",
                 "提案勝率", "提案連対率", "提案複勝率", "提案2着率", "提案3着率",
                 "勝率_val", "連対率_val", "複勝率_val", "2着率_val", "3着率_val", "掲示板率_val")
UNIFIED_META = ("family", "odds_mode", "rank_scheme", "indicator", "region", "テーブル名")
FACET_COLUMNS = ("場所", "芝・ダ", "距離", "芝(内・外)")
ENTRIES_ALL_COLUMNS = ("日付", "レースID(新)", "レースID(新/馬番無)", "場 R", "場所", "芝・ダ", "距離",
                       "芝(内・外)", "クラス名", "重量種別", "頭数", "枠番", "枠番グループ", "馬番", "馬名",
                       "騎手", "source_table")
# 出馬表で必ず持たせる列（無ければ空で作る。SQL が存在しない列を参照しないように）
ENTRIES_REQUIRED = ("日付", "レースID(新)", "レースID(新/馬番無)", "場 R", "レース名", "場所", "芝・ダ",
                    "距離", "距離_raw", "芝(内・外)", "クラス名", "クラス名_raw", "重量種別", "頭数",
                    "枠番", "枠番グループ", "馬番", "馬名", "騎手")
_SQL_TYPES = {"件数": "INTEGER", **{c: "REAL" for c in STAT_COLUMNS if c.endswith("_val")},
              **{c: "REAL" for c in FLOAT_COLUMNS}}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# メタ情報
# ---------------------------------------------------------------------------
def ensure_meta(con: sqlite3.Connection) -> None:
    con.execute(f"CREATE TABLE IF NOT EXISTS {META} (table_name TEXT PRIMARY KEY, source_file TEXT)")
    have = {r[1] for r in con.execute(f"PRAGMA table_info({META})")}
    for c in META_COLUMNS:
        if c not in have:
            con.execute(f"ALTER TABLE {META} ADD COLUMN {q(c)} {'INTEGER' if c == 'n_rows' else 'TEXT'}")


def replace_meta(con: sqlite3.Connection, kinds: tuple[str, ...], rows: list[dict]) -> None:
    con.execute(f"DELETE FROM {META} WHERE kind IN ({','.join('?' * len(kinds))}) OR kind IS NULL", kinds)
    con.executemany(
        f"INSERT OR REPLACE INTO {META} ({','.join(q(c) for c in META_COLUMNS)}) VALUES ({','.join('?' * len(META_COLUMNS))})",
        [tuple(r.get(c) for c in META_COLUMNS) for r in rows])


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# ---------------------------------------------------------------------------
# 集計結果 → foragg_* / unified_all
# ---------------------------------------------------------------------------
_EST_RE = re.compile(r"^\s*([\d.]+)%\s*(?:[(（]\s*([\d.]+)%\s*[)）])?")


def estimate_percent(series: pd.Series) -> pd.Series:
    """'45.00%（47.10%）' → 47.10（括弧内の推定値）。括弧が無ければ先頭の値。取れなければ NaN。"""
    m = series.astype(str).str.extract(_EST_RE)
    return pd.to_numeric(m[1].where(m[1].notna(), m[0]), errors="coerce")


def prepare_agg_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["件数"] = pd.to_numeric(df["件数"], errors="coerce").astype("Int64")
    for c in FLOAT_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for r in RATE_NAMES:
        if r in df.columns:
            df[f"{r}_val"] = estimate_percent(df[r])
    return df


def load_agg_tables(con: sqlite3.Connection, agg_dir: Path) -> list[dict]:
    files = sorted(Path(agg_dir).glob("*.csv"))
    if not files:
        print(f"[foragg] 集計CSVがありません: {agg_dir}（aggregate_stats_ver2.py を先に実行）")
        return []
    loaded, skipped, empty = [], 0, 0
    for p in files:
        info = parse_foragg_stem(p.stem)
        if info is None:
            print(f"[foragg] SKIP（ファイル名が規約外・旧仕様）: {p.name}", file=sys.stderr)
            skipped += 1
            continue
        df = read_csv_any(p)
        if "結合データ" not in df.columns or "件数" not in df.columns:
            print(f"[foragg] SKIP（集計結果ではありません）: {p.name}", file=sys.stderr)
            skipped += 1
            continue
        if df.empty:
            empty += 1
            continue
        df = prepare_agg_frame(df)
        tname = table_name_for_foragg(p.name)
        df.to_sql(tname, con, if_exists="replace", index=False)
        loaded.append({"table_name": tname, "source_file": p.name, "kind": "foragg",
                       "family": info["family"], "odds_mode": info["mode"], "rank_scheme": info["scheme"],
                       "indicator": info["indicator"], "region": info["region"], "n_rows": int(len(df)),
                       "_cols": set(df.columns)})
    print(f"[foragg] loaded {len(loaded)} tables (0件 {empty} / skip {skipped})")
    return loaded


def rebuild_unified_all(con: sqlite3.Connection, loaded: list[dict]) -> None:
    con.execute(f"DROP TABLE IF EXISTS {UNIFIED}")
    coldefs = ([f"{q(c)} TEXT" for c in UNIFIED_TEXT]
               + [f"{q(c)} {_SQL_TYPES.get(c, 'TEXT')}" for c in UNIFIED_STATS]
               + [f"{q(c)} TEXT" for c in UNIFIED_META])
    con.execute(f"CREATE TABLE {UNIFIED} ({', '.join(coldefs)})")
    data_cols = UNIFIED_TEXT + UNIFIED_STATS
    for t in loaded:
        sel = [q(c) if c in t["_cols"] else f"NULL AS {q(c)}" for c in data_cols]
        sel += ["? AS family", "? AS odds_mode", "? AS rank_scheme", "? AS indicator", "? AS region", "? AS テーブル名"]
        con.execute(f"INSERT INTO {UNIFIED} ({', '.join(q(c) for c in data_cols + UNIFIED_META)}) "
                    f"SELECT {', '.join(sel)} FROM {q(t['table_name'])}",
                    (t["family"], t["odds_mode"], t["rank_scheme"], t["indicator"], t["region"], t["table_name"]))
    con.execute(f'CREATE INDEX idx_unified_match ON {UNIFIED}("結合データ", "family", "indicator")')
    con.execute(f'CREATE INDEX idx_unified_table ON {UNIFIED}("テーブル名")')
    n = con.execute(f"SELECT COUNT(*) FROM {UNIFIED}").fetchone()[0]
    print(f"[foragg] unified_all: {n} rows")


def rebuild_facets(con: sqlite3.Connection) -> None:
    con.execute(f"DROP TABLE IF EXISTS {FACETS}")
    con.execute(f"CREATE TABLE {FACETS} (col TEXT, val TEXT)")
    for c in FACET_COLUMNS:
        con.execute(f"INSERT INTO {FACETS} SELECT ?, v FROM (SELECT DISTINCT {q(c)} AS v FROM {UNIFIED} "
                    f"WHERE {q(c)} IS NOT NULL AND {q(c)} <> '')", (c,))


# ---------------------------------------------------------------------------
# 出馬表
# ---------------------------------------------------------------------------
def _first_nonempty(df: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    out = pd.Series("", index=df.index, dtype=object)
    for n in names:
        if n in df.columns:
            v = norm_series(df[n])
            out = out.where(out != "", v)
    return out


def normalize_entries_df(raw: pd.DataFrame) -> pd.DataFrame:
    """出馬表（calc_entry_hensachi の出力）を、キーの部品が過去側と同じ表記になるよう正規化する。"""
    df = raw.copy()
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]
    rid = norm_series(df["レースID(新)"]) if "レースID(新)" in df.columns else pd.Series("", index=df.index)
    given = df["レースID(新/馬番無)"] if "レースID(新/馬番無)" in df.columns else None
    df["レースID(新)"] = rid
    df["レースID(新/馬番無)"] = race_id_without_horse(rid, given)
    df["日付"] = _first_nonempty(df, ("日付",)).where(lambda s: s != "", race_date(rid))
    df["場 R"] = _first_nonempty(df, ("場 R", "場R"))
    df["レース名"] = _first_nonempty(df, ("レース名",))
    df["場所"] = _first_nonempty(df, ("場所",))
    df["芝・ダ"] = _first_nonempty(df, ("芝・ダ", "芝ダ"))
    df["距離_raw"] = _first_nonempty(df, ("距離",))
    if "外回り" in raw.columns:
        inner = pd.Series([entry_inner_outer(p, s, g) for p, s, g in zip(df["場所"], df["芝・ダ"], raw["外回り"])],
                          index=df.index, dtype=object)
    elif "芝(内・外)" in raw.columns:
        inner = norm_series(raw["芝(内・外)"])
    else:
        inner = pd.Series([entry_inner_outer(p, s, "") for p, s in zip(df["場所"], df["芝・ダ"])],
                          index=df.index, dtype=object)
    dist, inner = apply_distance_rules(df["場所"], df["芝・ダ"], distance_series(df["距離_raw"]), inner)
    df["距離"] = dist
    df["芝(内・外)"] = inner
    cls_raw = _first_nonempty(df, ("クラス名", "クラス"))
    df["クラス名_raw"] = cls_raw
    cls = class_series(cls_raw)
    if "重量種別" in raw.columns and (norm_series(raw["重量種別"]) != "").any():
        weight = weight_series(raw["重量種別"])
    else:
        age = _first_nonempty(df, ("年限定",))
        weight = pd.Series([infer_entry_weight(rn, a, c) for rn, a, c in zip(df["レース名"], age, cls)],
                           index=df.index, dtype=object)
    df["重量種別"] = weight
    df["クラス名"] = apply_age_class_rule(cls, weight)
    df["頭数"] = _first_nonempty(df, ("頭数", "頭"))
    df["枠番"] = _first_nonempty(df, ("枠番", "枠"))
    df["枠番グループ"] = frame_group_series(df["枠番"], df["頭数"])
    df["馬番"] = _first_nonempty(df, ("馬番",))
    df["馬名"] = _first_nonempty(df, ("馬名", "馬名S", "馬名1", "出走馬名"))
    df["騎手"] = _first_nonempty(df, ("騎手", "騎手名"))
    for c in ENTRIES_REQUIRED:
        if c not in df.columns:
            df[c] = ""
    return df


def load_entries_tables(con: sqlite3.Connection, entries_dir: Path) -> list[dict]:
    files = sorted(Path(entries_dir).glob("*.csv"))
    if not files:
        print(f"[entries] 出馬表CSVがありません: {entries_dir}")
        return []
    loaded = []
    for p in files:
        df = normalize_entries_df(read_csv_any(p))
        tname = table_name_for_entries(p.name)
        df.to_sql(tname, con, if_exists="replace", index=False)
        con.execute(f'CREATE INDEX IF NOT EXISTS {q("idx_" + tname + "_race")} ON {q(tname)}("レースID(新/馬番無)")')
        con.execute(f'CREATE INDEX IF NOT EXISTS {q("idx_" + tname + "_date")} ON {q(tname)}("日付", "場所")')
        bases = indicator_bases(df.columns)
        loaded.append({"table_name": tname, "source_file": p.name, "kind": "entries", "n_rows": int(len(df)),
                       "indicator": ",".join(bases)})
        print(f"[entries] loaded: {p.name} -> {tname} ({len(df)} rows, 指数 {len(bases)})")
    return loaded


def build_entries_all(con: sqlite3.Connection, loaded: list[dict]) -> None:
    con.execute(f"DROP TABLE IF EXISTS {ENTRIES_ALL}")
    con.execute(f"CREATE TABLE {ENTRIES_ALL} ({', '.join(q(c) + ' TEXT' for c in ENTRIES_ALL_COLUMNS)})")
    for t in loaded:
        cols = [q(c) for c in ENTRIES_ALL_COLUMNS[:-1]]
        con.execute(f"INSERT INTO {ENTRIES_ALL} SELECT {', '.join(cols)}, ? FROM {q(t['table_name'])}",
                    (t["table_name"],))


def build_xjoin(con: sqlite3.Connection, entries_table: str, families=None) -> dict:
    """entries_table の全馬を unified_all と照合して entries_xjoin_* に保存する。"""
    from shisu.keys import ALL_FAMILIES
    families = tuple(families or ALL_FAMILIES)
    xname = xjoin_table_for(entries_table)
    con.execute(f"DROP TABLE IF EXISTS {q(xname)}")
    coldefs = [f"{q(c)} {_SQL_TYPES.get(c, 'TEXT')}" for c in HIT_COLUMNS]
    con.execute(f"CREATE TABLE {q(xname)} ({', '.join(coldefs)})")
    cur = con.execute(f"SELECT * FROM {q(entries_table)}")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    bases = indicator_bases(names)
    ins = (f"INSERT INTO {q(xname)} ({', '.join(q(c) for c in HIT_COLUMNS)}) "
           f"VALUES ({', '.join('?' * len(HIT_COLUMNS))})")
    cache: dict = {}
    total = 0
    batch = []
    for i, row in enumerate(rows, 1):
        for h in match_entry_row(con, row, bases, entries_table, families, cache):
            batch.append(tuple(h.get(c) for c in HIT_COLUMNS))
        if len(batch) >= 5000 or i == len(rows):
            con.executemany(ins, batch)
            total += len(batch)
            batch = []
        if i % 200 == 0 or i == len(rows):
            print(f"[xjoin] {xname}: {i}/{len(rows)} 頭, hits {total}", flush=True)
    con.execute(f'CREATE INDEX {q("idx_" + xname + "_race")} ON {q(xname)}("レースID(新/馬番無)")')
    return {"table_name": xname, "source_file": entries_table, "kind": "xjoin", "n_rows": total,
            "indicator": ",".join(bases)}


# ---------------------------------------------------------------------------
# 掃除
# ---------------------------------------------------------------------------
def prune(con: sqlite3.Connection, kinds: set[str], keep: set[str]) -> None:
    existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    prefixes = []
    if "foragg" in kinds:
        prefixes.append(FORAGG_PREFIX)
    if "entries" in kinds:
        prefixes.append(ENTRIES_PREFIX)   # entries_xjoin_* も含む
    stale = sorted(t for t in existing if any(t.startswith(p) for p in prefixes) and t not in keep)
    for t in stale:
        con.execute(f"DROP TABLE IF EXISTS {q(t)}")
        con.execute(f"DELETE FROM {META} WHERE table_name = ?", (t,))
    print(f"[prune] dropped {len(stale)} tables" + (f" (例: {stale[0]})" if stale else ""))


def parse_cli(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["both", "foragg", "entries"], default="both")
    p.add_argument("--fresh", action="store_true", help="DBファイルを削除して作り直す")
    p.add_argument("--prune", action="store_true", help="今回作り直した種類の古いテーブルを削除する")
    p.add_argument("--no-xjoin", action="store_true", help="entries_xjoin_* を作らない（画面はその場で照合する）")
    p.add_argument("--agg-dir", "--foragg-dir", dest="agg_dir", default=str(config.agg_dir()),
                   help="集計CSVのディレクトリ（既定 agg_csv/）")
    p.add_argument("--entries-dir", default=str(config.entries_out_dir()))
    p.add_argument("--db-path", default=str(config.db_path()), help="既定は環境変数 DB_PATH か racing.db")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_cli(argv)
    db = Path(args.db_path)
    if args.fresh:
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(db) + suffix)
            if f.exists():
                f.unlink()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    ensure_meta(con)
    keep: set[str] = set()
    kinds: set[str] = set()

    if args.mode in ("both", "foragg"):
        kinds.add("foragg")
        loaded = load_agg_tables(con, Path(args.agg_dir))
        rebuild_unified_all(con, loaded)
        rebuild_facets(con)
        replace_meta(con, ("foragg",), loaded)
        keep |= {t["table_name"] for t in loaded}
        con.commit()
        if args.mode == "foragg" and con.execute(f"SELECT 1 FROM {META} WHERE kind='xjoin' LIMIT 1").fetchone():
            print("[WARN] unified_all を作り直しました。entries_xjoin_* は古い照合結果のままなので "
                  "--mode entries も実行してください", file=sys.stderr)

    if args.mode in ("both", "entries"):
        kinds.add("entries")
        entries = load_entries_tables(con, Path(args.entries_dir))
        build_entries_all(con, entries)
        keep |= {t["table_name"] for t in entries} | {ENTRIES_ALL}
        meta_rows = list(entries)
        has_unified = table_exists(con, UNIFIED)
        for t in entries:
            xname = xjoin_table_for(t["table_name"])
            if args.no_xjoin or not has_unified:
                con.execute(f"DROP TABLE IF EXISTS {q(xname)}")   # 古い照合結果を残さない
                continue
            meta_rows.append(build_xjoin(con, t["table_name"]))
            keep.add(xname)
            con.commit()
        if not has_unified:
            print("[WARN] unified_all がありません。--mode foragg を先に実行してください（xjoin は作りません）",
                  file=sys.stderr)
        replace_meta(con, ("entries", "xjoin"), meta_rows)

    if args.prune:
        prune(con, kinds, keep)
    con.commit()
    con.close()
    print(f"OK: {db} (mode={args.mode}, prune={args.prune}, xjoin={not args.no_xjoin})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
