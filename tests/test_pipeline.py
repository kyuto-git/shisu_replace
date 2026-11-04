"""csv_files → replace → aggregate → calc → build_db → app の通しテスト（合成データ）。"""
import shutil
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def base(tmp_path_factory, monkeypatch_module):
    b = tmp_path_factory.mktemp("shisu")
    monkeypatch_module.setenv("SHISU_BASE_DIR", str(b))
    monkeypatch_module.delenv("DB_PATH", raising=False)
    synth.make_history(b)
    synth.make_entries(b)
    # 旧仕様のファイル（odds5p）が foragg_csv に残っていても取り込まれないこと
    import aggregate_stats_ver2, build_db, calc_entry_hensachi, replace_csv_ver3
    assert replace_csv_ver3.main(["--clean"]) == 0
    legacy = b / "foragg_csv" / "foragg_odds5p_normal_主５場（中山、東京、中京、京都、阪神）_カツラキ.csv"
    shutil.copy(next((b / "foragg_csv").glob("foragg_normal_*カツラキ.csv")), legacy)
    assert aggregate_stats_ver2.main(["--clean"]) == 0
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
    assert len(list((base / "foragg_csv").glob("foragg_*.csv"))) == 3 * 80 + 1   # +1 は旧仕様のダミー
    assert not list((base / "agg_csv").glob("foragg_odds5p_*"))                  # 旧仕様は集計しない
    m = pd.read_csv(next((base / "master_csv").glob("master_*ZI.csv")), encoding="cp932", dtype=str,
                    keep_default_na=False)
    assert {"year", "日付", "指数有効", "オッズ帯", "順位帯_s1", "順位帯_s2"} <= set(m.columns)
    assert set(m["year"]) == {"2022", "2023", "2024", "2025"}


def test_foragg_material_columns(base):
    f = next((base / "foragg_csv").glob("foragg_rankband_s1_normal_band_*カツラキ.csv"))
    df = pd.read_csv(f, encoding="cp932", dtype=str, keep_default_na=False)
    assert {"馬番", "日付", "year", "指数順位帯"} <= set(df.columns)
    assert set(df["指数順位帯"]) <= {"1~3位", "4~6位", "7~9位", "10位~"}


def test_aggregate_does_not_overwrite_material_and_is_rerunnable(base):
    import aggregate_stats_ver2
    src = next((base / "foragg_csv").glob("foragg_normal_*カツラキ.csv"))
    before = src.read_bytes()
    out = base / "agg_csv" / src.name
    first = out.read_bytes()
    assert aggregate_stats_ver2.main([]) == 0
    assert src.read_bytes() == before and out.read_bytes() == first


def test_no_broken_keys(base):
    with db(base) as con:
        n = con.execute("SELECT COUNT(*) FROM unified_all WHERE \"結合データ\" LIKE '%nan%' "
                        "OR \"結合データ\" = '' OR \"結合データ\" IS NULL").fetchone()[0]
        assert n == 0
        # 全頭0・欠損2頭のレースは使わない（各指数 4年×2レース×16頭 = 128頭が除外される）
        total = con.execute("SELECT SUM(\"件数\") FROM unified_all WHERE family='normal' AND odds_mode='normal' "
                            "AND indicator='カツラキ'").fetchone()[0]
        assert total <= 640


def test_table_names_and_meta(base):
    with db(base) as con:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg%'")]
        assert names and not any(n.startswith("foragg_foragg_") for n in names)
        modes = {r[0] for r in con.execute("SELECT DISTINCT odds_mode FROM __tables_meta__ WHERE kind='foragg'")}
        assert modes == {"normal", "odds10u", "odds10_50", "odds50p"}
        inds = {r[0] for r in con.execute("SELECT DISTINCT indicator FROM unified_all")}
        assert inds == {"カツラキ", "ZI", "先行"}


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
    assert (df["先行_偏差値帯"] != "").all() and (df["カツラキ_偏差値帯"] != "").all()
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
        assert c.get("/", query_string={"submitted": "1", "preset": preset, "ind": "カツラキ"}).status_code == 200
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
