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
WIN / EDGE / WEDGE / adjusted_WEDGE（と adjusted_WEDGE_年数・件数・最終年）は集計CSVの値をそのまま入れる。

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
from shisu import families as fam_registry
from shisu.conditions import CONDITION_COLUMNS, derive, load_inner_lookup, standardize_entries
from shisu.csvio import norm_series, read_csv_any
from shisu.matching import HIT_COLUMNS, STAT_COLUMNS, UNIFIED, indicator_bases, match_entry_row, unified_status
from shisu.naming import (ENTRIES_ALL, ENTRIES_PREFIX, FORAGG_PREFIX, file_stem, parse_foragg_stem,
                          table_name_for_entries, table_name_for_foragg, xjoin_table_for)
from shisu.normalize import (apply_age_class_rule, apply_distance_rules, class_series, distance_series,
                             entry_inner_outer, frame_group_series, infer_entry_weight, race_date,
                             race_id_without_horse, split_surface_distance, weight_series)
from shisu.wedge import WEDGE_COLUMNS, WEDGE_FLOAT_COLUMNS, WEDGE_INT_COLUMNS

META = "__tables_meta__"
FACETS = "__facets__"
FAMILIES_TABLE = fam_registry.DB_TABLE
RATE_NAMES = ("勝率", "連対率", "複勝率", "2着率", "3着率")
FLOAT_COLUMNS = ("掲示板率_val", "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値") + WEDGE_FLOAT_COLUMNS
INT_COLUMNS = ("件数",) + WEDGE_INT_COLUMNS
META_COLUMNS = ("table_name", "source_file", "kind", "family", "odds_mode", "rank_scheme", "indicator",
                "region", "n_rows")
UNIFIED_TEXT = ("場所", "芝・ダ", "距離", "芝(内・外)", "結合データ")
UNIFIED_STATS = ("件数", "勝率", "連対率", "複勝率", "2着率", "3着率", "掲示板率",
                 "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値",
                 "提案勝率", "提案連対率", "提案複勝率", "提案2着率", "提案3着率",
                 "勝率_val", "連対率_val", "複勝率_val", "2着率_val", "3着率_val", "掲示板率_val") + WEDGE_COLUMNS
UNIFIED_META = ("family", "odds_mode", "rank_scheme", "indicator", "region", "テーブル名")
FACET_COLUMNS = ("場所", "芝・ダ", "距離", "芝(内・外)")
XJOIN_CACHE_LIMIT = 20_000
ENTRIES_ALL_COLUMNS = ("日付", "レースID(新)", "レースID(新/馬番無)", "場 R", "場所", "芝・ダ", "距離",
                       "芝(内・外)", "クラス名", "重量種別", "頭数", "枠番", "枠番グループ", "馬番", "馬名",
                       "騎手", "source_table")
# 出馬表で必ず持たせる列（無ければ空で作る。SQL が存在しない列を参照しないように）
ENTRIES_REQUIRED = ("日付", "レースID(新)", "レースID(新/馬番無)", "場 R", "レース名", "場所", "芝・ダ",
                    "距離", "距離_raw", "芝(内・外)", "クラス名", "クラス名_raw", "重量種別", "頭数",
                    "枠番", "枠番グループ", "馬番", "馬名", "騎手")
