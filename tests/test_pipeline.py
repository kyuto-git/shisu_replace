"""csv_files → replace → aggregate → calc → build_db → app の通しテスト（合成データ）。"""
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AGG_TEST_ARGS = ["--wedge-min-year-count", "2"]


@pytest.fixture(scope="module")
def base(tmp_path_factory, monkeypatch_module):
    b = tmp_path_factory.mktemp("shisu")
    monkeypatch_module.setenv("SHISU_BASE_DIR", str(b))
    monkeypatch_module.delenv("DB_PATH", raising=False)
    synth.make_history(b)
    synth.make_entries(b)
    import aggregate_stats_ver2, build_db, calc_entry_hensachi, replace_csv_ver3
    assert replace_csv_ver3.main(["--clean", "--processed"]) == 0
    # 旧仕様のファイルが残っていても使われないこと: 旧版の素材（foragg_csv/）、成績表に odds5p
    old = b / "foragg_csv"
    old.mkdir(exist_ok=True)
    (old / "foragg_normal_主５場（中山、東京、中京、京都、阪神）_SAV.csv.gz").write_bytes(b"dummy")
    # 合成データは1キーあたり年に数件しかないので、adjusted_WEDGE の年の最低件数を下げて通す
    assert aggregate_stats_ver2.main(["--clean", *AGG_TEST_ARGS]) == 0
    from shisu.aggstore import read_agg_tables
    from shisu.csvio import write_csv
    name, table = next((n, t) for n, t in read_agg_tables(b / "agg_csv").items()
                       if n.startswith("foragg_normal_") and n.endswith("SAV.csv"))
    write_csv(table, b / "agg_csv" / ("foragg_odds5p_" + name[len("foragg_"):]))     # 旧来の形・旧仕様の名前
    assert calc_entry_hensachi.main(["--merge-one"]) == 0
    assert build_db.main(["--fresh"]) == 0
    return b


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def db(base):
    return sqlite3.connect(base / "racing.db")


def test_outputs_exist(base):
    from shisu import families as fam_registry
    from shisu.aggstore import ROW_META, read_agg_master, read_agg_tables
    from shisu.naming import file_stem, parse_foragg_stem
    # 素材（foragg_csv）はもう作らない。master が集計の入力
    assert [p.name for p in (base / "foragg_csv").iterdir()] == ["foragg_normal_主５場（中山、東京、中京、京都、阪神）_SAV.csv.gz"]
    masters = sorted(p.name for p in (base / "master_csv").glob("master_*.csv.gz"))
    assert len(masters) == 3
    # 成績表は master 1本につき1ファイル（旧来の1表1ファイルは作らない。fixture が置いた旧仕様の odds5p だけ）
    files = sorted(p.name for p in (base / "agg_csv").glob("agg_master_*.csv.gz"))
    assert files == ["agg_master_" + m[len("master_"):] for m in masters]
    assert [p.name for p in (base / "agg_csv").glob("foragg_*.csv")] == [
        "foragg_odds5p_normal_主５場（中山、東京、中京、京都、阪神）_SAV.csv"]
    one = read_agg_master(base / "agg_csv" / files[0])
    assert set(ROW_META) <= set(one.columns) and (one["テーブル名"] != "").all()
    agg = read_agg_tables(base / "agg_csv")
    infos = [parse_foragg_stem(file_stem(n)) for n in agg]
    variants = sum(len(f.schemes) for f in fam_registry.active())
    assert len(fam_registry.active()) == 83 and variants == 129
    assert all(infos) and len(agg) <= 3 * variants * 4                  # 0件の成績表は書かない
    normal = {i["family"] for i in infos if i["mode"] == "normal"}
    assert {f.name for f in fam_registry.active() if f.layer == "A"} <= normal
    assert not [n for n in agg if n.startswith("foragg_odds10u_odds10u_")]
    assert (base / "agg_csv" / "families.json").exists() and (base / "agg_csv" / "agg_settings.json").exists()
    assert len(list((base / "replace_csv").glob("processed_*.csv"))) == 3
    m = pd.read_csv(next((base / "master_csv").glob("master_*ZI.csv.gz")), encoding="cp932", dtype=str,
                    keep_default_na=False)
    assert {"year", "日付", "指数有効", "オッズ帯", "順位帯_s1", "順位帯_s2"} <= set(m.columns)
    assert set(m["year"]) == {"2022", "2023", "2024", "2025"}


def test_master_columns_and_bands(base):
    from shisu.conditions import CONDITION_COLUMNS
    m = pd.read_csv(next((base / "master_csv").glob("master_*SAV.csv.gz")), encoding="cp932", dtype=str,
                    keep_default_na=False)
    assert set(CONDITION_COLUMNS) <= set(m.columns)
    assert set(m["順位帯_s1"]) <= {"1位", "2~3位", "4~5位", "6~9位", "10位~", ""}
    assert set(m["順位帯_s2"]) <= {"1~3位・1~5位", "1~5位", "6位~", ""}
    assert (m.loc[m["順位"] == "2", "順位帯_s2"] == "1~3位・1~5位").all()
    # 22条件のうち、合成データで値が入るもの（前走は約9割の馬にある）
    for c in ("時期", "輸送距離", "間隔帯", "叩き", "R値帯", "直線帯", "距離帯", "左右", "坂", "斤量帯", "牝馬限定",
              "前走着順", "前走人気", "前走脚質", "距離増減", "斤量増減", "左右遷移"):
        assert (m[c] != "").mean() > 0.5, c
    # 前走のコースがマスタに無い（大井・その場に無い距離）馬は前走クワドラントが空
    assert (m["前走クワドラント"] != "").mean() > 0.3


def test_aggregate_does_not_touch_master_and_is_rerunnable(base):
    import aggregate_stats_ver2
    src = next((base / "master_csv").glob("master_*SAV.csv.gz"))
    before = src.read_bytes()
    outs = sorted((base / "agg_csv").glob("agg_master_*.csv.gz"))
    assert len(outs) == 3
    first = [o.read_bytes() for o in outs]
    assert aggregate_stats_ver2.main(AGG_TEST_ARGS) == 0
    assert src.read_bytes() == before and [o.read_bytes() for o in outs] == first


def test_odds_split_counts(base):
    """オッズ帯3区分の件数の合計は、オッズ欠損が無ければ通常と一致する（0件の帯はファイルが無い）。"""
    from shisu.aggstore import read_agg_tables
    tables = read_agg_tables(base / "agg_csv")

    def total(mode):                                   # normal は初期セットのコース固定（layer A）なので全部の行が残る
        prefix = "" if mode == "normal" else mode + "_"
        t = tables.get(f"foragg_{prefix}normal_主５場（中山、東京、中京、京都、阪神）_SAV.csv")
        return pd.to_numeric(t["件数"]).sum() if t is not None else 0
    assert 0 < total("odds10u") + total("odds10_50") + total("odds50p") <= total("normal")


def test_no_broken_keys(base):
    with db(base) as con:
        n = con.execute("SELECT COUNT(*) FROM unified_all WHERE \"結合データ\" LIKE '%nan%' "
                        "OR \"結合データ\" = '' OR \"結合データ\" IS NULL").fetchone()[0]
        assert n == 0
        # 全頭0・欠損2頭のレースは使わない（各指数 4年×2レース×16頭 = 128頭が除外される）
        total = con.execute("SELECT SUM(\"件数\") FROM unified_all WHERE family='normal' AND odds_mode='normal' "
                            "AND indicator='SAV'").fetchone()[0]
        assert total <= 640


def test_table_names_and_meta(base):
    with db(base) as con:
        # 1表ずつのテーブルは作らない。集計表の名前は __tables_meta__ と unified_all の「テーブル名」に持つ
        assert not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg%'").fetchall()
        names = {r[0] for r in con.execute("SELECT table_name FROM __tables_meta__ WHERE kind='foragg'")}
        assert names and not any(n.startswith("foragg_foragg_") for n in names)
        assert names == {r[0] for r in con.execute('SELECT DISTINCT "テーブル名" FROM unified_all')}
        n_meta = con.execute("SELECT SUM(n_rows) FROM __tables_meta__ WHERE kind='foragg'").fetchone()[0]
        assert n_meta == con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0]
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'unified_all__build'").fetchone()
        modes = {r[0] for r in con.execute("SELECT DISTINCT odds_mode FROM __tables_meta__ WHERE kind='foragg'")}
        assert modes == {"normal", "odds10u", "odds10_50", "odds50p"}
        inds = {r[0] for r in con.execute("SELECT DISTINCT indicator FROM unified_all")}
        assert inds == {"SAV", "ZI", "先行"}


def test_entry_keys_hit_history(base):
    """旧版で当たらなかったケース（ダートの空欄内外・G1・髙・京都外回り・順位帯）が照合される。"""
    with db(base) as con:
        x = "entries_xjoin_merged_hensachi"
        hit = lambda like: con.execute(f'SELECT COUNT(*) FROM "{x}" WHERE "key" LIKE ?', (like,)).fetchone()[0]
        assert hit("東京×ダ×1600×定量×1・2_勝×%") > 0
        assert hit("東京×芝×23-24×定量×G1×%") > 0
        assert hit("%×髙杉吏麒×%") > 0
        assert hit("京都×芝×14-18×外×%") > 0
        bands = {r[0] for r in con.execute(f'SELECT DISTINCT rank_scheme FROM "{x}" WHERE family LIKE \'%_band\'')}
        assert bands == {"s1", "s2"}
        # 先行力（出馬表）→ 先行（過去側）
        assert con.execute(f'SELECT COUNT(*) FROM "{x}" WHERE base = \'先行\'').fetchone()[0] > 0


def test_merge_fills_blanks(base):
    df = pd.read_csv(base / "entries_out" / "entries_merged_hensachi.csv", encoding="cp932", dtype=str,
                     keep_default_na=False)
    assert len(df) == 48
    assert (df["先行_偏差値帯"] != "").all() and (df["SAV_偏差値帯"] != "").all()
    assert (df["先行_順位帯_s1"] != "").all()


def test_entries_normalization(base):
    with db(base) as con:
        rows = con.execute('SELECT "場 R", "日付", "重量種別", "クラス名", "距離", "芝(内・外)" FROM entries_merged_hensachi '
                           'GROUP BY "場 R"').fetchall()
    got = {r[0]: r[1:] for r in rows}
    assert got["東1"] == ("20260531", "定量", "1・2_勝", "1600", "")
    assert got["東11"] == ("20260531", "定量", "G1", "23-24", "")
    assert got["京9"] == ("20260531", "定量", "1・2_勝", "14-18", "外")


def _app(base, monkeypatch, db_path=None):
    import app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", str(db_path or base / "racing.db"))
    app_module.app.config["TESTING"] = True
    return app_module


def _race_id(base, table="entries_merged_hensachi"):
    with db(base) as con:
        return con.execute(f'SELECT "レースID(新/馬番無)" FROM "{table}" WHERE "場 R"=\'東1\' LIMIT 1').fetchone()[0]


def test_app_routes(base, monkeypatch):
    am = _app(base, monkeypatch)
    c = am.app.test_client()
    assert c.get("/healthz").data == b"ok"
    assert c.get("/").status_code == 200
    r = c.get("/", query_string={"submitted": "1", "family": "normal", "odds_mode": "odds50p", "preset": "none"})
    assert r.status_code == 200 and "50倍以上" in r.get_data(as_text=True)
    for preset in ("abs", "axis", "opp", "keshi"):
        assert c.get("/", query_string={"submitted": "1", "preset": preset, "ind": "SAV"}).status_code == 200
    page = c.get("/entries").get_data(as_text=True)
    assert "entries_all" not in page and "20260531" in page
    rid = _race_id(base)
    assert c.get("/entries/race", query_string={"table": "entries_merged_hensachi", "rid": rid}).status_code == 200
    assert c.get("/entries/key", query_string={"key": "東京×ダ×1600"}).status_code == 200
    hits = c.get("/entries/hits", query_string={"table": "entries_merged_hensachi", "rid": rid}).get_data(as_text=True)
    assert "照合: xjoin" in hits
    # 旧リンク（ridn=場 R）も開ける
    assert c.get("/entries/hits", query_string={"table": "entries_merged_hensachi", "ridn": "東1"}).status_code == 200
    assert c.get("/entries/hits", query_string={"table": "nope", "rid": rid}).status_code == 302


def test_xjoin_and_on_the_fly_agree(base, monkeypatch, tmp_path):
    """xjoin（事前計算）と その場照合 の分類結果が一致する。"""
    import build_db
    no_x = tmp_path / "noxjoin.db"
    shutil.copy(base / "racing.db", no_x)
    assert build_db.main(["--mode", "entries", "--no-xjoin", "--db-path", str(no_x)]) == 0
    am = _app(base, monkeypatch)
    results = {}
    for name, path in (("x", base / "racing.db"), ("f", no_x)):
        monkeypatch.setattr(am, "DB_PATH", str(path))
        with am.connect() as con:
            for t in ("entries_merged_hensachi", "entries_shisu_20260531_1_hensachi"):
                for rid in [r[0] for r in con.execute(f'SELECT DISTINCT "レースID(新/馬番無)" FROM "{t}"')]:
                    race = am._race_rows(con, t, rid)
                    hits, path_used = am.collect_hits(con, t, race)
                    assert path_used == ("xjoin" if name == "x" else "on-the-fly")
                    b = am.classify_hits(hits)
                    results.setdefault((t, rid), {})[name] = {
                        c: sorted((h["馬番"], h["base"], h["family"], h["key"], h["テーブル名"]) for h in v)
                        for c, v in b.items()}
    assert results
    for k, v in results.items():
        assert v["x"] == v["f"], k


def test_prune_entries_keeps_foragg(base, tmp_path):
    import build_db
    p = tmp_path / "prune.db"
    shutil.copy(base / "racing.db", p)
    count = lambda con: (con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0],          # noqa: E731
                         con.execute("SELECT COUNT(*) FROM __tables_meta__ WHERE kind='foragg'").fetchone()[0])
    with sqlite3.connect(p) as con:
        before = count(con)
        con.execute('CREATE TABLE "entries_old_stale" (x TEXT)')
        con.execute('CREATE TABLE "entries_xjoin_old_stale" (x TEXT)')
    assert build_db.main(["--mode", "entries", "--prune", "--db-path", str(p)]) == 0
    with sqlite3.connect(p) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        after = count(con)
    assert before[0] > 0 and before[1] > 0 and after == before       # 成績表（unified_all と集計表の一覧）は消えない
    assert "entries_old_stale" not in names and "entries_xjoin_old_stale" not in names
    assert "entries_all" in names and "unified_all" in names


