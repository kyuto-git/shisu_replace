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
    agg = next((b / "agg_csv").glob("foragg_normal_*SAV.csv"))
    shutil.copy(agg, b / "agg_csv" / ("foragg_odds5p_" + agg.name[len("foragg_"):]))
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
    from shisu.naming import parse_foragg_stem
    # 素材（foragg_csv）はもう作らない。master が集計の入力
    assert [p.name for p in (base / "foragg_csv").iterdir()] == ["foragg_normal_主５場（中山、東京、中京、京都、阪神）_SAV.csv.gz"]
    masters = sorted(p.name for p in (base / "master_csv").glob("master_*.csv.gz"))
    assert len(masters) == 3
    agg = [p for p in (base / "agg_csv").glob("foragg_*.csv") if "odds5p_normal" not in p.name or
           parse_foragg_stem(p.stem) is not None]
    variants = sum(len(f.schemes) for f in fam_registry.active())
    assert len(fam_registry.active()) == 83 and variants == 129
    assert 3 * variants <= len(agg) <= 3 * variants * 4                   # 0件の成績表は書かない
    normal = {parse_foragg_stem(p.stem)["family"] for p in agg if parse_foragg_stem(p.stem)["mode"] == "normal"}
    assert {f.name for f in fam_registry.active() if f.layer == "A"} <= normal
    assert not list((base / "agg_csv").glob("foragg_odds10u_odds10u_*"))
    assert (base / "agg_csv" / "families.json").exists()
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
    outs = sorted(p for p in (base / "agg_csv").glob("foragg_*normal_主*SAV.csv") if "odds5p_normal" not in p.name
                  or p.name.startswith("foragg_odds50p_"))
    assert len(outs) >= 2                              # 通常 + オッズ帯（0件の帯は書かない）
    first = [o.read_bytes() for o in outs]
    assert aggregate_stats_ver2.main(AGG_TEST_ARGS) == 0
    assert src.read_bytes() == before and [o.read_bytes() for o in outs] == first


def test_odds_split_counts(base):
    """オッズ帯3区分の件数の合計は、オッズ欠損が無ければ通常と一致する（0件の帯はファイルが無い）。"""
    def total(mode):
        prefix = "" if mode == "normal" else mode + "_"
        f = base / "agg_csv" / f"foragg_{prefix}normal_主５場（中山、東京、中京、京都、阪神）_SAV.csv"
        return pd.read_csv(f, encoding="cp932")["件数"].sum() if f.exists() else 0
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
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg%'")]
        assert names and not any(n.startswith("foragg_foragg_") for n in names)
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
    with sqlite3.connect(p) as con:
        before = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg_%'")}
        con.execute('CREATE TABLE "entries_old_stale" (x TEXT)')
        con.execute('CREATE TABLE "entries_xjoin_old_stale" (x TEXT)')
    assert build_db.main(["--mode", "entries", "--prune", "--db-path", str(p)]) == 0
    with sqlite3.connect(p) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert before and before <= names                       # foragg_* は消えない
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
    agg = pd.read_csv(base / "agg_csv" / ("foragg_normal_" + src.name[len("master_"):].replace(".csv.gz", ".csv")),
                      encoding="cp932")
    assert list(agg.columns[18:25]) == list(WEDGE_COLS)
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
    """横断ランキングのプリセットは、オッズ帯ごとの判定（消し候補の10倍未満ルール）まで classify と一致する。"""
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
                    '"複勝回収値", "単勝適正回収値", odds_mode FROM unified_all')]
                want = {(r["テーブル名"], r["結合データ"]) for r in every if classify(
                    {"cnt": r["件数"], "fv": r["複勝率_val"], "rv": r["連対率_val"], "top5": r["掲示板率_val"],
                     "frc": r["複勝回収値"], "tra": r["単勝適正回収値"]}, r["odds_mode"]) == preset}
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


def test_build_db_rejects_agg_csv_without_wedge(base, tmp_path, capsys):
    """WEDGE 列の無い旧形式の成績表は取り込まない（WEDGE が全部空の DB を「正常」として作らない）。"""
    import build_db
    agg = tmp_path / "agg"
    shutil.copytree(base / "agg_csv", agg)
    old = next(agg.glob("foragg_normal_*SAV.csv"))
    df = pd.read_csv(old, encoding="cp932", dtype=str, keep_default_na=False).drop(columns=list(WEDGE_COLS))
    synth.write_cp932(df, old)
    p = tmp_path / "x.db"
    assert build_db.main(["--mode", "foragg", "--fresh", "--agg-dir", str(agg), "--db-path", str(p)]) == 1
    assert "旧形式の成績表" in capsys.readouterr().err
    with sqlite3.connect(p) as con:
        assert con.execute("SELECT COUNT(*) FROM __tables_meta__ WHERE source_file = ?", (old.name,)).fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM unified_all WHERE "WEDGE" IS NULL').fetchone()[0] == 0
    for f in agg.glob("*.csv"):
        d = pd.read_csv(f, encoding="cp932", dtype=str, keep_default_na=False)
        synth.write_cp932(d.drop(columns=[c for c in WEDGE_COLS if c in d.columns]), f)
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
    with sqlite3.connect(p) as con:
        before = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg_%'")}
    assert build_db.main(["--entries-only", "--db-path", str(p)]) == 0
    with sqlite3.connect(p) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert before and before <= names and any(n.startswith("entries_xjoin_") for n in names)


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
