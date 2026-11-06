"""screen_families.py（候補の一括採点・選抜）と、selected_families.json で family を置き換える流れ。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

from shisu import families as F  # noqa: E402
from shisu.classify import classify  # noqa: E402
from shisu.odds import ODDS_MODES  # noqa: E402

YEAR_ARGS = ["--wedge-min-year-count", "2"]
SAV = "主５場（中山、東京、中京、京都、阪神）_SAV"


@pytest.fixture(scope="module")
def mp():
    m = pytest.MonkeyPatch()
    yield m
    m.undo()


@pytest.fixture(scope="module")
def base(tmp_path_factory, mp):
    """合成の過去データ（3指数）→ master。出馬表も置く。"""
    b = tmp_path_factory.mktemp("screen")
    mp.setenv("SHISU_BASE_DIR", str(b))
    mp.delenv("DB_PATH", raising=False)
    mp.delenv("SHISU_FAMILIES_FILE", raising=False)
    synth.make_history(b)
    synth.make_entries(b)
    import replace_csv_ver3
    assert replace_csv_ver3.main(["--clean"]) == 0
    return b


@pytest.fixture(scope="module")
def screened(base):
    import screen_families
    report = base / "screening_report.csv"
    sel = base / "sel_initial.json"
    assert screen_families.main(["--space", "initial", "--indicators", "SAV", "--report", str(report),
                                 "--export-selected", str(sel), "--min-axis", "1", "--min-groups", "3",
                                 "--min-coverage", "0", "--min-spread", "-9", "--top-a", "5", *YEAR_ARGS]) == 0
    return pd.read_csv(report, encoding="cp932"), sel


def test_report_columns_and_rows(screened):
    rep, _ = screened
    need = {"name", "scheme", "layer", "tail", "parts", "label", "masters", "groups", "groups_ge10", "coverage",
            "adj_groups", "wedge_top", "wedge_bottom", "wedge_spread", "fuku_top", "fuku_bottom", "fuku_spread",
            "monotonic", "select_A", "select_B", "selected", "reason"}
    need |= {f"{c}_{m}" for c in ("abs", "axis", "opp", "keshi") for m in (*ODDS_MODES, "total")}
    assert need <= set(rep.columns)
    assert len(rep) == 122                                     # 初期セット 83 family・122 通り
    assert set(rep["masters"]) == {1}
    assert (rep["groups"] >= rep["groups_ge10"]).all() and rep["coverage"].between(0, 1).all()
    assert (rep["groups"] > 0).sum() > 60
    ok = rep["wedge_top"].notna() & rep["wedge_bottom"].notna()
    assert ok.sum() > 10
    assert ((rep.loc[ok, "wedge_top"] - rep.loc[ok, "wedge_bottom"]).round(4) == rep.loc[ok, "wedge_spread"]).all()
    for c in ("abs", "axis", "opp", "keshi"):
        assert (rep[f"{c}_total"] == sum(rep[f"{c}_{m}"] for m in ODDS_MODES)).all()


def test_classification_counts_equal_production(base, screened, tmp_path):
    """基準B の該当キー数は、本番（aggregate → build_db → classify）で同じ family を集計・判定した数と一致する。"""
    import aggregate_stats_ver2
    import build_db
    rep, _ = screened
    F.reset()
    agg = tmp_path / "agg"
    only_sav = tmp_path / "m"
    only_sav.mkdir()
    (only_sav / f"master_{SAV}.csv.gz").write_bytes((base / "master_csv" / f"master_{SAV}.csv.gz").read_bytes())
    assert aggregate_stats_ver2.main(["--in-dir", str(only_sav), "--out-dir", str(agg), "--clean", *YEAR_ARGS]) == 0
    db = tmp_path / "p.db"
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(agg), "--db-path", str(db), "--fresh"]) == 0
    with sqlite3.connect(db) as con:
        rows = con.execute('SELECT family, COALESCE(rank_scheme, \'\'), odds_mode, "件数", "複勝率_val", "連対率_val", '
                           '"掲示板率_val", "複勝回収値", "単勝適正回収値" FROM unified_all').fetchall()
    prod: dict[tuple, int] = {}
    for fam, sc, mode, cnt, fv, rv, top5, frc, tra in rows:
        c = classify({"cnt": cnt, "fv": fv, "rv": rv, "top5": top5, "frc": frc, "tra": tra}, mode)
        if c:
            prod[(fam, sc, mode, c)] = prod.get((fam, sc, mode, c), 0) + 1
    rep = rep.assign(scheme=rep["scheme"].fillna(""))
    got = {}
    for r in rep.to_dict("records"):
        for mode in ODDS_MODES:
            for c in ("abs", "axis", "opp", "keshi"):
                if r[f"{c}_{mode}"]:
                    got[(r["name"], r["scheme"], mode, c)] = int(r[f"{c}_{mode}"])
    assert got == prod
    assert sum(prod.values()) > 0
    # 件数10以上のグループ数も本番の成績表と一致
    with sqlite3.connect(db) as con:
        ge10 = dict(((f, s), n) for f, s, n in con.execute(
            'SELECT family, COALESCE(rank_scheme, \'\'), SUM("件数" >= 10) FROM unified_all WHERE odds_mode=\'normal\' '
            'GROUP BY 1, 2'))
    for r in rep.to_dict("records"):
        assert r["groups_ge10"] == ge10.get((r["name"], r["scheme"]), 0), r["name"]


def test_export_selected(screened):
    rep, sel = screened
    data = json.loads(sel.read_text(encoding="utf-8"))
    assert data["source"] == "screen_families.py" and data["criteria"]["min_axis"] == 1
    fams = F.load_families(sel)
    picked = rep[rep["selected"]]
    assert {f.name for f in fams} == set(picked["name"])
    assert all(((r["abs_total"] + r["axis_total"]) >= 1) or r["select_A"] for r in picked.to_dict("records"))
    assert picked["select_A"].sum() <= 5
    for f in fams:
        schemes = set(picked.loc[picked["name"] == f.name, "scheme"].fillna(""))
        if f.tail == "RBAND":
            assert set(f.schemes) == schemes                     # 片方のスキームだけ選ばれたら schemes で絞る
        else:
            assert schemes == {""}


def test_candidates_space():
    import screen_families as S
    init = F.default_families()
    cands = S.make_candidates("default")
    names = [f.name for f in cands]
    assert len(names) == len(set(names))
    assert {f.name for f in init} <= set(names)                 # 初期セットは名前のまま入る
    pool = len(S.POOL)
    singles = sum(1 for f in cands if f.layer == "X:single")
    pairs = sum(1 for f in cands if f.layer == "X:pair")
    assert singles == 2 * pool and pairs <= 2 * pool * (pool - 1) // 2 and pairs > 600
    assert any(f.parts == (F.COURSE, "前走着順") for f in cands)
    assert any(f.parts == ("芝・ダ", "距離帯", "騎手") for f in cands)
    assert len(S.make_candidates("all")) > len(cands) * 2
    def n_conditions(f):          # コース（または 芝・ダ×距離帯）を除いた条件の数
        parts = list(f.parts)
        if parts[:1] == [F.COURSE]:
            parts = parts[1:]
        elif parts[:2] == ["芝・ダ", "距離帯"]:
            parts = parts[2:]
        return len(parts)
    for space in ("default", "all"):
        assert all(1 <= n_conditions(f) <= 2 for f in S.make_candidates(space) if f.layer.startswith("X:"))


def test_screen_rejects_missing_masters(tmp_path):
    import screen_families
    assert screen_families.main(["--master-dir", str(tmp_path), "--report", str(tmp_path / "r.csv")]) == 2


# ---------------------------------------------------------------- selected_families.json で置き換える
CUSTOM = [
    {"name": "normal", "parts": ["COURSE", "重量種別", "クラス名"], "tail": "RANK"},
    {"name": "X_jk_pfin_season", "parts": ["騎手", "前走着順", "時期"], "tail": "RBAND", "schemes": ["s2"]},
    {"name": "X_crs_mare_hen", "parts": ["COURSE", "牝馬限定"], "tail": "HBAND"},
    {"name": "X_sd_distb_pstyle", "parts": ["芝・ダ", "距離帯", "前走脚質"], "tail": "RBAND"},
]


def test_selected_families_replace_initial_set(base, monkeypatch):
    """selected_families.json があれば、aggregate・build_db・照合・画面はその family だけを使う（任意の1〜3条件）。"""
    import aggregate_stats_ver2
    import build_db
    import calc_entry_hensachi
    sel = base / "selected_families.json"
    sel.write_text(json.dumps({"families": CUSTOM}, ensure_ascii=False), encoding="utf-8")
    try:
        assert aggregate_stats_ver2.main(["--clean", *YEAR_ARGS]) == 0
        names = {f["name"] for f in CUSTOM}
        fj = json.loads((base / "agg_csv" / "families.json").read_text(encoding="utf-8"))
        assert {f["name"] for f in fj["families"]} == names
        files = [p.name for p in (base / "agg_csv").glob("foragg_*.csv")]
        assert files and all(any(f"_{n}_" in fn for n in names) for fn in files)
        assert not any("rankband_s1_X_jk_pfin_season" in fn for fn in files)      # schemes=["s2"] のとおり
        assert any("rankband_s2_X_jk_pfin_season" in fn for fn in files)
        assert any("rankband_s1_X_sd_distb_pstyle" in fn for fn in files)
        assert calc_entry_hensachi.main(["--merge-only", "--clean"]) == 0
        F.reset()                                         # build_db は agg_csv/families.json から読み直す
        assert build_db.main(["--fresh"]) == 0
        with sqlite3.connect(base / "racing.db") as con:
            fams_db = {r[0] for r in con.execute("SELECT name FROM __families__")}
            fams_u = {r[0] for r in con.execute("SELECT DISTINCT family FROM unified_all")}
            fams_x = {r[0] for r in con.execute("SELECT DISTINCT family FROM entries_xjoin_merged_hensachi")}
            pairs = set(con.execute("SELECT DISTINCT family, rank_scheme FROM entries_xjoin_merged_hensachi"))
        assert fams_db == names and fams_u <= names and len(fams_u) >= 3
        assert fams_x <= names and len(fams_x) >= 2
        assert ("X_jk_pfin_season", "s1") not in pairs
        # 画面: DB の __families__ を読んで、照合と表示に使う（初期セットに戻っていても）
        F.reset()
        monkeypatch.setenv("SHISU_BASE_DIR", str(base))
        import importlib
        import app as app_module
        am = importlib.reload(app_module)
        c = am.app.test_client()
        r = c.get("/?submitted=1&view=all")
        assert r.status_code == 200
        assert set(F.active_names()) == names
        with sqlite3.connect(base / "racing.db") as con:
            rid = con.execute('SELECT "レースID(新/馬番無)" FROM entries_merged_hensachi LIMIT 1').fetchone()[0]
        q = {"table": "entries_merged_hensachi", "rid": rid}
        r = c.get("/entries/hits", query_string=q)
        assert r.status_code == 200
        r = c.get("/entries/wedge.csv", query_string={**q, "odds_mode": "all"})
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert any(f["name"] in body for f in CUSTOM[1:])        # 置き換えた family のキーが一覧に出る
        assert "rankJ_band" not in body and "hensachiA" not in body
    finally:
        sel.unlink(missing_ok=True)
        F.reset()


def test_bad_selected_file_is_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"families": [{"name": "X_bad", "parts": ["騎手", "存在しない条件"], "tail": "RBAND"}]},
                            ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        F.load_families(p)
    p.write_text(json.dumps({"families": [{"name": "9bad", "parts": ["騎手"], "tail": "RBAND"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        F.load_families(p)
    p.write_text(json.dumps({"families": [{"name": "X_a", "parts": ["騎手"], "tail": "XX"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        F.load_families(p)
    p.write_text(json.dumps({"families": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        F.load_families(p)


def test_families_json_roundtrip(tmp_path):
    fams = F.default_families()[:5] + [F.Family.from_dict(d) for d in CUSTOM[1:]]
    p = tmp_path / "f.json"
    F.save_families(p, fams, {"source": "test"})
    back = F.load_families(p)
    assert [(f.name, f.parts, f.tail, f.only) for f in back] == [(f.name, f.parts, f.tail, f.only) for f in fams]
    con = sqlite3.connect(":memory:")
    F.save_to_db(con, back)
    assert [(f.name, f.parts, f.tail, f.only) for f in F.load_from_db(con)] == [(f.name, f.parts, f.tail, f.only)
                                                                                for f in fams]