def test_real_entries_sample(tmp_path):
    """同梱サンプル（本番の出馬表）を新しい calc で処理できる。全頭0の keibaAI 指数が無いので安全側。"""
    import calc_entry_hensachi
    src = ROOT / "entries_in" / "shisu_20260531_1.csv"
    if not src.exists():
        pytest.skip("サンプル CSV がありません（イメージ単体で実行した場合）")
    out_dir = tmp_path / "out"
    assert calc_entry_hensachi.main(["--in-dir", str(src.parent), "--out-dir", str(out_dir)]) == 0
    df = pd.read_csv(out_dir / "shisu_20260531_1_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False)
    assert {"Gold_偏差値", "ZI_順位帯_s1", "オメガ指数_順位", "かよちん指数_偏差値帯", "タイムM_偏差値"} <= set(df.columns)
    assert (df["タイムM_偏差値"] == "").all()             # マイニング値が全空 → 空欄
    assert (df["Gold_順位"] == df["指数順位1"]).all()        # 元の順位列を使う
    assert (df["ZI_順位"] != "").sum() > 300


# ---------------------------------------------------------------- レビュー指摘の回帰防止
def _old_schema_db(path):
    """旧版の build_db が作った形（meta は2列、unified_all に family/indicator 列が無い）。"""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE __tables_meta__ (table_name TEXT PRIMARY KEY, source_file TEXT)")
    con.execute('CREATE TABLE unified_all ("結合データ" TEXT, "テーブル名" TEXT, "件数" INT)')
    con.commit()
    con.close()


def test_old_schema_db_is_detected(base, monkeypatch, tmp_path, capsys):
    import build_db
    old = tmp_path / "old.db"
    _old_schema_db(old)
    assert build_db.main(["--mode", "entries", "--db-path", str(old)]) == 1
    assert "旧形式" in capsys.readouterr().err
    am = _app(base, monkeypatch, old)
    c = am.app.test_client()
    for u in ("/", "/?submitted=1&family=normal", "/entries"):
        r = c.get(u)
        assert r.status_code == 200 and "旧形式" in r.get_data(as_text=True), u


def test_empty_agg_dir_does_not_wipe_db(base, tmp_path):
    import build_db
    p = tmp_path / "keep.db"
    shutil.copy(base / "racing.db", p)
    empty = tmp_path / "empty_agg"
    empty.mkdir()
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(empty), "--db-path", str(p)]) == 2
    with sqlite3.connect(p) as con:
        assert con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0] > 0
    # master のディレクトリを指定しても同じ（.csv.gz は成績表として読まない）
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(base / "master_csv"), "--db-path", str(p)]) == 2


def test_entries_without_race_id_are_split_by_race(base, monkeypatch, tmp_path):
    import build_db
    src = pd.read_csv(base / "entries_out" / "shisu_20260531_1_hensachi.csv", encoding="cp932", dtype=str,
                      keep_default_na=False).drop(columns=["レースID(新)"])
    d = tmp_path / "entries_norid"
    synth.write_cp932(src, d / "norid_hensachi.csv")
    p = tmp_path / "norid.db"
    shutil.copy(base / "racing.db", p)
    assert build_db.main(["--mode", "entries", "--entries-dir", str(d), "--db-path", str(p)]) == 0
    with sqlite3.connect(p) as con:
        races = con.execute('SELECT "レースID(新/馬番無)", COUNT(*) FROM entries_norid_hensachi GROUP BY 1').fetchall()
    assert len(races) == 3 and all(n == 16 for _, n in races)


def test_sort_link_ignores_reserved_args(base, monkeypatch):
    am = _app(base, monkeypatch)
    c = am.app.test_client()
    for extra in ("_scheme=x", "endpoint=x", "_anchor=x", "_external=1"):
        r = c.get(f"/?submitted=1&ind=SAV&{extra}")
        assert r.status_code == 200, extra
        assert "http://" not in r.get_data(as_text=True).split("<table>")[-1][:2000]


# ---------------------------------------------------------------- WEDGE（Phase 3）
WEDGE_COLS = ("WIN", "EDGE", "WEDGE", "adjusted_WEDGE", "adjusted_WEDGE_年数", "adjusted_WEDGE_件数",
              "adjusted_WEDGE_最終年")


def test_wedge_columns_in_agg_csv_match_reference(base):
    """成績表の WEDGE 列が、仕様どおりの素直な実装（1グループずつ計算）と一致する。"""
    from shisu import wedge
    from shisu.finish import parse_finish
    import aggregate_stats_ver2 as a
    from shisu import families as fam_registry
    src = next((base / "master_csv").glob("master_*SAV.csv.gz"))
    m = a.master_frame(pd.read_csv(src, encoding="cp932", dtype=str, keep_default_na=False))
    keys = fam_registry.build_keys(m, "normal")
    mat = m.loc[keys.index].assign(結合データ=keys.to_numpy())
    fl = parse_finish(mat["着順"], mat["頭数"])
    mat = mat[fl["denom"]].assign(finish_num=fl["finish_num"][fl["denom"]])
    ref_year = int(pd.to_numeric(mat["year"]).max())
    from shisu.aggstore import read_agg_tables
    agg = read_agg_tables(base / "agg_csv")["foragg_normal_" + src.name[len("master_"):].replace(".csv.gz", ".csv")]
    assert list(agg.columns[18:25]) == list(WEDGE_COLS)
    for c in WEDGE_COLS:
        agg[c] = pd.to_numeric(agg[c])
    checked = 0
    for _, row in agg.iterrows():
        g = mat[mat["結合データ"] == row["結合データ"]]
        r = wedge.wedge_from_frame(g)
        assert row["WIN"] == pytest.approx(round(r["WIN"], 4))
        assert row["EDGE"] == pytest.approx(round(r["EDGE"], 4))
        assert row["WEDGE"] == pytest.approx(round(r["WEDGE"], 4))
        yearly, counts = {}, {}
        for y, gy in g.groupby(g["year"].astype(int)):
            yearly[y] = wedge.wedge_from_frame(gy)["WEDGE"]
            counts[y] = len(gy)
        adj = wedge.adjusted_wedge(yearly, counts, min_year_count=2, window_end=ref_year)
        if adj != adj:
            assert pd.isna(row["adjusted_WEDGE"]) and row["adjusted_WEDGE_年数"] == 0
        else:
            assert row["adjusted_WEDGE"] == pytest.approx(round(adj, 4))
            used = [y for y in counts
                    if ref_year - wedge.DEFAULT_MAX_YEARS < y <= ref_year and counts[y] >= 2]
            assert row["adjusted_WEDGE_年数"] == len(used)
            assert row["adjusted_WEDGE_件数"] == sum(counts[y] for y in used)
            assert row["adjusted_WEDGE_最終年"] == max(used)
            checked += 1
    assert checked > 10


def test_wedge_in_db_and_xjoin(base):
    with db(base) as con:
        cols = {r[1]: r[2] for r in con.execute("PRAGMA table_info(unified_all)")}
        assert all(c in cols for c in WEDGE_COLS)
        assert cols["WEDGE"] == "REAL" and cols["adjusted_WEDGE_年数"] == "INTEGER"
        n, n_w, n_adj = con.execute('SELECT COUNT(*), COUNT("WEDGE"), COUNT("adjusted_WEDGE") FROM unified_all').fetchone()
        assert n == n_w and n_adj > 0      # テストでは年の最低件数を2にしているので大半に付く
        lo, hi = con.execute('SELECT MIN("WEDGE"), MAX("WEDGE") FROM unified_all').fetchone()
        assert 0 <= lo and hi <= 2.0
        x = con.execute('SELECT COUNT("WEDGE") FROM entries_xjoin_merged_hensachi').fetchone()[0]
        assert x > 0
        plan = " ".join(str(r) for r in con.execute(
            'EXPLAIN QUERY PLAN SELECT * FROM unified_all ORDER BY "adjusted_WEDGE" DESC NULLS LAST LIMIT 10'))
        assert "idx_unified_adj_wedge" in plan


def test_ranking_view_sorted_by_adjusted_wedge(base, monkeypatch):
    am = _app(base, monkeypatch)
    with am.connect() as con:
        rows = am._rank_query(con, [], ["SAV"], "all", "", ("", "", "", ""), "none",
                              {"min_cnt": None, "min_wedge": None, "min_adj": None}, "adjusted_WEDGE", "DESC")
    vals = [r["adjusted_WEDGE"] for r in rows]
    non_null = [v for v in vals if v is not None]
    assert non_null == sorted(non_null, reverse=True)
    assert vals[: len(non_null)] == non_null                  # 空欄は最後
    assert {r["indicator"] for r in rows} == {"SAV"}
    with am.connect() as con:
        rows = am._rank_query(con, ["normal"], [], "odds50p", "", ("", "", "", ""), "none",
                              {"min_cnt": 6, "min_wedge": 0.3, "min_adj": None}, "WEDGE", "ASC")
    assert rows and all(r["WEDGE"] >= 0.3 and r["件数"] >= 6 for r in rows)
    assert [r["WEDGE"] for r in rows] == sorted(r["WEDGE"] for r in rows)
    assert {r["family"] for r in rows} == {"normal"} and {r["odds_mode"] for r in rows} == {"odds50p"}


def test_ranking_preset_matches_classify(base, monkeypatch):
    """横断ランキングのプリセットは、オッズ帯ごとの判定（消し候補の2〜10倍未満ルール）まで classify と一致する。"""
    from shisu.classify import classify
    am = _app(base, monkeypatch)
    am.RANK_LIMIT = 10_000
    try:
        with am.connect() as con:
            for preset in ("keshi", "opp"):
                got = am._rank_query(con, [], [], "all", "", ("", "", "", ""), preset,
                                     {"min_cnt": None, "min_wedge": None, "min_adj": None}, "件数", "DESC")
                every = [dict(r) for r in con.execute(
                    'SELECT "テーブル名", "結合データ", "件数", "複勝率_val", "連対率_val", "掲示板率_val", '
                    '"複勝回収値", "単勝適正回収値", "勝率_val", "WEDGE", odds_mode FROM unified_all')]
                want = {(r["テーブル名"], r["結合データ"]) for r in every if classify(
                    {"cnt": r["件数"], "fv": r["複勝率_val"], "rv": r["連対率_val"], "top5": r["掲示板率_val"],
                     "frc": r["複勝回収値"], "tra": r["単勝適正回収値"], "key": r["結合データ"],
                     "wv": r["勝率_val"], "wedge": r["WEDGE"]},
                    r["odds_mode"]) == preset}
                assert {(r["テーブル名"], r["結合データ"]) for r in got} == want, preset
    finally:
        am.RANK_LIMIT = 500