_SQL_TYPES = {**{c: "INTEGER" for c in INT_COLUMNS}, **{c: "REAL" for c in STAT_COLUMNS if c.endswith("_val")},
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
    for c in INT_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").round().astype("Int64")
    for c in FLOAT_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for r in RATE_NAMES:
        if r in df.columns:
            df[f"{r}_val"] = estimate_percent(df[r])
    return df


def load_agg_tables(con: sqlite3.Connection, agg_dir: Path) -> tuple[list[dict], int]:
    """成績表を取り込む。戻り値は (取り込んだ表の情報, WEDGE 列が無く取り込まなかった旧形式の本数)。"""
    files = sorted(Path(agg_dir).glob("*.csv"))
    if not files:
        print(f"[foragg] 集計CSVがありません: {agg_dir}（aggregate_stats_ver2.py を先に実行）")
        return [], 0
    loaded, skipped, empty, old_format, inactive = [], 0, 0, [], []
    active = {f.name: set(f.schemes) for f in fam_registry.active()}
    for p in files:
        info = parse_foragg_stem(file_stem(p.name))
        if info is None:
            print(f"[foragg] SKIP（ファイル名が規約外・旧仕様）: {p.name}", file=sys.stderr)
            skipped += 1
            continue
        if info["scheme"] not in active.get(info["family"], set()):
            inactive.append(p.name)      # 前の実行の別の family・スキームの成績表（families.json に無い）
            continue
        df = read_csv_any(p)
        if "結合データ" not in df.columns or "件数" not in df.columns:
            print(f"[foragg] SKIP（集計結果ではありません）: {p.name}", file=sys.stderr)
            skipped += 1
            continue
        missing = [c for c in WEDGE_COLUMNS if c not in df.columns]
        if missing:     # Phase 3 より前の aggregate の出力。取り込むと WEDGE が全部空の DB になる
            old_format.append(p.name)
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
    if inactive:
        print(f"[WARN] 今の family（agg_csv/families.json）に無い成績表 {len(inactive)} 本は取り込みませんでした"
              f"（例: {inactive[0]}）。前の実行の残りなので aggregate_stats_ver2.py --clean で消せます", file=sys.stderr)
    if old_format:
        print(f"[ERROR] WEDGE 列の無い旧形式の成績表が {len(old_format)} 本あり、取り込みませんでした"
              f"（例: {old_format[0]}）。aggregate_stats_ver2.py --clean を実行し直してください", file=sys.stderr)
    return loaded, len(old_format)


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
    # 横断ランキング（/ の「まとめて表示」）を WEDGE 系で並べ替えるときに使う
    con.execute(f'CREATE INDEX idx_unified_wedge ON {UNIFIED}("WEDGE")')
    con.execute(f'CREATE INDEX idx_unified_adj_wedge ON {UNIFIED}("adjusted_WEDGE")')
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


def normalize_entries_df(raw: pd.DataFrame, inner_lookup: dict | None = None) -> pd.DataFrame:
    """出馬表（calc_entry_hensachi の出力）を、キーの部品が過去側と同じ表記になるよう正規化する。
    22条件（shisu/conditions.py）の列も足す。inner_lookup は前走の内外を引く表（replace が master_csv/ に書く）。"""
    df = raw.copy()
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]
    raw = df
    rid = norm_series(df["レースID(新)"]) if "レースID(新)" in df.columns else pd.Series("", index=df.index)
    given = df["レースID(新/馬番無)"] if "レースID(新/馬番無)" in df.columns else None
    df["レースID(新)"] = rid
    df["日付"] = _first_nonempty(df, ("日付",)).where(lambda s: s != "", race_date(rid))
    df["場 R"] = _first_nonempty(df, ("場 R", "場R"))
    rid_nh = race_id_without_horse(rid, given)
    # レースIDが無い出馬表は「日付_場 R」でレースを識別する（全馬が1レースにまとまらないように）
    fallback = (df["日付"].astype(str) + "_" + df["場 R"].astype(str)).where(df["場 R"] != "", "")
    df["レースID(新/馬番無)"] = rid_nh.where(rid_nh != "", fallback)
    df["レース名"] = _first_nonempty(df, ("レース名",))
    df["場所"] = _first_nonempty(df, ("場所",))
    surface, dist_raw = split_surface_distance(_first_nonempty(df, ("芝・ダ", "芝ダ")), _first_nonempty(df, ("距離",)))
    # 障害レース: TARGET の出馬表は 芝ダ に「ダ」「芝」と入り、今芝ダ・レース名（障害…）・トラックコードで分かる。
    # 過去側は障害を集計していないので「障」にして、どの family のキーも作らない（matching.is_jump_row）
    track = pd.to_numeric(_first_nonempty(df, ("トラックコード(JV)", "トラックコード")), errors="coerce")
    jump = ((_first_nonempty(df, ("今芝ダ", "今芝・ダ")) == "障") | (surface == "障")
            | df["レース名"].astype(str).str.startswith("障害") | (track >= 51).fillna(False))
    surface = surface.where(~jump, "障")
    df["芝・ダ"] = surface
    dnum = distance_series(dist_raw)
    df["距離_raw"] = dnum.astype(object).where(dnum.notna(), "").map(lambda v: "" if v == "" else str(int(v)))
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
    cond = derive(standardize_entries(raw, df), inner_lookup)
    for c in CONDITION_COLUMNS:
        df[c] = cond[c]
    return df


def load_entries_tables(con: sqlite3.Connection, entries_dir: Path) -> list[dict]:
    files = sorted(Path(entries_dir).glob("*.csv"))
    if not files:
        print(f"[entries] 出馬表CSVがありません: {entries_dir}")
        return []
    loaded = []
    inner = load_inner_lookup()
    for p in files:
        df = normalize_entries_df(read_csv_any(p), inner)
        tname = table_name_for_entries(p.name)
        df.to_sql(tname, con, if_exists="replace", index=False)
        con.execute(f'CREATE INDEX IF NOT EXISTS {q("idx_" + tname + "_race")} ON {q(tname)}("レースID(新/馬番無)")')
        con.execute(f'CREATE INDEX IF NOT EXISTS {q("idx_" + tname + "_date")} ON {q(tname)}("日付", "場所")')
        bases = indicator_bases(df.columns)
        old_format = [b for b in bases if f"{b}_順位帯_s1" not in df.columns]
        if old_format:
            print(f"[WARN] {p.name}: 旧形式の出馬表です（{', '.join(old_format)} に 順位帯_s1/s2 が無い）。"
                  "calc_entry_hensachi.py で作り直してください", file=sys.stderr)
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
    families = tuple(families or fam_registry.active_names())
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
    # 照合結果のキャッシュは上限付き（上限なしだと大きい出馬表で数GBになる。2万件で空にしても速度は同じ）
    cache: dict = {}
    total = 0
    batch = []
    for i, row in enumerate(rows, 1):
        if len(cache) > XJOIN_CACHE_LIMIT:
            cache.clear()
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


