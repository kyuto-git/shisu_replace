# -*- coding: utf-8 -*-
"""SQLite（racing.db）を構築する。

--mode foragg : agg_csv/agg_master_*.csv.gz（集計結果。master 1本につき1ファイル）→ unified_all（全成績表の縦結合）
                旧来の agg_csv/foragg_*.csv（1表1ファイル）も読める（同じ master の agg_master_*.csv.gz があれば使わない）
                1表ずつのテーブル foragg_* は作らない（2026-11-10〜。同じ行を2回持たない）。前の DB に残っていれば消す
--mode entries: entries_out/*.csv（偏差値付き出馬表）→ entries_*, entries_all, entries_xjoin_*（照合結果の事前計算）
--mode both   : 両方（既定）

テーブル
  __tables_meta__ : table_name, source_file, kind(foragg/entries/xjoin), family, odds_mode, rank_scheme,
                    indicator, region, n_rows。画面はテーブル名を解析せず、この表を見る
                    （kind=foragg の table_name は unified_all の「テーブル名」。その名前の実テーブルは無い）
  unified_all     : 全成績表の縦結合。family / odds_mode / rank_scheme / indicator / region / テーブル名 列付き
                    （作り直しは unified_all__build に作ってから置き換える。途中で止まっても前の unified_all が残る）
  __facets__      : 画面の選択肢（場所・芝・ダ・距離・芝(内・外)）
  entries_*       : 出馬表（キー部品を正規化済み。日付・レースID(新/馬番無) を付与）
  entries_xjoin_* : 出馬表の各馬 × 指数 × family × キー候補 と unified_all の完全一致照合の結果

*_val（勝率・連対率・複勝率・2着率・3着率）は、集計CSVの文字列 '頻度%（推定%）' の括弧内（推定値）で
作り直す。掲示板率_val は頻度のまま。率はすべて 0–100。
WIN / EDGE / WEDGE / adjusted_WEDGE（と adjusted_WEDGE_年数・件数・最終年）は集計CSVの値をそのまま入れる。

--prune は今回作り直した出馬表の古いテーブル（entries_*・entries_xjoin_*）だけを消す。成績表（unified_all・
__tables_meta__ の kind=foragg・前の版の foragg_*）は触らない（前の版の foragg_* は --mode foragg の入れ替えで消える。
多すぎて残ったときは --fresh）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import zlib
from pathlib import Path

import pandas as pd

from shisu import config
from shisu import families as fam_registry
from shisu.conditions import CONDITION_COLUMNS, derive, load_inner_lookup, standardize_entries
from shisu.csvio import norm_series, read_csv_any
from shisu.matching import HIT_COLUMNS, STAT_COLUMNS, UNIFIED, indicator_bases, match_entry_row, unified_status
from shisu.aggstore import (FLOAT_COLUMNS, INT_COLUMNS, RATE_NAMES, ROW_META, agg_master_files,  # noqa: F401
                            IDENTITY_KEYS, STAMP_SUFFIX, estimate_percent, iter_agg_chunks, legacy_files,
                            master_identity, prepare_agg_frame, read_stamp)
from shisu.csvio import READ_ENCODINGS
from shisu.naming import (ENTRIES_ALL, ENTRIES_PREFIX, file_stem, parse_agg_master_name,
                          parse_foragg_stem, table_name_for_entries, table_name_for_foragg, xjoin_table_for)
from shisu.normalize import (apply_age_class_rule, apply_distance_rules, class_series, distance_series,
                             entry_inner_outer, frame_group_series, infer_entry_weight, race_date,
                             race_id_without_horse, split_surface_distance, weight_series)
from shisu.wedge import WEDGE_COLUMNS

META = "__tables_meta__"
FACETS = "__facets__"
FAMILIES_TABLE = fam_registry.DB_TABLE
UNIFIED_BUILD = UNIFIED + "__build"
AGG_CHUNK_ROWS = 200_000
# 前の版の1表ずつのテーブル foragg_* を作り直しのときに消す上限。DROP TABLE は1本ごとにスキーマを書き直すので
# 本数の2乗で遅くなる（1.6万本で約13秒、数十万本だと何時間も）。超えたら消さずに残して --fresh を勧める
# （画面は unified_all から読むので、残っていても表示は変わらない）
DROP_TABLE_LIMIT = 5000
READ_ERRORS = (EOFError, OSError, zlib.error, pd.errors.ParserError, pd.errors.EmptyDataError)
META_COLUMNS = ("table_name", "source_file", "kind", "family", "odds_mode", "rank_scheme", "indicator",
                "region", "n_rows")
UNIFIED_TEXT = ("場所", "芝・ダ", "距離", "芝(内・外)", "結合データ")
UNIFIED_STATS = ("件数", "勝率", "連対率", "複勝率", "2着率", "3着率", "掲示板率",
                 "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値",
                 "提案勝率", "提案連対率", "提案複勝率", "提案2着率", "提案3着率",
                 "勝率_val", "連対率_val", "複勝率_val", "2着率_val", "3着率_val", "掲示板率_val") + WEDGE_COLUMNS
UNIFIED_META = ROW_META        # ("family", "odds_mode", "rank_scheme", "indicator", "region", "テーブル名")
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
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_meta_kind ON {META}(kind)")


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
UNIFIED_COLUMNS = UNIFIED_TEXT + UNIFIED_STATS + UNIFIED_META


def _create_unified(con: sqlite3.Connection, name: str) -> None:
    con.execute(f"DROP TABLE IF EXISTS {q(name)}")
    coldefs = ([f"{q(c)} TEXT" for c in UNIFIED_TEXT]
               + [f"{q(c)} {_SQL_TYPES.get(c, 'TEXT')}" for c in UNIFIED_STATS]
               + [f"{q(c)} TEXT" for c in UNIFIED_META])
    con.execute(f"CREATE TABLE {q(name)} ({', '.join(coldefs)})")


def _create_unified_indexes(con: sqlite3.Connection) -> None:
    con.execute(f'CREATE INDEX idx_unified_match ON {UNIFIED}("結合データ", "family", "indicator")')
    con.execute(f'CREATE INDEX idx_unified_table ON {UNIFIED}("テーブル名")')
    # 横断ランキング（/ の「まとめて表示」）を WEDGE 系で並べ替えるときに使う
    con.execute(f'CREATE INDEX idx_unified_wedge ON {UNIFIED}("WEDGE")')
    con.execute(f'CREATE INDEX idx_unified_adj_wedge ON {UNIFIED}("adjusted_WEDGE")')


class _AggLoad:
    """成績表を unified_all__build に流し込みながら、__tables_meta__・__facets__ の材料を数える。"""

    def __init__(self, con: sqlite3.Connection, active: dict[str, set[str]]):
        self.con = con
        self.active_keys = {f"{n}\t{sc}" for n, schemes in active.items() for sc in schemes}
        self.meta: dict[str, dict] = {}
        self.facets: dict[str, set] = {c: set() for c in FACET_COLUMNS}
        self.rows = 0
        self.inactive_rows = 0
        self.inactive_files: list[str] = []
        self.failed: list[str] = []
        self._ins = (f"INSERT INTO {q(UNIFIED_BUILD)} ({', '.join(q(c) for c in UNIFIED_COLUMNS)}) "
                     f"VALUES ({', '.join('?' * len(UNIFIED_COLUMNS))})")

    def insert(self, df: pd.DataFrame, source_file: str, meta: dict, facets: dict) -> int:
        """df（成績表の行。ROW_META の列つき・文字列）を入れる。今の family に無い行は入れない。"""
        key = df["family"].astype(str) + "\t" + df["rank_scheme"].astype(str)
        act = key.isin(self.active_keys)
        self.inactive_rows += int((~act).sum())
        df = df[act]
        if df.empty:
            return 0
        df = prepare_agg_frame(df)
        for c in UNIFIED_COLUMNS:
            if c not in df.columns:
                df[c] = None
        sub = df[list(UNIFIED_COLUMNS)].astype(object)
        sub = sub.where(sub.notna(), None)
        self.con.executemany(self._ins, sub.itertuples(index=False, name=None))
        for c in FACET_COLUMNS:
            v = df[c]
            facets[c].update(v[v.notna() & (v != "")].astype(str).unique().tolist())
        grp = df.groupby(["テーブル名", "family", "odds_mode", "rank_scheme", "indicator", "region"], sort=False).size()
        for (t, fam, mode, sc, ind, reg), n in grp.items():
            m = meta.setdefault(t, {"table_name": t, "source_file": source_file, "kind": "foragg", "family": fam,
                                    "odds_mode": mode, "rank_scheme": sc, "indicator": ind, "region": reg,
                                    "n_rows": 0})
            m["n_rows"] += int(n)
        return len(df)

    def load_file(self, read, source_file: str) -> int:
        """read(encoding) が返す DataFrame の列（chunk の並び）を1ファイル分として入れる。読めない符号化なら
        そのファイルの分を戻して次の符号化で読み直す。戻り値は入れた行数（読めなければ -1）。"""
        last = None
        for enc in READ_ENCODINGS:
            meta, facets, n = {}, {c: set() for c in FACET_COLUMNS}, 0
            inactive0 = self.inactive_rows
            self.con.commit()
            self.con.execute("SAVEPOINT agg_file")
            try:
                for df in read(enc):
                    n += self.insert(df, source_file, meta, facets)
            except UnicodeDecodeError as e:
                last = e
                self.con.execute("ROLLBACK TO agg_file")
                self.con.execute("RELEASE agg_file")
                self.inactive_rows = inactive0
                continue
            except READ_ERRORS as e:            # 途中で切れた gzip・壊れた CSV: そのファイルだけ戻して飛ばす
                self.con.execute("ROLLBACK TO agg_file")
                self.con.execute("RELEASE agg_file")
                self.inactive_rows = inactive0
                print(f"[ERROR] {source_file}: 読めません（{type(e).__name__}: {e}）。このファイルは取り込みません"
                      "（途中で切れている可能性。aggregate_stats_ver2.py --resume で作り直せます）", file=sys.stderr)
                self.failed.append(source_file)
                return -1
            except BaseException:
                self.con.execute("ROLLBACK TO agg_file")
                self.con.execute("RELEASE agg_file")
                raise
            self.con.execute("RELEASE agg_file")
            self.con.commit()
            for t, m in meta.items():
                if t in self.meta:                       # 同じテーブル名が2つのファイルにある（ふつうは無い）
                    self.meta[t]["n_rows"] += m["n_rows"]
                else:
                    self.meta[t] = m
            for c in FACET_COLUMNS:
                self.facets[c] |= facets[c]
            self.rows += n
            return n
        print(f"[foragg] SKIP（{READ_ENCODINGS} で読めません）: {source_file} ({last})", file=sys.stderr)
        self.failed.append(source_file)
        return -1


def _header(read) -> list[str] | None:
    """見出しの列（符号化を順に試す）。読めない（壊れた・空・途中で切れた）ときは READ_ERRORS を投げる。"""
    for enc in READ_ENCODINGS:
        try:
            for df in read(enc, nrows=0):
                return list(df.columns)
            return []
        except UnicodeDecodeError:
            continue
    return None


def load_agg_tables(con: sqlite3.Connection, agg_dir: Path) -> tuple[list[dict], int, dict[str, set]]:
    """成績表（agg_master_*.csv.gz と旧来の foragg_*.csv）を unified_all__build に入れる。
    戻り値は (__tables_meta__ の行, WEDGE 列が無い旧形式の本数＋読めなかった本数, __facets__ の値)。
    unified_all はまだ置き換えない（swap_unified で置き換える）。"""
    masters = agg_master_files(agg_dir)
    legacy = legacy_files(agg_dir)
    if not masters and not legacy:
        print(f"[foragg] 集計CSVがありません: {agg_dir}（aggregate_stats_ver2.py を先に実行）")
        return [], 0, {c: set() for c in FACET_COLUMNS}
    _create_unified(con, UNIFIED_BUILD)
    con.commit()
    active = {f.name: set(f.schemes) for f in fam_registry.active()}
    load = _AggLoad(con, active)
    skipped, old_format, shadowed = 0, [], 0
    stamps: dict[str, str] = {}
    rebuilt: list[str] = []
    master_dir = config.master_dir()
    # 旧来の foragg_*.csv を使わない master = agg_master のファイルがある master（読めなかったものも。
    # 壊れた agg_master の代わりに前の版の古い成績表を黙って使わないように）
    sources = {parse_agg_master_name(p.name) for p in masters}
    # 今の版で集計して0行だった master（印だけがあり成績表のファイルは無い）も、旧来のファイルを使わない
    sources |= {parse_agg_master_name(p.name[:-len(STAMP_SUFFIX)])
                for p in Path(agg_dir).glob(f"agg_master_*{STAMP_SUFFIX}")} - {None}
    loaded_sources = set()
    legacy_sources: set[str] = set()
    for p in masters:
        source = parse_agg_master_name(p.name)
        read = lambda enc, nrows=None, _p=p: (                         # noqa: E731
            iter_agg_chunks(_p, AGG_CHUNK_ROWS, enc) if nrows is None else
            iter([pd.read_csv(_p, compression="gzip", encoding=enc, dtype=str, keep_default_na=False, nrows=nrows)]))
        try:
            cols = _header(read)
        except READ_ERRORS as e:
            print(f"[ERROR] {p.name}: 読めません（{type(e).__name__}: {e}）。このファイルは取り込みません"
                  "（壊れている・途中で切れている可能性。aggregate_stats_ver2.py --resume で作り直せます）", file=sys.stderr)
            load.failed.append(p.name)
            continue
        if cols is None:
            print(f"[foragg] SKIP（{READ_ENCODINGS} で読めません）: {p.name}", file=sys.stderr)
            load.failed.append(p.name)
            continue
        if not {"結合データ", "件数", *ROW_META} <= set(cols):
            print(f"[foragg] SKIP（master ごとの成績表の形ではありません）: {p.name}", file=sys.stderr)
            skipped += 1
            continue
        if [c for c in WEDGE_COLUMNS if c not in cols]:
            old_format.append(p.name)
            continue
        n = load.load_file(read, p.name)
        if n < 0:
            continue
        loaded_sources.add(source)
        st = read_stamp(p) or {}
        stamps[p.name] = st.get("settings", "")
        # 成績表を作ったあとに replace が master を作り直したか（読んだ場所に無ければ master_csv の同じ名前で。
        # 比べるのは大きさと中身の指紋。更新時刻はコピー・Docker のマウントで変わるので使わない）
        m = Path(st.get("master_path") or "")
        if not (st.get("master_path") and m.exists()):
            m = master_dir / str(st.get("master", ""))
        if st.get("master") and st.get("master_fingerprint") and m.is_file():
            cur = master_identity(m)
            if any(cur[k] != st.get(k) for k in IDENTITY_KEYS):
                rebuilt.append(p.name)
        print(f"[foragg] {p.name}: {n} rows", flush=True)
    for p in legacy:
        info = parse_foragg_stem(file_stem(p.name))
        if info is None:
            print(f"[foragg] SKIP（ファイル名が規約外・旧仕様）: {p.name}", file=sys.stderr)
            skipped += 1
            continue
        if info["source"] in sources:
            shadowed += 1                 # 同じ master の agg_master_*.csv.gz を使う
            continue
        if info["scheme"] not in active.get(info["family"], set()):
            load.inactive_files.append(p.name)   # 前の実行の別の family・スキームの成績表（families.json に無い）
            continue
        df = read_csv_any(p)
        if "結合データ" not in df.columns or "件数" not in df.columns:
            print(f"[foragg] SKIP（集計結果ではありません）: {p.name}", file=sys.stderr)
            skipped += 1
            continue
        if [c for c in WEDGE_COLUMNS if c not in df.columns]:   # Phase 3 より前の aggregate の出力
            old_format.append(p.name)
            continue
        if df.empty:
            continue
        df = df.assign(family=info["family"], odds_mode=info["mode"], rank_scheme=info["scheme"],
                       indicator=info["indicator"], region=info["region"], テーブル名=table_name_for_foragg(p.name))
        if load.load_file(lambda enc, _df=df: iter([_df]), p.name) >= 0:
            legacy_sources.add(info["source"])
    rows = sorted(load.meta.values(), key=lambda m: m["table_name"])
    print(f"[foragg] loaded {len(rows)} tables / {load.rows} rows（master ごとの成績表 {len(loaded_sources)} 本・"
          f"旧来の成績表 {len(legacy) - shadowed} 本 / skip {skipped}）")
    if shadowed:
        print(f"[INFO] 旧来の成績表 foragg_*.csv {shadowed} 本は、同じ master の agg_master_*.csv.gz があるので使いませんでした"
              "（aggregate_stats_ver2.py --clean で消せます）")
    if load.inactive_rows or load.inactive_files:
        ex = f"（例: {load.inactive_files[0]}）" if load.inactive_files else ""
        print(f"[WARN] 今の family（agg_csv/families.json）に無い成績表は取り込みませんでした: 行 {load.inactive_rows}・"
              f"旧来のファイル {len(load.inactive_files)} 本{ex}。前の実行の残りなので aggregate_stats_ver2.py --clean で消せます",
              file=sys.stderr)
    if old_format:
        print(f"[ERROR] WEDGE 列の無い旧形式の成績表が {len(old_format)} 本あり、取り込みませんでした"
              f"（例: {old_format[0]}）。aggregate_stats_ver2.py --clean を実行し直してください", file=sys.stderr)
    ids = set(stamps.values()) - {""}
    no_stamp = [n for n, v in stamps.items() if not v]
    if len(ids) > 1 or no_stamp:
        print(f"[WARN] master ごとの成績表に、違う設定（family・コンパクト化・WEDGE など）で作られたもの、または書き終わりの印"
              f"（.done）が無いものがあります（設定 {len(ids)} 通り・印が無いもの {len(no_stamp)} 本"
              + (f"。例: {no_stamp[0]}" if no_stamp else "") + "）。印が無いのは、集計の間に master が作り直された・前の版の"
              "aggregate で作ったものです。aggregate_stats_ver2.py --resume で今の設定にそろえてから作り直してください",
              file=sys.stderr)
    if legacy_sources:
        msg = (f"旧来の成績表 foragg_*.csv を使いました（{len(legacy_sources)} master。例: {sorted(legacy_sources)[0]}）。"
               "作ったときの設定が分からないので、aggregate_stats_ver2.py --clean で作り直すのがおすすめです")
        print(("[WARN] " if loaded_sources else "[INFO] ") + msg, file=sys.stderr if loaded_sources else sys.stdout)
    if rebuilt:
        print(f"[WARN] 成績表を作ったあとに master が作り直されています（{len(rebuilt)} 本。例: {rebuilt[0]}）。"
              "aggregate_stats_ver2.py --resume で作り直してください", file=sys.stderr)
    con.commit()
    return rows, len(old_format) + len(load.failed), load.facets


def drop_table_level_foragg(con: sqlite3.Connection, limit: int | None = None) -> tuple[int, int]:
    """前の版の build_db が作った1表ずつのテーブル foragg_*（今は作らない。unified_all と同じ行）を消す。
    limit（既定 DROP_TABLE_LIMIT）本を超えるときは消さない（遅いので）。戻り値は (消した本数, 残した本数)。"""
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg\\_%' ESCAPE '\\'")]
    if len(names) > (DROP_TABLE_LIMIT if limit is None else limit):
        return 0, len(names)
    for t in names:
        con.execute(f"DROP TABLE IF EXISTS {q(t)}")
    return len(names), 0


def swap_unified(con: sqlite3.Connection, facets: dict[str, set] | None = None) -> None:
    """unified_all__build を unified_all にする（索引・__facets__ も作る）。前の unified_all・1表ずつの foragg_* は消す。"""
    con.commit()
    con.execute("BEGIN")
    n_old, n_left = drop_table_level_foragg(con)
    con.execute(f"DROP TABLE IF EXISTS {UNIFIED}")
    con.execute(f"ALTER TABLE {q(UNIFIED_BUILD)} RENAME TO {UNIFIED}")
    _create_unified_indexes(con)
    rebuild_facets(con, facets)
    n = con.execute(f"SELECT COUNT(*) FROM {UNIFIED}").fetchone()[0]
    print(f"[foragg] unified_all: {n} rows" + (f"（前の版の1表ずつのテーブル foragg_* {n_old} 個を消しました）" if n_old else ""))
    if n_left:
        print(f"[WARN] 前の版の1表ずつのテーブル foragg_* が {n_left} 個残っています（多すぎて消すと時間がかかるので残しました。"
              "画面は unified_all から読むので表示には使いません）。DB を小さくするには build_db.py --fresh で作り直してください",
              file=sys.stderr)


def rebuild_facets(con: sqlite3.Connection, facets: dict[str, set] | None = None) -> None:
    """__facets__（画面の選択肢）。facets を渡せばその値、無ければ unified_all から数える。"""
    con.execute(f"DROP TABLE IF EXISTS {FACETS}")
    con.execute(f"CREATE TABLE {FACETS} (col TEXT, val TEXT)")
    for c in FACET_COLUMNS:
        if facets is not None:
            con.executemany(f"INSERT INTO {FACETS} VALUES (?, ?)", [(c, v) for v in sorted(facets.get(c, ()))])
        else:
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
    """今回作り直した種類の古いテーブルを消す（出馬表 entries_*・entries_xjoin_*）。
    成績表は対象にしない: 前の版の1表ずつのテーブル foragg_* は swap_unified が（DROP_TABLE_LIMIT つきで）消し、
    __tables_meta__ の kind=foragg の行は replace_meta が入れ替える（ここで消すと unified_all の集計表の一覧まで消える）。"""
    existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    prefixes = []
    if "entries" in kinds:
        prefixes.append(ENTRIES_PREFIX)   # entries_xjoin_* も含む
    stale = sorted(t for t in existing if any(t.startswith(p) for p in prefixes) and t not in keep)
    for t in stale:
        con.execute(f"DROP TABLE IF EXISTS {q(t)}")
        con.execute(f"DELETE FROM {META} WHERE table_name = ? AND kind IS NOT 'foragg'", (t,))
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
    p.add_argument("--entries-only", action="store_true",
                   help="--mode entries と同じ（出馬表だけ取り込み直す。馬体重発表後のマイニング確定値の更新など）")
    p.add_argument("--fresh", action="store_true", help="DBファイルを削除して作り直す")
    p.add_argument("--prune", action="store_true",
                   help="今回作り直した出馬表の古いテーブル（entries_*・entries_xjoin_*）を削除する（成績表は触らない）")
    p.add_argument("--no-xjoin", action="store_true", help="entries_xjoin_* を作らない（画面はその場で照合する）")
    p.add_argument("--agg-dir", dest="agg_dir", default=str(config.agg_dir()),
                   help="成績表（aggregate_stats_ver2.py の出力）のディレクトリ（既定 agg_csv/）")
    p.add_argument("--allow-empty", action="store_true", help="成績表が0本でも unified_all を空で作り直す")
    p.add_argument("--entries-dir", default=str(config.entries_out_dir()))
    p.add_argument("--db-path", default=str(config.db_path()), help="既定は環境変数 DB_PATH か racing.db")
    args = p.parse_args(argv)
    if args.entries_only:
        if args.mode not in ("both", "entries"):
            p.error("--entries-only と --mode foragg は一緒に使えません")
        if args.fresh:
            p.error("--entries-only と --fresh は一緒に使えません（--fresh は成績表も含めて DB を消します）")
        args.mode = "entries"
    return args


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
        con.execute(f"DROP TABLE IF EXISTS {q(UNIFIED_BUILD)}")      # 前に止まった作り直しの残り
        loaded, n_old, facets = load_agg_tables(con, Path(args.agg_dir))
        if n_old:
            rc = 1
        if not loaded and not args.allow_empty:
            con.execute(f"DROP TABLE IF EXISTS {q(UNIFIED_BUILD)}")
            con.commit()
            print(f"[ERROR] 読み込める成績表がありません（{args.agg_dir}）。unified_all は変更しません。"
                  "aggregate_stats_ver2.py の実行と --agg-dir を確認してください（空にしたいときは --allow-empty）",
                  file=sys.stderr)
            con.close()
            return 2
        if not table_exists(con, UNIFIED_BUILD):
            _create_unified(con, UNIFIED_BUILD)
        swap_unified(con, facets)
        save_families_table(con, fams)
        replace_meta(con, ("foragg",), loaded)
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
    try:                                     # 作り直しで大きくなった WAL を切り詰める（ディスクを返す）
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError:
        pass
    con.close()
    print(f"{'OK' if rc == 0 else 'DONE(警告あり)'}: {db} (mode={args.mode}, prune={args.prune}, xjoin={not args.no_xjoin})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