def test_index_page_wedge_views(base, monkeypatch):
    am = _app(base, monkeypatch)
    c = am.app.test_client()
    r = c.get("/", query_string={"submitted": "1", "view": "rank", "ind": "ZI"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "横断ランキング（adjusted_WEDGE 大きい順" in html
    r = c.get("/", query_string={"submitted": "1", "view": "tables", "ind": "ZI", "family": "normal",
                                 "sort": "WEDGE", "sortdir": "DESC", "min_wedge": "0.6", "min_cnt": "x"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "WEDGE ▼" in html
    assert 'name="min_cnt" value="x"' in html and "数値として読めませんでした" in html   # 読めない値は残して警告
    rid = _race_id(base)
    hits = c.get("/entries/hits", query_string={"table": "entries_merged_hensachi", "rid": rid}).get_data(as_text=True)
    assert "adjusted_WEDGE" in hits


def test_phase2_db_without_wedge_is_old(base, tmp_path):
    """WEDGE 列の無い unified_all（Phase 2 の DB）は旧形式として扱う（作り直しを促す）。"""
    from shisu.matching import unified_status
    p = tmp_path / "p2.db"
    shutil.copy(base / "racing.db", p)
    with sqlite3.connect(p) as con:
        con.execute("ALTER TABLE unified_all DROP COLUMN adjusted_WEDGE_最終年")
        assert unified_status(con) == "old"


def test_unified_all_without_a_classify_column_is_old(base, tmp_path):
    """区分の判定が読む列（勝率_val など）の無い unified_all は「ok」にしない: SQLite は表に無い列名の二重引用符を文字列として
    読むので、横断検索のプリセット（SQL 版）の「勝率_val ≥ 40」が全部の行で真になってしまう。"""
    from shisu import classify as C
    from shisu.matching import REQUIRED_UNIFIED_COLUMNS, unified_status
    assert set(C.FIELD_COLUMNS.values()) <= set(REQUIRED_UNIFIED_COLUMNS)
    with sqlite3.connect(base / "racing.db") as con:
        assert unified_status(con) == "ok"
    p = tmp_path / "nowin.db"
    shutil.copy(base / "racing.db", p)
    with sqlite3.connect(p) as con:
        n = con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0]
        con.execute('ALTER TABLE unified_all DROP COLUMN "勝率_val"')
        assert unified_status(con) == "old"
        # 列が無いと、SQL 版の「勝率_val ≥ 40」は NULL でも偽でもなく真になる（だから「ok」にしない）
        assert con.execute('SELECT COUNT(*) FROM unified_all WHERE IFNULL("勝率_val" >= 40.0, 0)').fetchone()[0] == n > 0


def test_build_db_rejects_agg_csv_without_wedge(base, tmp_path, capsys):
    """WEDGE 列の無い旧形式の成績表は取り込まない（WEDGE が全部空の DB を「正常」として作らない）。"""
    import build_db
    from shisu.aggstore import read_agg_master
    from shisu.csvio import write_csv
    agg = tmp_path / "agg"
    shutil.copytree(base / "agg_csv", agg)
    old = next(agg.glob("agg_master_*SAV.csv.gz"))
    write_csv(read_agg_master(old).drop(columns=list(WEDGE_COLS)), old)
    p = tmp_path / "x.db"
    assert build_db.main(["--mode", "foragg", "--fresh", "--agg-dir", str(agg), "--db-path", str(p)]) == 1
    assert "旧形式の成績表" in capsys.readouterr().err
    with sqlite3.connect(p) as con:
        assert con.execute("SELECT COUNT(*) FROM __tables_meta__ WHERE source_file = ?", (old.name,)).fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM unified_all WHERE "WEDGE" IS NULL').fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0] > 0
    # 旧来の1表1ファイルの形でも、WEDGE 列が無ければ取り込まない（読める成績表が無いので DB は変えずに終了コード2）
    from shisu.aggstore import ROW_META
    legacy = read_agg_master(old)
    legacy = legacy[(legacy["family"] == "normal") & (legacy["odds_mode"] == "normal")].drop(columns=list(ROW_META))
    for f in agg.glob("agg_master_*.csv.gz"):
        f.unlink()
    synth.write_cp932(legacy, agg / f"foragg_normal_{old.name[len('agg_master_'):-len('.csv.gz')]}.csv")
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(agg), "--db-path", str(p)]) == 2


def test_old_xjoin_falls_back_to_on_the_fly(base, monkeypatch, tmp_path):
    p = tmp_path / "oldx.db"
    shutil.copy(base / "racing.db", p)
    with sqlite3.connect(p) as con:
        con.execute('ALTER TABLE entries_xjoin_merged_hensachi DROP COLUMN "adjusted_WEDGE_最終年"')
    am = _app(base, monkeypatch, p)
    with am.connect() as con:
        race = am._race_rows(con, "entries_merged_hensachi", _race_id(base))
        hits, path = am.collect_hits(con, "entries_merged_hensachi", race)
    assert path.startswith("on-the-fly（") and hits and all("WEDGE" in h for h in hits)


def test_rank_view_form_defaults_and_inputs(base, monkeypatch):
    am = _app(base, monkeypatch)
    c = am.app.test_client()
    # フォームは並べ替え欄を空で送る → まとめて表示の既定（adjusted_WEDGE）
    html = c.get("/", query_string={"submitted": "1", "view": "rank", "sort": "", "sortdir": "DESC",
                                    "odds_mode": "all", "preset": "none"}).get_data(as_text=True)
    assert "横断ランキング（adjusted_WEDGE 大きい順" in html
    # 全角の小数点・カンマ区切りも読む。読めない値は警告
    html = c.get("/", query_string={"submitted": "1", "view": "rank", "min_wedge": "０．５",
                                    "min_cnt": "1,0", "min_adj": "abc"}).get_data(as_text=True)
    assert 'name="min_wedge" value="0.5"' in html and 'name="min_cnt" value="10"' in html
    assert "数値として読めませんでした" in html and "abc" in html
    # 表示列に無い列で並べたら、その列も表示する
    html = c.get("/", query_string={"submitted": "1", "view": "rank", "sort": "平均着順",
                                    "sortdir": "ASC"}).get_data(as_text=True)
    assert ">平均着順 ▲<" in html


def test_rank_query_avoids_sort_index_when_filtered(base, monkeypatch):
    am = _app(base, monkeypatch)
    none = {"min_cnt": None, "min_wedge": None, "min_adj": None}
    seen = []
    with am.connect() as con:
        con.set_trace_callback(seen.append)
        am._rank_query(con, [], [], "all", "", ("", "", "", ""), "none", none, "WEDGE", "DESC")
        am._rank_query(con, [], [], "all", "", ("", "", "", ""), "none", {**none, "min_wedge": 0.5}, "WEDGE", "DESC")
        am._rank_query(con, [], ["ZI"], "all", "", ("", "", "", ""), "none", none, "WEDGE", "DESC")
        am._rank_query(con, [], [], "all", "", ("", "", "", ""), "none", {**none, "min_cnt": 10}, "WEDGE", "DESC")
    orders = [re.search(r'ORDER BY (\S+)', sql).group(1) for sql in seen if "FROM unified_all" in sql]
    assert orders == ['"WEDGE"', '"WEDGE"', '+"WEDGE"', '+"WEDGE"']


# ---------------------------------------------------------------- レース別 WEDGE 一覧
def test_entries_wedge_lists_all_hits_without_threshold(base, monkeypatch):
    """/entries/wedge は閾値で足切りせず、そのレースの全馬 × 該当する全キーを出す（CSV は全行）。"""
    import csv as _csv
    import io
    am = _app(base, monkeypatch)
    c = am.app.test_client()
    t = "entries_merged_hensachi"
    rid = _race_id(base)
    with am.connect() as con:
        race = am._race_rows(con, t, rid)
        hits, _ = am.collect_hits(con, t, race)
    every = am.dedupe_hits(hits)
    classified = [h for v in am.classify_hits(hits).values() for h in v]
    assert len(every) > len(classified) > 0                  # 分類（閾値あり）より多い＝足切りしていない
    page = c.get("/entries/wedge", query_string={"table": t, "rid": rid, "odds_mode": "all"}).get_data(as_text=True)
    assert f"全該当キー（{len(every)}行" in page and "馬 × 指数" in page
    for r in race:                                            # 全馬が要約表に出る
        assert f"<td>{r['馬名']}</td>" in page
    r = c.get("/entries/wedge.csv", query_string={"table": t, "rid": rid, "odds_mode": "all"})
    assert r.status_code == 200 and r.mimetype == "text/csv"
    rows = list(_csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert len(rows) == len(every)
    ident = lambda h: (str(h["馬番"]), h["base"], h["family"], h["key"], h["テーブル名"])
    assert {ident(h) for h in rows} == {ident(h) for h in every}
    vals = [float(h["adjusted_WEDGE"]) for h in rows if h["adjusted_WEDGE"] != ""]
    assert vals == sorted(vals, reverse=True)                  # 既定は adjusted_WEDGE の大きい順、空欄は最後
    blanks = [i for i, h in enumerate(rows) if h["adjusted_WEDGE"] == ""]
    assert not blanks or blanks[0] == len(vals)
    # 既定のオッズ帯は「通常」
    r = c.get("/entries/wedge.csv", query_string={"table": t, "rid": rid})
    normal = list(_csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert normal and {h["odds_mode"] for h in normal} == {"normal"}
    # 馬番・指数・パターンで絞る
    uma = str(race[0]["馬番"])
    r = c.get("/entries/wedge.csv", query_string={"table": t, "rid": rid, "odds_mode": "all", "uma": uma,
                                                  "ind": "SAV", "family": "rankJ_band"})
    sub = list(_csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert sub and all(h["馬番"] == uma and h["base"] == "SAV" and h["family"] == "rankJ_band" for h in sub)
    assert len(sub) == sum(1 for h in every if ident(h)[0] == uma and h["base"] == "SAV"
                           and h["family"] == "rankJ_band")
    # 並べ替え（WEDGE の小さい順）と不正な引数
    r = c.get("/entries/wedge.csv", query_string={"table": t, "rid": rid, "odds_mode": "all", "sort": "WEDGE",
                                                  "dir": "ASC"})
    w = [float(h["WEDGE"]) for h in _csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))) if h["WEDGE"] != ""]
    assert w == sorted(w)
    assert c.get("/entries/wedge", query_string={"table": t, "rid": rid, "sort": "x;drop", "odds_mode": "?"}).status_code == 200
    assert c.get("/entries/wedge", query_string={"table": "nope", "rid": rid}).status_code == 302
    for extra in ("_anchor", "_scheme", "_method", "endpoint", "_external"):   # url_for の予約引数で 500 にならない
        r = c.get("/entries/wedge", query_string={"table": t, "rid": rid, extra: "1"})
        assert r.status_code == 200, extra
        assert "http://" not in r.get_data(as_text=True).split("CSV でダウンロード")[0][-300:]
    # 一覧・出馬表・条件ヒット馬からリンクがある
    assert "/entries/wedge?" in c.get("/entries").get_data(as_text=True)
    assert "/entries/wedge?" in c.get("/entries/race", query_string={"table": t, "rid": rid}).get_data(as_text=True)
    assert "/entries/wedge?" in c.get("/entries/hits", query_string={"table": t, "rid": rid}).get_data(as_text=True)


def test_entries_wedge_on_the_fly_matches_xjoin(base, monkeypatch, tmp_path):
    import build_db
    no_x = tmp_path / "noxjoin.db"
    shutil.copy(base / "racing.db", no_x)
    assert build_db.main(["--mode", "entries", "--no-xjoin", "--db-path", str(no_x)]) == 0
    am = _app(base, monkeypatch)
    c = am.app.test_client()
    rid = _race_id(base)
    q = {"table": "entries_merged_hensachi", "rid": rid, "odds_mode": "all"}
    a = c.get("/entries/wedge.csv", query_string=q).data
    monkeypatch.setattr(am, "DB_PATH", str(no_x))
    b = c.get("/entries/wedge.csv", query_string=q).data
    assert a == b and len(a.splitlines()) > 1


def test_entries_only_is_mode_entries(base, tmp_path):
    """build_db --entries-only は --mode entries と同じ（出馬表だけ取り込み直す。成績表のテーブルは残す）。"""
    import build_db
    assert build_db.parse_cli(["--entries-only"]).mode == "entries"
    assert build_db.parse_cli(["--entries-only", "--mode", "entries"]).mode == "entries"
    for bad in (["--entries-only", "--mode", "foragg"], ["--entries-only", "--fresh"]):
        with pytest.raises(SystemExit):              # 成績表を消す・作り直す指定とは一緒に使えない
            build_db.parse_cli(bad)
    p = tmp_path / "eo.db"
    shutil.copy(base / "racing.db", p)
    count = lambda con: con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0]      # noqa: E731
    with sqlite3.connect(p) as con:
        before = count(con)
    assert build_db.main(["--entries-only", "--db-path", str(p)]) == 0
    with sqlite3.connect(p) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        after = count(con)
    assert before > 0 and after == before and any(n.startswith("entries_xjoin_") for n in names)


def test_replace_parallel_restarts_workers_and_matches_sequential(base, tmp_path, monkeypatch):
    """--workers 2 でも1プロセスと同じ master。子プロセスは workers × MAX_TASKS_PER_CHILD 件ごとに
    ProcessPoolExecutor を作り直して入れ替える（max_tasks_per_child は Python 3.11・3.12 で止まるので使わない）。"""
    import concurrent.futures
    import replace_csv_ver3
    created = []

    class Recorder(concurrent.futures.ProcessPoolExecutor):
        def __init__(self, *a, **k):
            created.append(k)
            super().__init__(*a, **k)
    monkeypatch.setattr(replace_csv_ver3, "ProcessPoolExecutor", Recorder)
    monkeypatch.setattr(replace_csv_ver3, "MAX_TASKS_PER_CHILD", 1)    # 2件ごとに作り直す（ジョブは3件以上）
    out = tmp_path / "m2"
    assert replace_csv_ver3.main(["--workers", "2", "--no-raw-merge", "--master-dir", str(out)]) == 0
    assert len(created) >= 2 and all("max_tasks_per_child" not in k for k in created)
    names = sorted(p.name for p in (base / "master_csv").glob("master_*.csv.gz"))
    assert names and names == sorted(p.name for p in out.glob("master_*.csv.gz"))
    for n in names:
        a = pd.read_csv(base / "master_csv" / n, encoding="cp932", dtype=str, keep_default_na=False)
        b = pd.read_csv(out / n, encoding="cp932", dtype=str, keep_default_na=False)
        assert a.equals(b), n


def _run_or_crash(job):
    """run_parallel の試験用（子プロセスで pickle から戻せるようにモジュールの直下に置く）。"""
    import os
    if job[1] == "crash.csv":
        os._exit(1)                                 # OOM Killer に止められたのと同じ（例外を出さずに子プロセスが消える）
    return f"ok {job[1]}"


@pytest.mark.skipif(sys.platform == "win32", reason="fork が前提")
def test_replace_run_parallel_survives_killed_worker(monkeypatch):
    """子プロセスが止められても（BrokenProcessPool）、そのまとまりのジョブを [ERROR] にして残りを続ける。"""
    import multiprocessing
    import replace_csv_ver3
    if multiprocessing.get_start_method() != "fork":
        pytest.skip("試験用の関数を子プロセスに渡すのに fork が要る")
    monkeypatch.setattr(replace_csv_ver3, "_run", _run_or_crash)
    monkeypatch.setattr(replace_csv_ver3, "MAX_TASKS_PER_CHILD", 1)    # まとまり = [0,1] [2,3] [4]
    jobs = [("file", n) for n in ["j0.csv", "j1.csv", "crash.csv", "j3.csv", "j4.csv"]]
    res = replace_csv_ver3.run_parallel(jobs, 2)
    assert res[0] == "ok j0.csv" and res[1] == "ok j1.csv" and res[4] == "ok j4.csv"
    assert res[2].startswith("[ERROR] crash.csv: 子プロセスが止められました")
    assert res[3] == "ok j3.csv" or res[3].startswith("[ERROR] j3.csv: 子プロセスが止められました")


# ---------------------------------------------------------------- 画面（2026-11-12: 条件ヒット馬・WEDGE 一覧・指数クロス）
def _fake_hit(race_row, base, mode, cnt, cat, family="normal", key="東京×ダ×1600×定量×1・2_勝×1"):
    stats = {"abs": (85.0, 90.0, 95.0, 120.0), "axis": (55.0, 82.0, 92.0, 80.0), "keshi": (1.0, 3.0, 8.0, 10.0)}[cat]
    h = {c: None for c in __import__("shisu.matching", fromlist=["HIT_COLUMNS"]).HIT_COLUMNS}
    h.update({"馬番": race_row["馬番"], "枠番": race_row["枠番"], "馬名": race_row["馬名"], "騎手": race_row["騎手"],
              "base": base, "key": key, "family": family, "odds_mode": mode, "rank_scheme": "", "region": "主5場",
              "indicator": base, "テーブル名": f"t_{mode}_{family}_{base}", "件数": cnt, "連対率_val": stats[0],
              "複勝率_val": stats[1], "掲示板率_val": stats[2], "複勝回収値": stats[3], "単勝適正回収値": 50.0,
              "勝率": "", "連対率": "", "複勝率": "", "WEDGE": 0.9, "adjusted_WEDGE": 0.8})
    from shisu.classify import classify               # 作った行が、頼んだ区分になっていること（基準を変えたら件数を直す）
    assert classify({"cnt": cnt, "rv": stats[0], "fv": stats[1], "top5": stats[2], "frc": stats[3], "tra": 50.0, "key": key,
                     "wv": h["勝率_val"], "wedge": h["WEDGE"]}, mode) == cat, (cat, cnt, mode)
    return h


def test_hits_page_drops_redundant_normal_marks_hot_rows_and_scrolls(base, monkeypatch):
    """条件ヒット馬: 冗長な「通常」の行を表と馬ごとの該当数から除く・10〜50倍/50倍以上の軸は行を赤字・表は独立スクロールで見出し固定・
    指数クロス好走馬（軸が複数の指数で出ている馬）の要約と絞り込み。"""
    am = _app(base, monkeypatch)
    rid = _race_id(base)
    with am.connect() as con:
        race = am._race_rows(con, "entries_merged_hensachi", rid)
    r1, r2, r3 = race[0], race[1], race[2]
    fake = [_fake_hit(r1, "SAV", "normal", 20, "abs"), _fake_hit(r1, "SAV", "odds10_50", 18, "abs"),     # 通常は冗長 → 省く
            _fake_hit(r1, "ZI", "odds50p", 22, "axis"),
            _fake_hit(r2, "SAV", "normal", 40, "axis"), _fake_hit(r2, "SAV", "odds10u", 30, "axis"),       # 件数が離れている → 残す
            _fake_hit(r3, "ZI", "normal", 25, "keshi")]
    monkeypatch.setattr(am, "collect_hits", lambda con, table, rows: (list(fake), "xjoin"))
    c = am.app.test_client()
    q = {"table": "entries_merged_hensachi", "rid": rid, "cut_low_10u": "0"}      # 過剰人気カットは別のテストで
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert "「通常」の行 1 行は、重なるので省いています" in page
    assert page.count('<tr class="hot">') == 2 and "tr.hot td{color:#b91c1c;font-weight:700;background:#fef2f2}" in page
    assert ".scroll{max-height:520px;overflow:auto" in page and ".scroll thead th{position:sticky;top:0;z-index:2" in page
    assert page.count('<div class="scroll"') == 7                       # 指数クロス・馬ごとの該当数・4区分の表・条件特化ヒット
    assert page.count("<thead>") == page.count('<div class="scroll"')   # どの表も見出しがスクロールの枠の中
    body = page.split("馬ごとの該当数", 1)[1]
    assert "絶対軸（1）" in body and "軸候補（3）" in body and "消し候補（1）" in body and "相手候補（0）" in body
    import re as _re
    summary = body.split("</table>", 1)[0]
    row1 = _re.search(r"<tr[^>]*><td>%s</td><td>%s</td>(.*?)</tr>" % (r1["馬番"], _re.escape(r1["馬名"])), summary, _re.S).group(1)
    nums = _re.findall(r'<td class="num[^"]*">\s*(?:<b>)?([^<]*)', row1)
    # 絶対軸1（通常は数えない）・軸1・消し・相手・穴の軸2・軸の指数数2・消し(2〜10倍未満)・その指数数
    assert [v.strip() for v in nums] == ["1", "1", "", "", "2", "2", "", ""]
    # 並び: 軸候補は 2〜10倍未満 → 50倍以上 → 通常
    axis_tbl = body.split('class="card cat-axis"', 1)[1].split("</table>", 1)[0]
    order = [m for m in _re.findall(r"<td>(2〜10倍未満|10〜50倍|10~50倍|50倍以上|通常)</td>", axis_tbl)]
    assert order == [am.odds_label("odds10u"), am.odds_label("odds50p"), am.odds_label("normal")]
    # 指数クロス好走馬: 馬1は SAV と ZI の2指数で軸
    assert "指数クロス好走馬（絶対軸・軸候補が複数の指数で同時に出ている馬）：1頭" in page
    assert "：0頭" in c.get("/entries/hits", query_string={**q, "xmin": "3"}).get_data(as_text=True)
    assert "：2頭" in c.get("/entries/hits", query_string={**q, "xmin": "1"}).get_data(as_text=True)
    pair = c.get("/entries/hits", query_string={**q, "xmin": "1", "xa": "SAV", "xb": "ZI"}).get_data(as_text=True)
    assert "：1頭" in pair and "SAV と ZI の両方で軸" in pair
    only = c.get("/entries/hits", query_string={**q, "xonly": "1"}).get_data(as_text=True).split("馬ごとの該当数", 1)[1]
    assert "軸候補（1・指数クロスの該当馬だけ）" in only and "消し候補（0・指数クロスの該当馬だけ）" in only
    assert c.get("/entries/hits", query_string={**q, "xmin": "abc", "xa": "nope", "_anchor": "x"}).status_code == 200