def load_families_for_agg(agg_dir: Path) -> list:
    """agg_csv/families.json（aggregate が書く）の family を台帳に入れて、使う family にする。無ければ今の設定のまま。"""
    p = Path(agg_dir) / "families.json"
    if not p.exists():
        return fam_registry.configure()
    fams = fam_registry.load_families(p)
    fam_registry.set_active(fams, f"agg_csv/families.json（{len(fams)} family）")
    return fams


def save_families_table(con: sqlite3.Connection, fams) -> None:
    fam_registry.save_to_db(con, fams)


def families_from_db(con: sqlite3.Connection) -> list:
    return fam_registry.load_from_db(con)


def parse_cli(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["both", "foragg", "entries"], default="both")
    p.add_argument("--fresh", action="store_true", help="DBファイルを削除して作り直す")
    p.add_argument("--prune", action="store_true", help="今回作り直した種類の古いテーブルを削除する")
    p.add_argument("--no-xjoin", action="store_true", help="entries_xjoin_* を作らない（画面はその場で照合する）")
    p.add_argument("--agg-dir", dest="agg_dir", default=str(config.agg_dir()),
                   help="成績表（aggregate_stats_ver2.py の出力）のディレクトリ（既定 agg_csv/）")
    p.add_argument("--allow-empty", action="store_true", help="成績表が0本でも unified_all を空で作り直す")
    p.add_argument("--entries-dir", default=str(config.entries_out_dir()))
    p.add_argument("--db-path", default=str(config.db_path()), help="既定は環境変数 DB_PATH か racing.db")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_cli(argv)
    db = Path(args.db_path)
    if args.mode in ("both", "foragg"):      # DB を消す前に family の定義を確かめる（指定したファイルが無いなど）
        try:
            fams = load_families_for_agg(Path(args.agg_dir))
        except (FileNotFoundError, ValueError) as e:
            print(f"[ERROR] family の定義: {e}", file=sys.stderr)
            return 2
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
    rc = 0

    if args.mode in ("both", "foragg"):
        kinds.add("foragg")
        loaded, n_old = load_agg_tables(con, Path(args.agg_dir))
        if n_old:
            rc = 1
        if not loaded and not args.allow_empty:
            print(f"[ERROR] 読み込める成績表がありません（{args.agg_dir}）。unified_all は変更しません。"
                  "aggregate_stats_ver2.py の実行と --agg-dir を確認してください（空にしたいときは --allow-empty）",
                  file=sys.stderr)
            con.close()
            return 2
        rebuild_unified_all(con, loaded)
        rebuild_facets(con)
        save_families_table(con, fams)
        replace_meta(con, ("foragg",), loaded)
        keep |= {t["table_name"] for t in loaded}
        con.commit()
        if args.mode == "foragg" and con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name LIKE 'entries\\_xjoin\\_%' ESCAPE '\\' LIMIT 1").fetchone():
            print("[WARN] unified_all を作り直しました。entries_xjoin_* は古い照合結果のままなので "
                  "--mode entries も実行してください", file=sys.stderr)

    if args.mode in ("both", "entries"):
        kinds.add("entries")
        if args.mode == "entries":           # 照合に使う family は、DB を作ったときの family
            db_fams = families_from_db(con)
            if db_fams:
                fam_registry.set_active(db_fams, f"DB の {FAMILIES_TABLE}（{len(db_fams)} family）")
        entries = load_entries_tables(con, Path(args.entries_dir))
        build_entries_all(con, entries)
        keep |= {t["table_name"] for t in entries} | {ENTRIES_ALL}
        meta_rows = list(entries)
        status = unified_status(con)
        for t in entries:
            xname = xjoin_table_for(t["table_name"])
            if args.no_xjoin or status != "ok":
                con.execute(f"DROP TABLE IF EXISTS {q(xname)}")   # 古い照合結果を残さない
                continue
            meta_rows.append(build_xjoin(con, t["table_name"]))
            keep.add(xname)
            con.commit()
        replace_meta(con, ("entries", "xjoin"), meta_rows)
        if status == "missing":
            print("[WARN] unified_all がありません。--mode foragg（または both）を先に実行してください（xjoin は作りません）",
                  file=sys.stderr)
            rc = 1
        elif status == "old":
            print("[ERROR] unified_all が旧形式です（family/indicator/WEDGE などの列が無い）。"
                  "replace → aggregate → build_db.py --fresh で作り直してください（xjoin は作りません）", file=sys.stderr)
            rc = 1

    if args.prune:
        prune(con, kinds, keep)
    con.commit()
    con.close()
    print(f"{'OK' if rc == 0 else 'DONE(警告あり)'}: {db} (mode={args.mode}, prune={args.prune}, xjoin={not args.no_xjoin})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