def test_race_page_shows_indicator_cross(base, monkeypatch):
    """出馬表: 複数の指数で同時に上位（順位3位以内・偏差値61以上）の馬の要約と絞り込み。数は DB の値から独立に数える。"""
    am = _app(base, monkeypatch)
    with db(base) as con:
        con.row_factory = sqlite3.Row
        every = [dict(r) for r in con.execute("SELECT * FROM entries_merged_hensachi")]
    bases = sorted({c[:-len("_偏差値")] for c in every[0] if c.endswith("_偏差値")})
    assert len(bases) >= 2

    def tops(r, crit):
        n = 0
        for b in bases:
            try:
                rank = int(float(r.get(f"{b}_順位") or 0))
            except ValueError:
                rank = 0
            try:
                hen = float(r.get(f"{b}_偏差値"))
            except (TypeError, ValueError):
                hen = None
            by_rank, by_hen = 1 <= rank <= 3, hen is not None and hen >= 61
            n += {"rank": by_rank, "hen": by_hen, "both": by_rank and by_hen, "either": by_rank or by_hen}[crit]
        return n

    by_race = {}
    for r in every:
        by_race.setdefault(r["レースID(新/馬番無)"], []).append(r)
    rid, rows = max(by_race.items(), key=lambda kv: sum(1 for r in kv[1] if tops(r, "either") >= 2))
    c = am.app.test_client()
    q = {"table": "entries_merged_hensachi", "rid": rid}
    for crit in ("either", "rank", "hen", "both"):
        for xmin in (1, 2, 3):
            want = sum(1 for r in rows if tops(r, crit) >= xmin)
            page = c.get("/entries/race", query_string={**q, "xc": crit, "xmin": str(xmin)}).get_data(as_text=True)
            assert f"指数クロス（複数の指数で同時に上位の馬）：{want}頭" in page, (crit, xmin)
    assert sum(1 for r in rows if tops(r, "either") >= 2) >= 1, "合成データで2指数同時に上位の馬が要る"
    page = c.get("/entries/race", query_string=q).get_data(as_text=True)
    assert 'class="top3' in page and "hen61" in page and '<div class="scroll tall">' in page
    full = page.split('<div class="scroll tall">', 1)[1]
    only = c.get("/entries/race", query_string={**q, "xonly": "1"}).get_data(as_text=True).split('<div class="scroll tall">', 1)[1]
    want = sum(1 for r in rows if tops(r, "either") >= 2)
    assert full.count("<tr><td>") == len(rows) and only.count("<tr><td>") == want
    assert c.get("/entries/race", query_string={**q, "xc": "nope", "xmin": "x", "xa": "nope"}).status_code == 200


def test_wedge_page_collapses_filters_and_keeps_matrix_on_top(base, monkeypatch):
    """WEDGE 一覧: 指数・パターンの絞り込みは折りたたみ（選択中の数を見出しに）、馬 × 指数の表がすぐ下・見出し固定。列は今までどおり。"""
    am = _app(base, monkeypatch)
    rid = _race_id(base)
    c = am.app.test_client()
    q = {"table": "entries_merged_hensachi", "rid": rid}
    page = c.get("/entries/wedge", query_string=q).get_data(as_text=True)
    assert '<details class="filters" style="margin-top:6px"><summary>指数・パターンの絞り込みを開く（指数 すべて ／ パターン すべて）</summary>' in page
    assert "<details open" not in page and "details class=\"filters\" open" not in page
    d0, d1 = page.index("指数・パターンの絞り込みを開く"), page.index("</details></form>")
    box = page[d0:d1]
    assert box.count('name="ind"') >= 2 and box.count('name="family"') >= 10      # チェックボックスは折りたたみの中だけ
    assert page.count('name="ind"') == box.count('name="ind"') and page.count('name="family"') == box.count('name="family"')
    m0, k0 = page.index("馬 × 指数（該当キーの"), page.index("全該当キー（")
    assert d1 < m0 < k0
    matrix = page[m0:k0]
    assert matrix.index('<div class="scroll tall"><table><thead>') < matrix.index("<tbody>")
    for col in ("<th>adj 最大</th>", "<th>adj 平均</th>", "<th>WEDGE 最大</th>", "<th>WEDGE 平均</th>", "<th>該当数</th>"):
        assert col in matrix
    assert '<div class="scroll tall">' in page[k0:]
    sel = c.get("/entries/wedge", query_string={**q, "ind": "SAV", "family": ["normal", "rankB"]}).get_data(as_text=True)
    assert "指数・パターンの絞り込みを開く（指数 1 個を選択中 ／ パターン 2 個を選択中）" in sel
    assert "指数・パターンの選択を外す" in sel and "指数・パターンの選択を外す" not in page
    # 馬 × 指数の値は今までどおり（wedge_matrix の計算は変えていない）
    with am.connect() as con:
        race = am._race_rows(con, "entries_merged_hensachi", rid)
        hits, _ = am.collect_hits(con, "entries_merged_hensachi", race)
    rows = am.filter_wedge_hits(hits, "normal")
    m = am.wedge_matrix(race, rows, am.order_bases({h["base"] for h in rows}))
    best = max((x for x in m if x["adj_max"] is not None), key=lambda x: x["adj_max"], default=None)
    if best is not None:
        assert am.fmt(best["adj_max"], 3) in matrix and am.fmt(best["wedge_mean"], 3) in matrix


def test_hits_page_favorite_cut_keshi_counts_and_danger_badges(base, monkeypatch):
    """条件ヒット馬（2026-11-13）: 過剰人気カット（既定 ON・切り替え）、消し(2〜10倍未満) の行数と指数数、
    同じ指数で軸と消しが出ているタグ、消優勢・要注意の判定。"""
    import re as _re
    am = _app(base, monkeypatch)
    rid = _race_id(base)
    with am.connect() as con:
        race = am._race_rows(con, "entries_merged_hensachi", rid)
    r1, r2, r3, r4, r5, r6 = race[0], race[1], race[2], race[3], race[4], race[5]

    def good(h):                                    # 回収値100以上の軸
        return {**h, "複勝回収値": 130.0}
    fake = [
        # 馬1: 買える人気馬（2〜10倍未満の軸が回収値100以上で2指数・消しなし）
        good(_fake_hit(r1, "SAV", "odds10u", 30, "axis")), good(_fake_hit(r1, "ZI", "odds10u", 30, "axis")),
        # 馬2: 軸は過剰人気（回収値80）だけ → カットで軸0。絶対軸なし・消し(2〜10倍未満) 3行が2指数（SAV 2・ZI 1）→ 消優勢
        _fake_hit(r2, "SAV", "odds10u", 30, "axis"), _fake_hit(r2, "ZI", "odds10u", 30, "axis"),
        _fake_hit(r2, "SAV", "odds10u", 25, "keshi", family="rankB"), _fake_hit(r2, "SAV", "odds10u", 25, "keshi", family="rankJ"),
        _fake_hit(r2, "ZI", "odds10u", 25, "keshi", family="rankB"),
        # 馬3: 同じ指数 SAV で 絶対軸1・軸1（回収値100以上）と 消し(2〜10倍未満)1、ZI にも軸 → 軸3・消し1 → 判定なし（消しが1行だけ）
        _fake_hit(r3, "SAV", "odds10u", 12, "abs"), good(_fake_hit(r3, "SAV", "odds10u", 30, "axis", family="rankJ")),
        good(_fake_hit(r3, "ZI", "odds10_50", 30, "axis")), _fake_hit(r3, "SAV", "odds10u", 25, "keshi", family="rankB"),
        # 馬4: 軸2（SAV・ZI）・消し(2〜10倍未満)2（先行だけ）→ 2行以上で軸の合計以上 → 要注意（3行・2指数に届かないので消優勢ではない）。
        #       通常の消しは 消し(2〜10倍未満) に数えない
        good(_fake_hit(r4, "SAV", "odds10u", 30, "axis")), good(_fake_hit(r4, "ZI", "odds10u", 30, "axis")),
        _fake_hit(r4, "先行", "odds10u", 25, "keshi"), _fake_hit(r4, "先行", "odds10u", 25, "keshi", family="rankJ"),
        _fake_hit(r4, "先行", "normal", 60, "keshi", family="rankB"),
        # 馬5: 絶対軸1・消し(2〜10倍未満) 4行が2指数 → 絶対軸がある馬は消しが上回っても 要注意 まで
        _fake_hit(r5, "SAV", "odds10u", 12, "abs"),
        _fake_hit(r5, "ZI", "odds10u", 25, "keshi", family="rankB"), _fake_hit(r5, "ZI", "odds10u", 25, "keshi", family="rankJ"),
        _fake_hit(r5, "先行", "odds10u", 25, "keshi", family="rankB"), _fake_hit(r5, "先行", "odds10u", 25, "keshi", family="rankJ"),
        # 馬6: 軸1・消しは 2〜10倍未満 が1行だけで、ほかの2行は通常 → 判定は 2〜10倍未満 の行数（1）で付ける → 何も付けない
        #      （全部のオッズ帯の消し3行で比べると 要注意 になる）
        good(_fake_hit(r6, "SAV", "odds10u", 30, "axis")), _fake_hit(r6, "ZI", "odds10u", 25, "keshi"),
        _fake_hit(r6, "ZI", "normal", 60, "keshi", family="rankB"), _fake_hit(r6, "先行", "normal", 60, "keshi", family="rankB"),
    ]
    monkeypatch.setattr(am, "collect_hits", lambda con, table, rows: (list(fake), "xjoin"))
    c = am.app.test_client()
    q = {"table": "entries_merged_hensachi", "rid": rid}

    def rows_of(page, title):                       # 表の本体の行（馬番 → 行の HTML）
        tbody = page.split(title, 1)[1].split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        return {m.group(1): m.group(0) for m in _re.finditer(r"<tr[^>]*><td>(\d+)</td>.*?</tr>", tbody, _re.S)}

    def nums(row):
        return [v.strip() for v in _re.findall(r'<td class="num[^"]*">\s*(?:<b>)?([^<]*)', row)]

    # ---- 既定（引数なし）= 過剰人気カット ON
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert "過剰人気カット中" in page and "軸候補 2 行・相手候補 0 行" in page
    assert 'name="cut_low_10u" value="1" checked' in page and '<input type="hidden" name="cut_low_10u" value="0">' in page
    assert "cut_low_10u=0" in page.split("カットせずに全部表示する", 1)[0].rsplit("<a href=", 1)[1]
    body = page.split("馬ごとの該当数", 1)[1]
    assert "絶対軸（2）" in body and "軸候補（7）" in body and "消し候補（14）" in body
    # 判定の説明（消優勢 = 絶対軸なし・3行以上・2指数以上・軸より多い ／ 要注意 = 軸あり・2行以上・軸以上）
    legend = _re.sub(r"<[^>]+>", "", page.split("指数クロス好走馬（", 1)[1].split("<form", 1)[0])
    assert "⚠️消優勢 = 絶対軸が無く、消し(2〜10倍未満)が 3 行以上・2 指数以上で、軸の行数（絶対軸＋軸候補）より多い" in legend
    assert "要注意 = 軸があり、消し(2〜10倍未満)が 2 行以上で、軸の行数以上（絶対軸のある馬はここまで）" in legend
    assert "過剰人気カットで、2〜10倍未満・回収値100未満の 2 行を除いています" in body
    summ = rows_of(page, "馬ごとの該当数")
    # 列: 絶対軸・軸候補・消し候補・相手候補・穴の軸・軸の指数数・消し(2〜10倍未満)・消し(2〜10倍未満)の指数数
    assert nums(summ[r1["馬番"]]) == ["", "2", "", "", "", "2", "", ""] and "kd" not in summ[r1["馬番"]].split("</td>", 3)[2]
    assert nums(summ[r2["馬番"]]) == ["", "", "3", "", "", "", "3", "2"]
    assert 'class="kd-dominant"' in summ[r2["馬番"]] and '<span class="kd">⚠️消優勢</span>' in summ[r2["馬番"]]
    assert nums(summ[r3["馬番"]]) == ["1", "2", "1", "", "1", "2", "1", "1"] and "kd-" not in summ[r3["馬番"]]
    assert nums(summ[r4["馬番"]]) == ["", "2", "3", "", "", "2", "2", "1"]
    assert 'class="kd-caution"' in summ[r4["馬番"]] and '<span class="kd caution">要注意</span>' in summ[r4["馬番"]]
    assert nums(summ[r5["馬番"]]) == ["1", "", "4", "", "", "1", "4", "2"]
    assert 'class="kd-caution"' in summ[r5["馬番"]] and "kd-dominant" not in summ[r5["馬番"]]      # 絶対軸があるので消優勢にはしない
    assert nums(summ[r6["馬番"]]) == ["", "1", "3", "", "", "1", "1", "1"]
    assert "kd-" not in summ[r6["馬番"]] and 'class="kd' not in summ[r6["馬番"]]             # 行の色もバッジも無い
    assert "馬ごとの該当数（⚠️消優勢 1頭・要注意 2頭）" in page
    for th in ("<th class=\"num\">消し(2〜10倍未満)</th>", "<th class=\"num\">消し(2〜10倍未満)の指数数</th>"):
        assert page.count(th) == 2                                           # 指数クロス好走馬・馬ごとの該当数の両方
    # 指数クロス好走馬: 馬2 は軸が無くなったので出ない。馬3 の SAV のタグに (消1)（同じ指数で軸と消し）
    assert "：3頭" in page.split("指数クロス好走馬（", 1)[1][:60]
    cross = rows_of(page, "指数クロス好走馬（")
    assert set(cross) == {r1["馬番"], r3["馬番"], r4["馬番"]}
    tags3 = _re.sub(r"<[^>]+>", "", cross[r3["馬番"]].rsplit("<td", 1)[1])
    assert "SAV 1 / 1 (消1)" in tags3 and "ZI 0 / 1" in tags3 and "ZI 0 / 1 (消" not in tags3
    assert '<span class="tag clash">SAV 1 / 1 <span class="ks">(消1)</span></span>' in cross[r3["馬番"]]
    assert 'class="tag hotnum">ZI 0 / 1</span>' in cross[r3["馬番"]]
    assert "(消" not in cross[r1["馬番"]] and "(消" not in cross[r4["馬番"]]   # 馬4 の消しは別の指数（先行）
    assert nums(cross[r3["馬番"]])[:5] == ["2", "1", "3", "1", "1"]             # 軸の指数数・うち穴・軸の行数・消し(2〜10倍未満)・その指数数
    assert nums(cross[r4["馬番"]])[:5] == ["2", "", "2", "2", "1"] and 'class="kd-caution"' in cross[r4["馬番"]]
    # 4区分の表でも、判定の付いた馬の馬名にバッジ
    keshi_tbl = body.split('class="card cat-keshi"', 1)[1].split("</table>", 1)[0]
    assert keshi_tbl.count("⚠️消優勢") == 3 and keshi_tbl.count(">要注意<") == 7      # 馬2 の3行 ／ 馬4 の3行 + 馬5 の4行
    # ---- カット OFF（リンク・チェックを外して送ったフォーム = hidden の 0 だけ）
    off = c.get("/entries/hits", query_string={**q, "cut_low_10u": "0"}).get_data(as_text=True)
    assert "過剰人気カットは切ってあります" in off and "過剰人気カット中" not in off
    assert 'name="cut_low_10u" value="1" >' in off or 'name="cut_low_10u" value="1">' in off
    assert "cut_low_10u=1" in off.split("回収値100以上だけにする", 1)[0].rsplit("<a href=", 1)[1]
    assert "軸候補（9）" in off.split("馬ごとの該当数", 1)[1]
    s_off = rows_of(off, "馬ごとの該当数")
    # カット OFF: 馬2 は軸2が戻るが、消し3行（2指数）のほうが多い → 消優勢のまま
    assert nums(s_off[r2["馬番"]]) == ["", "2", "3", "", "", "2", "3", "2"] and 'class="kd-dominant"' in s_off[r2["馬番"]]
    c_off = rows_of(off, "指数クロス好走馬（")
    assert set(c_off) == {r1["馬番"], r2["馬番"], r3["馬番"], r4["馬番"]}
    assert '<span class="tag clash">SAV 0 / 1 <span class="ks">(消2)</span></span>' in c_off[r2["馬番"]]
    assert '<span class="tag clash">ZI 0 / 1 <span class="ks">(消1)</span></span>' in c_off[r2["馬番"]]
    # ---- フォームからの送信（チェックあり = 0 と 1 の両方が届く）は ON。ほかの絞り込みも一緒に効く
    on = c.get("/entries/hits?table=entries_merged_hensachi&rid=%s&cut_low_10u=0&cut_low_10u=1&xmin=1&xonly=1" % rid).get_data(as_text=True)
    assert "過剰人気カット中" in on and "xonly=1" in on.split("カットせずに全部表示する", 1)[0].rsplit("<a href=", 1)[1]
    assert "xmin=1" in on.split("カットせずに全部表示する", 1)[0].rsplit("<a href=", 1)[1]
    assert c.get("/entries/hits", query_string={**q, "cut_low_10u": "junk"}).status_code == 200
    # リンクは指数A・B も保つ。選択肢に無い「何指数以上」（URL を手で書いた）も選ばれた状態で出る（送り直しても 1 に戻らない）
    ab = c.get("/entries/hits", query_string={**q, "xmin": "7", "xa": "SAV", "xb": "ZI"}).get_data(as_text=True)
    link = ab.split("カットせずに全部表示する", 1)[0].rsplit("<a href=", 1)[1]
    assert "xa=SAV" in link and "xb=ZI" in link and "xmin=7" in link and "cut_low_10u=0" in link
    assert '<option value="7" selected>7 指数以上</option>' in ab
    race7 = c.get("/entries/race", query_string={**q, "xmin": "7"}).get_data(as_text=True)
    assert '<option value="7" selected>7 指数以上</option>' in race7 and race7.count(" selected>") >= 1
    assert '<option value="7"' not in c.get("/entries/race", query_string=q).get_data(as_text=True)
    assert "過剰人気カットは切ってあります" in c.get("/entries/hits", query_string={**q, "cut_low_10u": "junk"}).get_data(as_text=True)
    # 出馬表のページにはこのチェックは出ない
    assert "cut_low_10u" not in c.get("/entries/race", query_string=q).get_data(as_text=True)


# ---------------------------------------------------------------- 2026-11-14: オッズ帯の境目（odds10u = 2〜10倍未満）と前の区切りの DB
def test_db_records_odds_breaks_and_old_db_gets_a_notice(base, monkeypatch, tmp_path, capsys):
    """build_db は、成績表を集計したときのオッズ帯の境目を DB に残す。今の境目（2〜10倍未満）で集計した DB には注意書きを出さず、
    前の区切り（10倍未満。1倍台も入る）で集計した DB・分からない DB では、オッズ帯を出す画面に注意書きを出す。"""
    import json
    import build_db
    from shisu import odds
    with db(base) as con:
        info = dict(con.execute("SELECT key, value FROM __build_info__"))
    assert json.loads(info["odds_breaks"]) == [2.0, 10.0, 50.0] and "odds_breaks:build" not in info
    am = _app(base, monkeypatch)
    rid = _race_id(base)
    c = am.app.test_client()
    q = {"table": "entries_merged_hensachi", "rid": rid}
    pages = {"/": {"submitted": "1", "family": "normal"}, "/entries/hits": q, "/entries/wedge": q,
             "/entries/key": {"key": "東京×ダ×1600"}}
    note = "オッズ帯の前の区切り（10倍未満"
    for url, args in pages.items():
        page = c.get(url, query_string=args).get_data(as_text=True)
        assert note not in page, url
    hits = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert "2〜10倍未満（odds10u）の軸・相手は回収値100以上のみ（過剰人気カット）" in hits
    assert "消し(2〜10倍未満)" in hits and "消し(10倍未満)" not in hits.replace("消し(2〜10倍未満)", "")
    assert am.odds_label("odds10u") == "2〜10倍未満" == odds.FAV_LABEL
    assert any("（2〜10倍未満）件数" in line for line in am.description_lines()["keshi"])
    assert any("2〜10倍未満・10〜50倍 のときだけ" in line for line in am.description_lines()["axis"])
    # 前の版で集計した成績表（印に境目が無い）から作った DB・記録の無い DB には注意書き
    agg = tmp_path / "agg"
    shutil.copytree(base / "agg_csv", agg)
    for p in agg.glob("*.done"):
        st = json.loads(p.read_text(encoding="utf-8"))
        st.pop("odds_breaks")
        p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    old = tmp_path / "old.db"
    shutil.copy(base / "racing.db", old)
    capsys.readouterr()
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(agg), "--db-path", str(old)]) == 0
    assert "オッズ帯の前の区切り（10倍未満" in capsys.readouterr().out
    norec = tmp_path / "norec.db"
    shutil.copy(base / "racing.db", norec)
    with sqlite3.connect(norec) as con:
        con.execute("DELETE FROM __build_info__ WHERE key = 'odds_breaks'")
    mixed = tmp_path / "mixed.db"                              # 一部の成績表だけ前の区切り
    first = sorted(agg.glob("*.done"))[0]
    shutil.copy(base / "agg_csv" / first.name, first)
    shutil.copy(base / "racing.db", mixed)
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(agg), "--db-path", str(mixed)]) == 0
    for path in (old, norec, mixed):
        with sqlite3.connect(path) as con:
            got = dict(con.execute("SELECT key, value FROM __build_info__")).get("odds_breaks")
        assert got in (None, "null"), path.name
        am = _app(base, monkeypatch, path)
        c = am.app.test_client()
        for url, args in pages.items():
            assert note in c.get(url, query_string=args).get_data(as_text=True), (path.name, url)
        assert note not in c.get("/entries/race", query_string=q).get_data(as_text=True)      # オッズ帯を出さない画面
        assert note not in c.get("/entries").get_data(as_text=True)
    # 今の境目で集計し直した成績表から作り直せば消える。ただし成績表だけを作り直した（--mode foragg）あとは、照合結果（xjoin）が
    # 前の成績表のままなので、照合結果を読む画面（条件ヒット馬・WEDGE 一覧）には別の注意書きを出す（--entries-only で消える）
    stale = "この出馬表の照合結果（下の行）は、今の成績表より前に作ったものです"
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(base / "agg_csv"), "--db-path", str(old)]) == 0
    am = _app(base, monkeypatch, old)
    c = am.app.test_client()
    for url in ("/entries/hits", "/entries/wedge"):
        page = c.get(url, query_string=q).get_data(as_text=True)
        assert note not in page and stale in page, url
    for url, args in (("/", pages["/"]), ("/entries/key", pages["/entries/key"]), ("/entries/race", q)):
        assert stale not in c.get(url, query_string=args).get_data(as_text=True), url      # 照合結果を読まない画面
    assert stale not in c.get("/entries/wedge.csv", query_string=q).get_data(as_text=True)
    assert build_db.main(["--entries-only", "--entries-dir", str(base / "entries_out"), "--db-path", str(old)]) == 0
    am = _app(base, monkeypatch, old)
    c = am.app.test_client()
    for url in ("/entries/hits", "/entries/wedge"):
        page = c.get(url, query_string=q).get_data(as_text=True)
        assert note not in page and stale not in page, url
    # 作ったままの DB（照合結果は今の成績表から）・記録の仕組みが無い DB（前の版）には出さない
    am = _app(base, monkeypatch)
    assert stale not in am.app.test_client().get("/entries/hits", query_string=q).get_data(as_text=True)
    noinfo = tmp_path / "noinfo.db"
    shutil.copy(base / "racing.db", noinfo)
    with sqlite3.connect(noinfo) as con:
        con.execute("DROP TABLE __build_info__")
    am = _app(base, monkeypatch, noinfo)
    c = am.app.test_client()
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert stale not in page and note in page and "照合: xjoin" in page
    with sqlite3.connect(am.DB_PATH) as con:                    # その場照合のときは出さない（今の成績表と照合している）
        assert am.xjoin_note(con, "entries_merged_hensachi", "on-the-fly") == ""


# ---------------------------------------------------------------- 2026-11-16: 指数の上限ゲート・負の相関・逆引き・危険条件メモ
def test_low_index_rows_in_the_db_are_never_axis_and_sql_presets_agree(base):
    """DB の成績表で: 指数が下位の条件（末尾が 6位~・41~50・指数順位 6 以上 …）は絶対軸・軸候補・相手候補にならない。
    横断検索のプリセット（SQL）と画面の区分（1行版）は同じ行を選ぶ。"""
    from shisu import classify as C
    from shisu.odds import ODDS_MODES
    with db(base) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute('SELECT rowid AS rid, * FROM unified_all')]
        low = [r for r in rows if C.is_low_tail(r["結合データ"])]
        assert len(low) > 100 and len(low) < len(rows)
        tails = {C.tail_token(r["結合データ"]) for r in rows}
        assert {"6~9位", "10位~", "6位~", "1位", "1~3位", "1", "6"} <= tails            # 順位帯（s1・s2）・指数順位
        assert tails & {"41~50", "40以下"} and tails & {"51~60", "61~65", "66~70", "71以上"}     # 偏差値帯
        n_gated = 0
        for mode in ODDS_MODES:
            part = [r for r in rows if r["odds_mode"] == mode]
            py = {}
            for r in part:
                py[r["rid"]] = C.classify({"cnt": r["件数"], "fv": r["複勝率_val"], "rv": r["連対率_val"], "top5": r["掲示板率_val"],
                                           "frc": r["複勝回収値"], "tra": r["単勝適正回収値"], "key": r["結合データ"],
                                           "wv": r["勝率_val"], "wedge": r["WEDGE"]}, mode)
                if C.is_low_tail(r["結合データ"]):
                    assert py[r["rid"]] in (None, "keshi")
                    # 同じ成績で末尾だけ上位の形にすると（ほかの部品＝希少条件かどうかは同じ）、軸・相手になる行がある
                    upper = C.classify({"cnt": r["件数"], "fv": r["複勝率_val"], "rv": r["連対率_val"], "top5": r["掲示板率_val"],
                                        "frc": r["複勝回収値"], "tra": r["単勝適正回収値"], "wv": r["勝率_val"],
                                        "wedge": r["WEDGE"], "key": r["結合データ"].rsplit("×", 1)[0] + "×1位"}, mode)
                    n_gated += upper in C.TAIL_GATED
                    assert py[r["rid"]] == ("keshi" if upper == "keshi" else None)
            for cat in C.CATEGORIES:
                got = {x[0] for x in con.execute(
                    f"SELECT rowid FROM unified_all WHERE odds_mode = ? AND {C.sql_condition(cat, mode)}", (mode,))}
                assert got == {k for k, v in py.items() if v == cat}, (mode, cat)
        assert n_gated > 0                                                  # 合成データにも、指数が下位なのに率の高い行がある


def test_drill_rows_match_the_counts_in_the_db(base):
    """逆引き（master から取り出した行）の件数・好走数は、成績表（DB）の 件数・複勝率（頻度）と一致する（どの family・スキーム・オッズ帯でも）。"""
    import random
    from shisu import drill
    from shisu import families as F
    with db(base) as con:
        F.set_active(F.load_from_db(con), "db")
        rows = con.execute('SELECT "結合データ", family, indicator, odds_mode, IFNULL(rank_scheme, \'\'), region, "件数", "複勝率", "勝率" '
                           'FROM unified_all').fetchall()
    rng = random.Random(16)
    groups = {}
    for r in rows:
        groups.setdefault((r[1], r[3], r[4]), []).append(r)                 # family × オッズ帯 × スキーム ごとに1〜2行
    sample = [r for g in groups.values() for r in rng.sample(g, min(2, len(g)))]
    rng.shuffle(sample)
    sample = sample[:260]
    assert len({(r[1], r[4]) for r in sample}) > 60 and {r[3] for r in sample} == {"normal", "odds10u", "odds10_50", "odds50p"}
    try:
        for key, fam, ind, mode, scheme, region, cnt, plc, win in sample:
            path = drill.find_master(region, ind)
            assert path is not None, (region, ind)
            df = drill.extract(path, fam, scheme, mode, key)
            ov = drill.overview(df)
            assert ov["n"] == cnt, (key, fam, mode, scheme, ov)
            assert f"{ov['good'] / ov['n'] * 100:.2f}%" == plc.split("（")[0], (key, plc, ov)
            wins = int((df[drill.FINISH] == 1).sum())
            assert f"{wins / ov['n'] * 100:.2f}%" == win.split("（")[0]
            ids = drill.id_lists(df)
            assert len(ids["all"]["horse"]) == cnt == len(set(ids["all"]["horse"]))
    finally:
        F.reset()


def _drill_row(base, where='odds_mode = \'normal\' AND family = \'normal\' AND "件数" >= 8'):
    with db(base) as con:
        r = con.execute('SELECT "結合データ", family, indicator, odds_mode, IFNULL(rank_scheme, \'\'), region, "件数" FROM unified_all '
                        f'WHERE {where} ORDER BY "件数" DESC, "結合データ" LIMIT 1').fetchone()
    return {"key": r[0], "family": r[1], "indicator": r[2], "odds_mode": r[3], "rank_scheme": r[4], "region": r[5]}, r[6]


def test_drill_page_shows_target_ids_summary_details_and_csv(base, monkeypatch, tmp_path):
    """/entries/drill: TARGET 貼付用のレースID（全件・好走・凡走）、コア分布のまとめ、明細の表、CSV。"""
    import json
    import re as _re
    monkeypatch.setenv("NOTES_PATH", str(tmp_path / "notes.json"))
    am = _app(base, monkeypatch)
    c = am.app.test_client()
    q, cnt = _drill_row(base)
    r = c.get("/entries/drill", query_string=q)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and f"逆引き: {q['key']}" in page and f"成績表（DB）: 件数 {cnt} " in page
    assert "件数 %d と、いまの master から取り出した件数" % cnt not in page                # DB と master が合っている
    m = _re.search(r"TARGET 貼付用のレースID（(\d+)件: 好走 (\d+)・凡走 (\d+)", page)
    n, good, bad = (int(v) for v in m.groups())
    assert n == cnt == good + bad
    horse = _re.search(r'<textarea class="ids" id="ids-horse" readonly>([^<]*)</textarea>', page).group(1).split("\n")
    race = _re.search(r'<textarea class="ids" id="ids-race" readonly>([^<]*)</textarea>', page).group(1).split("\n")
    assert len(horse) == n and all(_re.fullmatch(r"\d{18}", v) for v in horse)
    assert all(_re.fullmatch(r"\d{16}", v) for v in race) and len(set(race)) == len(race) and set(race) == {v[:16] for v in horse}
    data = json.loads(_re.search(r"var data = (\{.*?\}), kind = 'all';", page, _re.S).group(1))   # 切り替え用（JS）
    assert data["all"]["horse"] == horse and data["all"]["race"] == race
    assert (len(data["good"]["horse"]), len(data["bad"]["horse"])) == (good, bad)
    assert sorted(data["good"]["horse"] + data["bad"]["horse"]) == sorted(horse)
    assert f"好走 1〜3着のみ（{good}）" in page and f"凡走 4着以下のみ（{bad}）" in page and "全件（%d）" % n in page
    assert page.count('data-copy="ids-') == 2 and ".join('\\n')" in page and "明細CSVをダウンロード" in page
    # コア分布のまとめ: 項目ごとの行。条件の部品（場所など）は1つの値に 100%
    summ = page.split("コア分布サマリー", 1)[1].split("該当した過去走の明細", 1)[0]
    for name in ("単勝オッズ", "枠番", "馬番", "頭数", "騎手", "間隔帯", "前走着順", "前走脚質"):
        assert f"<td><b>{name}</b>" in summ, name
    place_row = summ.split("<td><b>場所</b>", 1)[1].split("</tr>", 1)[0]
    assert q["key"].split("×")[0] in place_row and ">100%<" in place_row
    assert 'class="chip core"' in summ
    # 明細の表: 好走は緑・凡走は赤の行。数は上の件数と同じ
    detail = page.split("該当した過去走の明細", 1)[1]
    assert detail.count('<tr class="good') == good and detail.count('<tr class="bad"') == bad
    for col in ("日付", "場所", "レースID(新)", "馬番", "馬名", "騎手", "単勝オッズ", "着順", "指数値", "順位", "偏差値", "前走R印3"):
        assert f"<th>{col}</th>" in detail, col
    assert horse[0] in detail
    # base= でも同じページ（条件ヒット馬の行は indicator と base が同じ）
    alt = {k: v for k, v in q.items() if k != "indicator"}
    assert c.get("/entries/drill", query_string={**alt, "base": q["indicator"]}).get_data(as_text=True) == page
    # CSV: 区分の列つき・全行・BOM つき UTF-8
    r = c.get("/entries/drill.csv", query_string=q)
    text = r.data.decode("utf-8-sig")
    lines = text.strip().split("\r\n")
    assert r.status_code == 200 and r.data[:3] == b"\xef\xbb\xbf" and lines[0].startswith("区分,") and "レースID(新)" in lines[0]
    assert len(lines) - 1 >= n and sum(line.startswith("好走,") for line in lines) == good
    assert not any(col.startswith("_") for col in lines[0].split(","))
    assert "attachment" in r.headers["Content-Disposition"]
    # 順位帯の family・オッズ帯別でも開ける
    q2, cnt2 = _drill_row(base, 'odds_mode = \'odds10u\' AND IFNULL(rank_scheme, \'\') = \'s2\' AND "件数" >= 6')
    p2 = c.get("/entries/drill", query_string=q2).get_data(as_text=True)
    assert f"TARGET 貼付用のレースID（{cnt2}件" in p2 and "2〜10倍未満" in p2
    # 引数が足りない・合わないときは、理由を出す（500 にしない）。master が無くてもメモの欄は出る
    for bad_q, msg, form in (({}, "指数・パターン・結合データを指定してください", False),
                             ({**q, "family": "nope"}, "今の DB の定義にありません", True),
                             ({**q, "odds_mode": "odds5p"}, "オッズ帯", True),
                             ({**q, "rank_scheme": "s1"}, "スキームを持たないパターン", True),
                             ({**q, "region": "どこにも無い場"}, "master がありません", True),
                             ({**q, "indicator": "../../etc/passwd"}, "master がありません", True)):
        r = c.get("/entries/drill", query_string=bad_q)
        body = r.get_data(as_text=True)
        assert r.status_code == 200 and msg in body and "TARGET 貼付用" not in body, bad_q
        assert ("危険条件・死角メモ" in body) == form
        assert c.get("/entries/drill.csv", query_string=bad_q).status_code == 404
    # 当たる馬のいない結合データ: 0件として出る
    none = c.get("/entries/drill", query_string={**q, "key": q["key"] + "×無い"}).get_data(as_text=True)
    assert "TARGET 貼付用のレースID（0件" in none and "今の DB の成績表にありません" in none
    assert not (tmp_path / "notes.json").exists()                                           # 見るだけではメモのファイルを作らない


def test_drill_cache_follows_the_odds_band_and_the_master_and_the_detail_is_capped(base, monkeypatch, tmp_path):
    """逆引きの覚えている結果は、オッズ帯・master（大きさ・更新時刻）ごと。明細の表は DRILL_VIEW_LIMIT 行まで（レースIDは全件）。
    「戻る」でラジオの選択だけが戻っても、レースIDの欄を選択に合わせ直す（pageshow）。"""
    import re as _re
    import pandas as pd
    monkeypatch.setenv("NOTES_PATH", str(tmp_path / "notes.json"))
    am = _app(base, monkeypatch)
    with db(base) as con:
        row = con.execute(
            'SELECT a."結合データ", a.family, a.indicator, a.region, a."件数", b."件数" FROM unified_all a JOIN unified_all b '
            'ON a."結合データ" = b."結合データ" AND a.family = b.family AND a.indicator = b.indicator AND a.region = b.region '
            "AND IFNULL(a.rank_scheme, '') = IFNULL(b.rank_scheme, '') "
            "WHERE a.odds_mode = 'normal' AND b.odds_mode = 'odds10u' AND IFNULL(a.rank_scheme, '') = '' "
            'AND b."件数" < a."件数" ORDER BY a."件数" DESC, a."結合データ", a.family, a.indicator LIMIT 1').fetchone()
    q = {"key": row[0], "family": row[1], "indicator": row[2], "region": row[3], "rank_scheme": "", "odds_mode": "normal"}
    n_all, n_fav = row[4], row[5]
    assert n_all > n_fav >= 5
    master = tmp_path / am.drill.find_master(q["region"], q["indicator"]).name
    shutil.copy(am.drill.find_master(q["region"], q["indicator"]), master)
    monkeypatch.setattr(am.drill, "find_master", lambda region, indicator, master_dir=None: master)
    monkeypatch.setattr(am, "_DRILL_CACHE", type(am._DRILL_CACHE)())
    c = am.app.test_client()

    def count(mode):
        page = c.get("/entries/drill", query_string={**q, "odds_mode": mode}).get_data(as_text=True)
        return int(_re.search(r"TARGET 貼付用のレースID（(\d+)件", page).group(1)), page
    # 同じ結合データを 通常 → 2〜10倍未満 → 通常 と続けて開いても、それぞれの成績表の件数
    assert [count(m)[0] for m in ("normal", "odds10u", "normal", "odds10u")] == [n_all, n_fav, n_all, n_fav]
    # 明細の表は上限まで。レースIDの欄・件数は全件のまま
    monkeypatch.setattr(am, "DRILL_VIEW_LIMIT", 3)
    n, page = count("normal")
    detail = page.split("該当した過去走の明細", 1)[1]
    assert n == n_all > 3 and len(_re.findall(r'<tr class="(?:good|bad|out)', detail)) == 3
    horse = _re.search(r'<textarea class="ids" id="ids-horse" readonly>([^<]*)</textarea>', page).group(1).split("\n")
    assert len(horse) == n_all
    # master を作り直したら（1頭ぶん減らす）、覚えている結果を使わず読み直す
    df = pd.read_csv(master, encoding="cp932", dtype=str, keep_default_na=False)
    df[df["レースID(新)"] != horse[0]].to_csv(master, index=False, encoding="cp932", compression="gzip")
    assert count("normal")[0] == n_all - 1
    # ラジオの切り替え: 馬単位・レース単位の両方の欄と件数を入れ替え、表示のたび（pageshow）に選択へ合わせ直す
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    assert "['horse', 'race'].forEach" in script and "getElementById('ids-' + t).value = data[kind][t]" in script
    assert "getElementById('n-' + t).textContent = data[kind][t].length" in script
    assert "window.addEventListener('pageshow', sync)" in script and "input[name=idkind]:checked" in script
    assert _re.search(r"addEventListener\('change', sync\)", script) and script.rstrip().endswith("})();")


def test_drill_page_warns_when_the_db_count_differs_from_the_master(base, monkeypatch, tmp_path):
    """成績表（DB）の件数と、いまの master から取り出した件数が違えば知らせる（集計のあとに master を作り直した など）。"""
    monkeypatch.setenv("NOTES_PATH", str(tmp_path / "notes.json"))
    q, cnt = _drill_row(base)
    other = tmp_path / "other.db"
    shutil.copy(base / "racing.db", other)
    with sqlite3.connect(other) as con:
        con.execute('UPDATE unified_all SET "件数" = "件数" + 3 WHERE "結合データ" = ? AND family = ? AND indicator = ? AND odds_mode = ?',
                    (q["key"], q["family"], q["indicator"], q["odds_mode"]))
    am = _app(base, monkeypatch, other)
    page = am.app.test_client().get("/entries/drill", query_string=q).get_data(as_text=True)
    assert f"成績表（DB）の件数 {cnt + 3} と、いまの master から取り出した件数 {cnt} が違います" in page


def test_notes_are_saved_and_shown_in_red_on_the_hits_and_wedge_pages(base, monkeypatch, tmp_path):
    """危険条件メモ: 逆引きページで保存 → 条件ヒット馬・WEDGE 一覧の「危険条件メモ / 調査」の列に赤の太字。DB の外の JSON に残る。"""
    import json
    import re as _re
    from urllib.parse import parse_qs, urlsplit
    notes_file = tmp_path / "condition_notes.json"
    monkeypatch.setenv("NOTES_PATH", str(notes_file))
    am = _app(base, monkeypatch)
    rid = _race_id(base)
    with am.connect() as con:
        race = am._race_rows(con, "entries_merged_hensachi", rid)
    r1, r2 = race[0], race[1]
    noted = {**_fake_hit(r1, "SAV", "odds10_50", 30, "axis"), "複勝回収値": 130.0}
    same_other_band = {**_fake_hit(r1, "SAV", "odds10u", 30, "axis", family="rankB"), "複勝回収値": 130.0}
    other_region = {**_fake_hit(r2, "ZI", "odds10_50", 30, "axis", family="rankJ"), "region": "全場", "複勝回収値": 130.0}
    keshi = _fake_hit(r2, "SAV", "normal", 60, "keshi", family="rankB", key="東京×ダ×1600×定量×1・2_勝×10位~")
    fake = [noted, same_other_band, other_region, keshi]
    monkeypatch.setattr(am, "collect_hits", lambda con, table, rows: (list(fake), "xjoin"))
    c = am.app.test_client()
    q = {"table": "entries_merged_hensachi", "rid": rid, "cut_low_10u": "0"}

    def trs(page, title):
        body = page.split(title, 1)[1].split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        return [m.group(0) for m in _re.finditer(r"<tr.*?</tr>", body, _re.S)]

    # ---- メモなし: 4区分の表に列があり、どの行も 結合データ と「調査」が別タブで逆引きへ
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert page.count("<th>年数</th><th>危険条件メモ / 調査</th></tr>") == 5 and "notetext" not in page.split("<body>", 1)[1]   # 4区分 + 条件特化ヒット
    row = next(t for t in trs(page, 'class="card cat-axis"') if "10〜50倍" in t and "SAV" in t)
    links = _re.findall(r'<a href="(/entries/drill\?[^"]+)" target="_blank">([^<]*)</a>', row)
    assert [text for _, text in links] == [noted["key"], "調査"] and links[0][0] == links[1][0]
    args = {k: v[0] for k, v in parse_qs(urlsplit(links[0][0].replace("&amp;", "&")).query, keep_blank_values=True).items()}
    assert args == {"region": "主5場", "indicator": "SAV", "family": "normal", "rank_scheme": "", "odds_mode": "odds10_50",
                    "key": noted["key"]}
    assert row.rstrip().endswith('<td class="note"><a href="%s" target="_blank">調査</a></td></tr>' % links[0][0])
    # ---- 保存（逆引きページのフォームと同じ POST）
    drill_page = c.get(links[0][0].replace("&amp;", "&")).get_data(as_text=True)
    assert "⚠️ 危険条件・死角メモ（ここに書くと条件ヒット馬の表に赤字で表示されます）" in drill_page
    assert 'action="/entries/note"' in drill_page and '<button type="submit" name="action" value="save">保存</button>' in drill_page
    for f, v in args.items():
        assert f'<input type="hidden" name="{f}" value="{v}">' in drill_page
    text = '⚠️馬体重478kg以下は危険\n前走4角10番手以下は消し <script>alert(1)</script> "x"'
    r = c.post("/entries/note", data={**args, "note": text, "action": "save"})
    assert r.status_code == 303 and "/entries/drill?" in r.headers["Location"] and "note_result=saved" in r.headers["Location"]
    saved = json.loads(notes_file.read_text(encoding="utf-8"))["notes"]
    assert len(saved) == 1 and saved[0]["note"] == text and saved[0]["region"] == "主5場" and saved[0]["odds_mode"] == "odds10_50"
    after = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "メモを保存しました。" in after and "⚠️馬体重478kg以下は危険\n前走4角10番手以下は消し &lt;script&gt;" in after
    assert "いまのメモ: この地域（主5場）だけ" in after and "condition_notes.json" in after and "このメモを削除" in after
    # ---- 条件ヒット馬: その条件の行だけ、赤の太字のメモ・title に全文・結合データのセルにも title
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page
    rows = trs(page, 'class="card cat-axis"')
    row = next(t for t in rows if "10〜50倍" in t and "SAV" in t)
    esc = "⚠️馬体重478kg以下は危険\n前走4角10番手以下は消し &lt;script&gt;alert(1)&lt;/script&gt; &#34;x&#34;"
    assert f'<td class="hasnote" title="{esc}"><a href="{links[0][0]}" target="_blank">{noted["key"]}</a></td>' in row
    assert (f'<td class="note" title="{esc}"><a class="notetext" style="color:#b91c1c;font-weight:700" href="{links[0][0]}" '
            f'target="_blank">{esc}</a> <a href="{links[0][0]}" target="_blank">調査</a></td>') in row
    assert ".notetext{color:#b91c1c;font-weight:700}" in page
    others = [t for t in rows if t is not row] + trs(page, 'class="card cat-keshi"')
    assert len(others) == 3 and all("notetext" not in t and "hasnote" not in t and ">調査</a>" in t for t in others)
    # ---- WEDGE 一覧: 年数 の右隣の列。結合データ・調査は逆引き、成績表は今までのキーの画面
    wq = {"table": "entries_merged_hensachi", "rid": rid, "odds_mode": "all"}
    wedge = c.get("/entries/wedge", query_string=wq).get_data(as_text=True)
    assert "<th>adjusted_WEDGE</th><th>年数</th><th>危険条件メモ / 調査</th><th>年の件数計</th>" in wedge
    wrows = trs(wedge, "全該当キー")
    wrow = next(t for t in wrows if "10〜50倍" in t and "SAV" in t)
    assert len(wrows) == 4 and f'<a class="notetext" style="color:#b91c1c;font-weight:700" href="{links[0][0]}" target="_blank">{esc}</a>' in wrow
    assert f'<td class="hasnote" title="{esc}"><a href="{links[0][0]}" target="_blank">' in wrow
    assert wrow.count(">調査</a>") == 1 and ">成績表</a>" in wrow and "/entries/key?" in wrow
    assert sum("notetext" in t for t in wrows) == 1
    cells = _re.findall(r"<td[^>]*>", wrow)
    assert cells[15] == f'<td class="note" title="{esc}">'                                    # 枠…adjusted_WEDGE・年数 の次（16列目）
    assert "危険条件メモ" not in c.get("/entries/wedge.csv", query_string=wq).data.decode("utf-8-sig").split("\r\n")[0]
    # ---- 全地域共通のメモ: 地域が違う同じ条件の行にも出る。その地域のメモがあればそちらを優先
    cond2 = am.condition_of(other_region)
    r = c.post("/entries/note", data={**cond2, "region": "主5場", "note": "共通メモ", "all_regions": "1", "action": "save"})
    assert r.status_code == 303 and {n["region"] for n in json.loads(notes_file.read_text(encoding="utf-8"))["notes"]} == {"主5場", ""}
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    row2 = next(t for t in trs(page, 'class="card cat-axis"') if "ZI" in t)
    assert ">共通メモ</a>" in row2 and "<td>全場</td>" in row2
    d2 = c.get("/entries/drill", query_string=cond2).get_data(as_text=True)
    assert "いまのメモ: 全地域共通" in d2 and 'name="all_regions" value="1" checked' in d2
    c.post("/entries/note", data={**cond2, "note": "全場だけ", "action": "save"})
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert ">全場だけ</a>" in next(t for t in trs(page, 'class="card cat-axis"') if "ZI" in t)
    assert "いまのメモ: この地域（全場）だけ" in c.get("/entries/drill", query_string=cond2).get_data(as_text=True)
    # 全地域共通で保存し直すと、この地域だけのメモは消える（残すと共通のメモが出ない）
    c.post("/entries/note", data={**cond2, "note": "共通に戻す", "all_regions": "1", "action": "save"})
    assert ">共通に戻す</a>" in c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert sorted(n["region"] for n in json.loads(notes_file.read_text(encoding="utf-8"))["notes"]) == ["", "主5場"]
    # ---- 消し候補の行（指数が下位の条件）にも付けられる。削除は「このメモを削除」か、空で保存
    cond3 = am.condition_of(keshi)
    c.post("/entries/note", data={**cond3, "note": "消しのメモ", "action": "save"})
    assert ">消しのメモ</a>" in next(t for t in trs(c.get("/entries/hits", query_string=q).get_data(as_text=True), 'class="card cat-keshi"'))
    r = c.post("/entries/note", data={**cond3, "note": "消しのメモ", "action": "delete"})
    assert "note_result=deleted" in r.headers["Location"] and "メモを削除しました。" in c.get(r.headers["Location"]).get_data(as_text=True)
    r = c.post("/entries/note", data={**args, "note": "   ", "action": "save"})
    assert "note_result=deleted" in r.headers["Location"]
    assert "note_result=none" in c.post("/entries/note", data={**args, "note": "", "action": "save"}).headers["Location"]
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert "消しのメモ" not in page and "馬体重478kg" not in page and len(json.loads(notes_file.read_text(encoding="utf-8"))["notes"]) == 1
    # ---- 別のサイトからの POST・条件の足りない POST は受けない
    before = notes_file.read_bytes()
    assert c.post("/entries/note", data={**args, "note": "x", "action": "save"}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/entries/note", data={**args, "note": "x"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/entries/note", data={**{k: v for k, v in args.items() if k != "key"}, "note": "x"}).status_code == 400
    assert c.get("/entries/note").status_code == 405 and notes_file.read_bytes() == before
    # DNS リバインディング（Host も Origin も別の名前）・長すぎる条件も受けない（/ingest の POST と同じ見方）。IP アドレスの Host は通す
    evil = {"Host": "evil.example:8000", "Origin": "http://evil.example:8000", "Sec-Fetch-Site": "same-origin"}
    assert c.post("/entries/note", data={**args, "note": "x", "action": "save"}, headers=evil).status_code == 403
    assert c.post("/entries/note", data={**args, "action": "delete"}, headers=evil).status_code == 403
    assert c.post("/entries/note", data={**args, "key": "k" * (am.notes_store.MAX_FIELD + 1), "note": "x"}).status_code == 400
    assert notes_file.read_bytes() == before
    lan = {"Host": "192.168.0.5:8000", "Origin": "http://192.168.0.5:8000", "Sec-Fetch-Site": "same-origin"}
    # 戻り先の URL は条件の6項目と結果だけで作る（フォームに混ぜた _external・_anchor などは使わない）
    r = c.post("/entries/note", headers=lan, data={**args, "note": "", "action": "save", "_external": "1", "_anchor": "x",
                                                   "_scheme": "ftp", "_method": "POST", "extra": "1"})
    loc = urlsplit(r.headers["Location"])
    assert r.status_code == 303 and loc.path == "/entries/drill" and not loc.fragment and not loc.scheme and not loc.netloc
    assert set(parse_qs(loc.query, keep_blank_values=True)) <= set(args) | {"note_result"} and notes_file.read_bytes() == before

    # ---- 「該当なし」の行は、足した列も含めた表の幅（見出しの列の数）
    def empty_rows(html):
        out = []
        for m in _re.finditer(r"<table>.*?</table>", html, _re.S):
            e = _re.search(r'<td colspan="(\d+)">該当なし', m.group(0))
            if e:
                out.append((int(e.group(1)), len(_re.findall(r"<th[ >]", m.group(0).split("</thead>", 1)[0]))))
        return out
    assert empty_rows(c.get("/entries/hits", query_string=q).get_data(as_text=True)) == [(25, 25), (25, 25), (27, 27)]   # 絶対軸・相手候補・条件特化ヒット
    assert empty_rows(c.get("/entries/wedge", query_string={**wq, "uma": "99"}).get_data(as_text=True)) == [(24, 24)]
    # ---- メモは DB の外: DB を別のファイルに替えても（作り直しても）同じメモが出る
    other = tmp_path / "rebuilt.db"
    shutil.copy(base / "racing.db", other)
    am2 = _app(base, monkeypatch, other)
    monkeypatch.setattr(am2, "collect_hits", lambda con, table, rows: (list(fake), "xjoin"))
    assert ">共通に戻す</a>" in am2.app.test_client().get("/entries/hits", query_string=q).get_data(as_text=True)
    # ---- メモのファイルが壊れていても画面は出る（メモなし・理由を表示）。保存はしない（人が書いたメモを上書きしない）
    notes_file.write_text('{"notes": [', encoding="utf-8")
    for url, args_ in (("/entries/hits", q), ("/entries/wedge", wq), ("/entries/drill", args)):
        body = c.get(url, query_string=args_).get_data(as_text=True)
        assert "危険条件メモを読めません" in body and "notetext" not in body.split("<body>", 1)[1], url
    r = c.post("/entries/note", data={**args, "note": "x", "action": "save"})
    assert "note_result=error" in r.headers["Location"] and notes_file.read_text(encoding="utf-8") == '{"notes": ['
    failed = c.get(r.headers["Location"]).get_data(as_text=True)
    assert _re.search(r"メモを保存できませんでした: condition_notes\.json の形を読めません（[^<]+）。直すか、別の名前に変えてください", failed)


def test_hits_page_excludes_tradeoff_keshi_and_shows_the_gate_in_the_rules(base, monkeypatch, tmp_path):
    """条件ヒット馬: 負の相関にある指数の軸ヒットがある馬の「2〜10倍未満だけの消し候補」を除き、行数を上に出す。
    区分の説明に、指数が下位の条件は対象外であることを出す。"""
    import re as _re
    monkeypatch.setenv("NOTES_PATH", str(tmp_path / "notes.json"))
    am = _app(base, monkeypatch)
    rid = _race_id(base)
    with am.connect() as con:
        race = am._race_rows(con, "entries_merged_hensachi", rid)
    r1, r2 = race[0], race[1]

    def fav_only(h):                 # 2〜10倍未満専用の規則（複勝率 < 20 かつ（掲示板率 < 35 または WEDGE < 0.350））だけで消し候補になる成績
        return {**h, "複勝率_val": 15.0, "掲示板率_val": 50.0, "WEDGE": 0.3}

    axis = {**_fake_hit(r1, "先行", "odds10_50", 30, "axis"), "複勝回収値": 130.0}
    fake = [axis,
            fav_only(_fake_hit(r1, "SAV", "odds10u", 25, "keshi")),                        # 負の相関の組に無い指数 → 残す
            fav_only(_fake_hit(r2, "ZI", "odds10u", 25, "keshi")),                         # 軸の無い馬 → 残す
            _fake_hit(r1, "ZI", "odds10u", 25, "keshi", family="rankB")]                    # 共通の規則の消し → 残す
    tradeoff = [fav_only({**_fake_hit(r1, "SAV", "odds10u", 25, "keshi", family=f), "base": b, "indicator": b})
                for f, b in (("rankJ", "瞬発"), ("rankBJ", "持続"), ("normalJ", "追走"))]   # 先行と負の相関 → 除く
    monkeypatch.setattr(am, "collect_hits", lambda con, table, rows: (list(fake + tradeoff), "xjoin"))
    c = am.app.test_client()
    q = {"table": "entries_merged_hensachi", "rid": rid}
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert "負の相関にある指数の軸ヒットにより除外した消し候補 3 行" in page
    assert "StridePlus: 先行・想定前半・ST指数 ⇔ 瞬発・想定後半・追走・持久・持続" in page and "吉馬: 吉馬_先行力 ⇔ 吉馬_末脚力" in page
    assert "安井式: 安井_スピード ⇔ 安井_スタミナ・安井_パワー・安井_根性" in page
    body = page.split("馬ごとの該当数", 1)[1]
    assert "軸候補（1）" in body and "消し候補（3）" in body
    keshi_tbl = body.split('class="card cat-keshi"', 1)[1].split("</table>", 1)[0]
    assert "瞬発" not in keshi_tbl and "持続" not in keshi_tbl and "追走" not in keshi_tbl
    summary = body.split("</table>", 1)[0]
    row1 = _re.search(r"<tr[^>]*><td>%s</td><td>%s</td>(.*?)</tr>" % (r1["馬番"], _re.escape(r1["馬名"])), summary, _re.S).group(1)
    nums = [v.strip() for v in _re.findall(r'<td class="num[^"]*">\s*(?:<b>)?([^<]*)', row1)]
    # 絶対軸・軸候補・消し候補・相手候補・穴の軸・軸の指数数・消し(2〜10倍未満)・その指数数（除いた3行は数えない → 判定も付かない）
    assert nums == ["", "1", "2", "", "1", "1", "2", "2"] and "kd-" not in row1
    # 軸が無ければ除かない（3行とも消し候補に出て、消優勢になる）
    monkeypatch.setattr(am, "collect_hits", lambda con, table, rows: (list(fake[1:] + tradeoff), "xjoin"))
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    assert "負の相関にある指数の軸ヒットにより除外した消し候補" not in page
    assert "消し候補（6）" in page.split("馬ごとの該当数", 1)[1] and "⚠️消優勢 1頭" in page
    # 区分の説明: 絶対軸・軸候補・相手候補には「指数が下位の条件は対象外」、消し候補には「絞らない」
    for cat in ("abs", "axis", "opp"):
        card = page.split(f'class="card cat-{cat}"', 1)[1].split("<table", 1)[0]
        assert "指数が下位の条件（結合データの末尾が 6~9位・10位~・6位~・41~50・40以下、または指数順位 6 位以下）は対象外" in card, cat
    keshi_card = page.split('class="card cat-keshi"', 1)[1].split("<table", 1)[0]
    assert "指数が下位の条件" not in keshi_card and "指数の順位・順位帯・偏差値帯では絞らない" in keshi_card


def test_hits_page_lists_condition_special_hits_below_the_four_tables(base, monkeypatch, tmp_path):
    """条件ヒット馬: 4区分の表の下に「条件特化ヒット」（指数が下位でも絶対軸・軸候補の基準を満たす行）。
    同じ条件は代表の1行にまとめ、元区分・指数数・指数名を出す。件数・結合データ・メモの列・指数名から逆引きを別タブで開ける。"""
    import re as _re
    from urllib.parse import parse_qs, urlsplit
    notes_file = tmp_path / "notes.json"
    monkeypatch.setenv("NOTES_PATH", str(notes_file))
    am = _app(base, monkeypatch)
    rid = _race_id(base)
    with am.connect() as con:
        race = am._race_rows(con, "entries_merged_hensachi", rid)
    r1, r2 = race[0], race[1]
    head = "騎手A×前走1着"

    def low(r, ind, mode, cnt, cat, tail, fv=None, frc=None):
        stats = {"abs": (85.0, 90.0, 95.0, 120.0), "axis": (55.0, 82.0, 92.0, 80.0)}[cat]
        h = _fake_hit(r, "SAV", mode, 60, "keshi", family="B_jk_prevfin", key=f"{head}×{tail}")
        h.update({"base": ind, "indicator": ind, "rank_scheme": "s1", "テーブル名": f"t_{mode}_{ind}_{tail}", "件数": cnt,
                  "連対率_val": stats[0], "複勝率_val": stats[1] if fv is None else fv, "掲示板率_val": stats[2],
                  "複勝回収値": stats[3] if frc is None else frc})
        assert am.hit_category(h) is None and am.low_tail_category(h) == cat
        return h
    rep = low(r1, "瞬発", "normal", 22, "axis", "10位~", fv=85.0)
    twin = {**low(r1, "先行", "normal", 31, "axis", "6~9位", fv=81.0), "テーブル名": "t_twin"}     # 同じ指数の行がもう1つ（指数数は3のまま）
    fake = [low(r1, "先行", "normal", 30, "axis", "6~9位"), twin, rep, low(r1, "SAV", "normal", 12, "abs", "6~9位", fv=84.0),
            low(r2, "ZI", "odds10u", 30, "axis", "6~9位"),                                   # 回収値 80 → 過剰人気カットの対象
            low(r2, "BC", "odds10u", 30, "axis", "6~9位", frc=130.0),
            _fake_hit(r1, "ZI", "odds10_50", 22, "axis"),                                     # 末尾が上位の軸（4区分の表）
            _fake_hit(r2, "SAV", "normal", 60, "keshi", family="rankB", key="東京×ダ×1600×定量×1・2_勝×10位~")]
    monkeypatch.setattr(am, "collect_hits", lambda con, table, rows: (list(fake), "xjoin"))
    c = am.app.test_client()
    q = {"table": "entries_merged_hensachi", "rid": rid}
    page = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    title = "条件特化ヒット（指数が下位帯でも絶対軸・軸候補の基準を満たす強い条件）"
    assert page.count(title) == 1 and page.index('class="card cat-opp"') < page.index('class="card cat-special"')   # 4区分の下
    assert f"{title}：2 行" in page and "過剰人気カットで、2〜10倍未満・回収値100未満の 1 行を除いています（まとめる前の行数）" in page
    card = page.split('class="card cat-special"', 1)[1]
    assert "6~9位・10位~・6位~・41~50・40以下・指数順位 6 位以下" in card and "複勝率_val が最大（同じなら件数が最大）の1行" in card
    assert ("<th>元区分</th><th class=\"num\">指数数</th><th>指数（末尾）</th>" in card
            and card.split("</thead>", 1)[0].rstrip().endswith("<th>年数</th><th>危険条件メモ / 調査</th></tr>"))
    rows = [m.group(0) for m in _re.finditer(r"<tr>.*?</tr>", card.split("<tbody>", 1)[1].split("</tbody>", 1)[0], _re.S)]
    assert len(rows) == 2 and len(_re.findall(r"<td[ >]", rows[1])) == 27 == len(_re.findall(r"<th[ >]", card.split("</thead>", 1)[0]))
    row = next(t for t in rows if r1["馬名"] in t)
    assert "<td>軸候補</td><td class=\"num\"><b>3</b></td>" in row and f">{head}×10位~</a>" in row       # 代表 = 複勝率が最大の行
    links = _re.findall(r'<a(?: class="tag")? href="(/entries/drill\?[^"]+)" target="_blank">(.*?)</a>', row)
    assert [t for _, t in links] == ["<b>SAV 6~9位</b>", "先行 6~9位", "瞬発 10位~", f"{head}×10位~", "22", "調査"]   # 指数は台帳の順・絶対軸は太字

    def args(url):
        return {k: v[0] for k, v in parse_qs(urlsplit(url.replace("&amp;", "&")).query, keep_blank_values=True).items()}
    want = {"region": "主5場", "indicator": "瞬発", "family": "B_jk_prevfin", "rank_scheme": "s1", "odds_mode": "normal",
            "key": f"{head}×10位~"}
    assert args(links[3][0]) == args(links[4][0]) == args(links[5][0]) == args(links[2][0]) == want
    assert args(links[0][0]) == {**want, "indicator": "SAV", "key": f"{head}×6~9位"}
    assert c.get(links[3][0].replace("&amp;", "&")).status_code == 200
    row2 = next(t for t in rows if r2["馬名"] in t)
    assert "<b>1</b>" in row2 and ">BC 6~9位</a>" in row2 and "ZI" not in row2                          # カットした行は指数数にも入らない
    # 4区分の表は今までどおり（指数が下位の行は軸にしない）
    body = page.split("馬ごとの該当数", 1)[1]
    assert "軸候補（1）" in body and "絶対軸（0）" in body and "消し候補（1）" in body
    # カット OFF: 2〜10倍未満の回収値100未満の行も入る
    off = c.get("/entries/hits", query_string={**q, "cut_low_10u": "0"}).get_data(as_text=True)
    card = off.split('class="card cat-special"', 1)[1]
    assert f"{title}：2 行" in off and "回収値100未満の 1 行を除いています" not in card
    assert ">ZI 6~9位</a>" in card and "<b>2</b>" in card
    # メモ: 代表の行のメモは赤の太字、まとめた指数のメモは指数名の枠と title に
    am.notes_store.save("瞬発", "B_jk_prevfin", "s1", "normal", "主5場", f"{head}×10位~", "代表のメモ")
    am.notes_store.save("SAV", "B_jk_prevfin", "s1", "normal", "主5場", f"{head}×6~9位", "SAV のメモ")
    card = c.get("/entries/hits", query_string=q).get_data(as_text=True).split('class="card cat-special"', 1)[1]
    assert '<a class="notetext" style="color:#b91c1c;font-weight:700"' in card and ">代表のメモ</a>" in card
    assert _re.search(r'<a class="tag hasnote" href="[^"]+" target="_blank" title="SAV のメモ"><b>SAV 6~9位</b></a>', card)
    # 指数クロスの該当馬だけにすると、この表も絞る（軸が2指数以上の馬はいない → 0 行）
    only = c.get("/entries/hits", query_string={**q, "xonly": "1"}).get_data(as_text=True)
    assert f"{title}：0 行（指数クロスの該当馬だけ）" in only and '<td colspan="27">該当なし</td>' in only
    assert "回収値100未満の 1 行を除いています（まとめる前の行数・全馬の合計）" in only.split('class="card cat-special"', 1)[1]


# ---------------------------------------------------------------- 2026-11-23: 絶対軸の条件②・2〜10倍未満の消し（勝率_val・WEDGE を読む）
def test_pages_classify_db_rows_with_their_win_rate_and_wedge(base, monkeypatch, tmp_path):
    """DB の行の 勝率_val・WEDGE が、どの画面の区分にも届く: 照合結果を読む経路・その場照合・WEDGE 一覧の CSV の「区分」列・
    逆引きの区分・横断検索のプリセット（SQL）。合成データには 勝率_val・WEDGE で区分が変わる行が無いので、DB の写しの3行を
    その値に書き換えて確かめる（① 絶対軸の条件②だけで絶対軸 ② WEDGE < 0.350 で消し ③ 同じ成績で WEDGE 0.5 → 区分なし）。"""
    import csv as _csv
    import io
    from shisu import classify as C
    from shisu import matching
    monkeypatch.setenv("NOTES_PATH", str(tmp_path / "notes.json"))
    p = tmp_path / "eng.db"
    shutil.copy(base / "racing.db", p)
    am = _app(base, monkeypatch, db_path=p)
    rid, t, xj = _race_id(base), "entries_merged_hensachi", "entries_xjoin_merged_hensachi"
    ident = lambda h: (h["テーブル名"], h["key"])
    with am.connect() as con:
        race = am._race_rows(con, t, rid)
        hits, path = am.collect_hits(con, t, race)
        assert path == "xjoin" and all("勝率_val" in h and "WEDGE" in h for h in hits)
        top = [h for h in hits if not C.is_low_tail(h["key"]) and not C.is_rare_key(h["key"])]
        two = next(h for h in top if h["odds_mode"] == "normal")
        fav = [h for h in top if h["odds_mode"] == "odds10u" and h["key"] != two["key"]]
        by_wedge = fav[0]                             # 3行は結合データも別（ページに出る・出ないを結合データで見るため）
        not_keshi = next(h for h in fav if h["key"] != by_wedge["key"])
        plans = [(two, "abs", {"件数": 22, "勝率_val": 45.0, "連対率_val": 55.0, "複勝率_val": 82.0, "掲示板率_val": 70.0,
                               "複勝回収値": 90.0, "単勝適正回収値": 110.0, "WEDGE": 0.9}),
                 (by_wedge, "keshi", {"件数": 25, "勝率_val": 3.0, "連対率_val": 8.0, "複勝率_val": 15.0, "掲示板率_val": 50.0,
                                      "複勝回収値": 60.0, "単勝適正回収値": 60.0, "WEDGE": 0.30}),
                 (not_keshi, None, {"件数": 25, "勝率_val": 3.0, "連対率_val": 8.0, "複勝率_val": 15.0, "掲示板率_val": 50.0,
                                    "複勝回収値": 60.0, "単勝適正回収値": 60.0, "WEDGE": 0.50})]
        for h, _cat, vals in plans:
            sets = ", ".join(f'"{c}" = ?' for c in vals)
            n1 = con.execute(f'UPDATE unified_all SET {sets} WHERE "テーブル名" = ? AND "結合データ" = ?',
                             [*vals.values(), h["テーブル名"], h["key"]]).rowcount
            n2 = con.execute(f'UPDATE "{xj}" SET {sets} WHERE "テーブル名" = ? AND key = ?',
                             [*vals.values(), h["テーブル名"], h["key"]]).rowcount
            assert n1 == 1 and n2 >= 1
        con.commit()
    want = {ident(h): cat for h, cat, _v in plans}
    # 値を1つ落とすと区分が変わる行であること（勝率_val・WEDGE が届いていなければ、下の確認は落ちる）
    assert C.classify({**C.record(plans[0][2], key=two["key"]), "wv": None}, "normal") is None
    assert C.classify({**C.record(plans[1][2], key=by_wedge["key"]), "wedge": None}, "odds10u") is None
    c = am.app.test_client()
    with am.connect() as con:
        race = am._race_rows(con, t, rid)
        # (1) 照合結果（xjoin）を読む経路と、その場照合
        hits, path = am.collect_hits(con, t, race)
        got = {ident(h): am.hit_category(h) for h in hits if ident(h) in want}
        assert path == "xjoin" and got == want
        fly = [h for r in race for h in matching.match_entry_row(con, r, matching.match_bases(r.keys()), t)]
        got = {ident(h): am.hit_category(h) for h in fly if ident(h) in want}
        assert got == want
        assert am.keshi_rule_of(next(h for h in hits if ident(h) == ident(by_wedge))) == "fav"
        assert am.keshi_rule_of(next(h for h in hits if ident(h) == ident(not_keshi))) is None
        # (2) 横断検索のプリセット（SQL）
        for preset in ("abs", "keshi"):               # 画面と同じ SQL（上位500行で切らずに、全部の行で）
            cond = " OR ".join(f"(odds_mode = '{m}' AND {C.sql_condition(preset, m, indicator_col='indicator')})"
                               for m in am.ODDS_MODES)
            inside = {tuple(r) for r in con.execute(f'SELECT "テーブル名", "結合データ" FROM unified_all WHERE {cond}')}
            assert {k for k, cat in want.items() if cat == preset} <= inside
            assert not ({k for k, cat in want.items() if cat != preset} & inside)
            rows = am._rank_query(con, [], [], "all", "", ("", "", "", ""), preset,
                                  {"min_cnt": None, "min_wedge": None, "min_adj": None}, "件数", "DESC")
            assert {(r["テーブル名"], r["結合データ"]) for r in rows} <= inside
        # 画面の2つの呼び出し口（まとめて = _rank_query・集計表ごと）が、その行のオッズ帯の規則で判定している
        # （2〜10倍未満専用の消しを「通常」の規則で見ると、WEDGE で消しになる行が出ない）
        none = {"min_cnt": None, "min_wedge": None, "min_adj": None}
        def ranked(h, preset, mode):                  # その行の family・指数に絞る（上位500行で切られないように）
            rows = am._rank_query(con, [h["family"]], [h["indicator"]], mode, "", ("", "", "", ""), preset, none,
                                  "件数", "DESC")
            assert len(rows) < 500
            return ident(h) in {(r["テーブル名"], r["結合データ"]) for r in rows}
        assert ranked(by_wedge, "keshi", "odds10u") and ranked(by_wedge, "keshi", "all")
        assert not ranked(not_keshi, "keshi", "odds10u") and not ranked(not_keshi, "keshi", "all")
        assert ranked(two, "abs", "normal") and ranked(two, "abs", "all")
        assert not ranked(by_wedge, "keshi", "normal") and not ranked(two, "abs", "odds10u")
    for view in ("tables", "rank"):
        q = {"submitted": "1", "view": view, "preset": "keshi", "odds_mode": "odds10u"}
        page = c.get("/", query_string={**q, "family": by_wedge["family"], "ind": by_wedge["indicator"]}).get_data(as_text=True)
        assert by_wedge["key"] in page, view
        page = c.get("/", query_string={**q, "family": not_keshi["family"], "ind": not_keshi["indicator"]}).get_data(as_text=True)
        assert not_keshi["key"] not in page, view
        page = c.get("/", query_string={"submitted": "1", "view": view, "preset": "abs", "odds_mode": "normal",
                                        "family": two["family"], "ind": two["indicator"]}).get_data(as_text=True)
        assert two["key"] in page, view
    # (3) WEDGE 一覧の CSV の「区分」列
    r = c.get("/entries/wedge.csv", query_string={"table": t, "rid": rid, "odds_mode": "all"})
    rows = list(_csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
    labels = {None: "", "abs": "絶対軸", "keshi": "消し候補"}
    seen = {(x["テーブル名"], x["key"]): x["区分"] for x in rows if (x["テーブル名"], x["key"]) in want}
    assert seen == {k: labels[cat] for k, cat in want.items()}
    # (4) 逆引きのページの区分（DB の成績表の行）
    for h, cat, _v in plans:
        q = {"key": h["key"], "family": h["family"], "indicator": h["indicator"], "odds_mode": h["odds_mode"],
             "rank_scheme": h["rank_scheme"] or "", "region": h["region"] or ""}
        page = c.get("/entries/drill", query_string=q).get_data(as_text=True)
        assert "成績表（DB）: 件数 " in page
        assert (f"区分 <b>{labels[cat]}</b>" in page) if cat else ("区分 <b>" not in page), (cat, h["key"])
    # (5) 条件ヒット馬の4区分（絶対軸・消し候補の表に入り、区分なしの行はどの表にも入らない）
    with am.connect() as con:
        hits, _path = am.collect_hits(con, t, am._race_rows(con, t, rid))
    b = am.classify_hits(hits, {})
    where = {k: [cat for cat, rows in b.items() if any(ident(h) == k for h in rows)] for k in want}
    assert where == {ident(two): ["abs"], ident(by_wedge): ["keshi"], ident(not_keshi): []}
    assert c.get("/entries/hits", query_string={"table": t, "rid": rid}).status_code == 200
