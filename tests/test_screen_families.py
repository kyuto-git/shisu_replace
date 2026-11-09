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
            "adj_wedge_top", "adj_wedge_bottom", "adj_wedge_spread",
            "monotonic", "select_A", "select_B", "selected", "reason"}
    need |= {f"{c}_{m}" for c in ("abs", "axis", "opp", "keshi") for m in (*ODDS_MODES, "total")}
    assert need <= set(rep.columns)
    assert len(rep) == 129                                     # 初期セット 83 family・129 通り
    assert set(rep["masters"]) == {1}
    assert (rep["groups"] >= rep["groups_ge10"]).all() and rep["coverage"].between(0, 1).all()
    assert (rep["groups"] > 0).sum() > 60
    ok = rep["wedge_top"].notna() & rep["wedge_bottom"].notna()
    assert ok.sum() > 10
    assert ((rep.loc[ok, "wedge_top"] - rep.loc[ok, "wedge_bottom"]).round(4) == rep.loc[ok, "wedge_spread"]).all()
    ok2 = rep["adj_wedge_top"].notna() & rep["adj_wedge_bottom"].notna()
    assert ok2.sum() > 10
    assert ((rep.loc[ok2, "adj_wedge_top"] - rep.loc[ok2, "adj_wedge_bottom"]).round(4)
            == rep.loc[ok2, "adj_wedge_spread"]).all()
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
    # 基準A は wedge_spread 上位5 と adj_wedge_spread 上位5 の和（どちらかに入る行だけ）
    a = rep[rep["select_A"]]
    assert len(a) <= 10 and set(a["reason"]).issubset({"A:識別力", "A:識別力(adj)", "B:軸が出る・A:識別力",
                                                       "B:軸が出る・A:識別力(adj)", "B:絶対軸・A:識別力",
                                                       "B:絶対軸・A:識別力(adj)"})
    for f in fams:
        schemes = set(picked.loc[picked["name"] == f.name, "scheme"].fillna(""))
        if f.uses_schemes:
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
    assert len(S.POOL) == 28                                    # 重量種別・クラス名・枠番グループ・騎手 ＋ 22条件の24列
    kinds = {}
    for _parts, kind in S.templates("default"):
        kinds[kind] = kinds.get(kind, 0) + 1
    assert kinds == {"s0:single": 28, "s0:pair": 377, "s0:trio": 87, "s2:single": 26, "s3:single": 27,
                     "s4:single": 27, "s5:single": 25, "s6:single": 19, "s6n:single": 17}
    n_all = len(S.templates("all"))
    assert 2000 <= n_all <= 3000                                 # 約2,000〜3,000 のテンプレート
    allk = {}
    for _parts, kind in S.templates("all"):
        allk[kind] = allk.get(kind, 0) + 1
    assert allk["s6:pair"] == 171 and allk["s2:pair"] == 325 and allk["s1:trio"] == 87
    assert any(f.parts == (F.COURSE, "前走着順") for f in cands)          # 初期セット D_prevfin
    assert any(f.parts == ("場所", "芝・ダ", "騎手") for f in cands)
    assert any(f.parts == (F.COURSE, "重量種別", "クラス名", "前走脚質") for f in cands)   # normal × 1条件
    # 部品の集合が同じなら1つ（初期セットの C_sd_corner_frame と 芝・ダ×枠番グループ×初角帯）
    sets = [(frozenset(f.parts), f.tail) for f in S.make_candidates("all")]
    assert len(sets) == len(set(sets))
    assert all(not S.redundant(f.parts) for f in S.make_candidates("all") if f.layer.startswith("X:"))


def test_redundant_rules():
    import screen_families as S
    for attr in S.COURSE_ATTRS:                                  # COURSE で一意に決まる属性
        assert S.redundant((F.COURSE, attr)) and S.redundant(("場所", "芝・ダ", "距離", attr))
    assert S.redundant(("場所", "芝・ダ", "左右")) and S.redundant(("場所", "坂", "騎手"))
    assert S.redundant(("芝・ダ", "距離", "距離帯")) and S.redundant(("左右", "左右遷移"))
    assert S.redundant((F.COURSE, "場所")) and S.redundant((F.COURSE, "芝・ダ", "騎手"))
    assert not S.redundant(("場所", "芝・ダ", "距離帯", "騎手")) and not S.redundant((F.COURSE, "左右遷移"))
    assert not S.redundant(("芝・ダ", "距離帯", "直線帯")) and not S.redundant(("場所", "芝・ダ", "R値帯"))


def test_expanded_candidates_keep_history_entry_parity():
    """広げた候補（場所・距離・芝(内・外) を部品に持つ骨格を含む）でも、過去側のキーは出馬表側の候補に必ず入る。"""
    import random
    import screen_families as S
    from shisu.conditions import BANDED_CONDITIONS, CONDITION_COLUMNS
    rng = random.Random(4)
    rows = []
    from shisu.course import inner_values
    for _ in range(60):
        place, sd, raw = rng.choice(["東京", "京都", "阪神"]), rng.choice(["芝", "ダ"]), rng.choice(["1600", "2000"])
        # 芝(内・外) は実際にありうる値だけ（ダート・内外の区別の無い芝は空。京都の芝は出馬表でも分かる）
        inner = "" if sd != "芝" else (rng.choice(["内", "外"]) if place == "京都" else (inner_values(place, sd, raw) or [""])[0])
        r = {"場所": place, "芝・ダ": sd, "距離": rng.choice(["1600", "16-18"]), "距離_raw": raw,
             "芝(内・外)": inner, "重量種別": "定量", "クラス名": rng.choice(["1・2_勝", "G1"]),
             "枠番グループ": rng.choice(["内枠", "外枠"]), "騎手": rng.choice(["A", "B"]),
             "順位": str(rng.randint(1, 16)), "偏差値帯": rng.choice(["71以上", "51~60"])}
        for c in CONDITION_COLUMNS:
            r[c] = str(rng.randint(1, 12)) if c in BANDED_CONDITIONS else rng.choice([f"{c}1", f"{c}2", ""])
        rows.append(r)
    df = pd.DataFrame(rows)
    recs = df.to_dict("records")
    fams = [f for f in S.make_candidates("all") if f.layer.startswith("X:s2") or f.layer.startswith("X:s3")
            or f.layer.startswith("X:s5") or f.layer.startswith("X:s6")][::7]
    fams.append(F.Family("X_sd_io_dist", ("芝・ダ", "芝(内・外)", "距離"), "HBAND"))
    n = 0
    for fam in fams:
        for sc in fam.schemes:
            keys = F.build_keys(df, fam, sc)
            for i, k in zip(keys.index, keys):
                if recs[i]["場所"] != "京都":                                             # 出馬表で内外が分からない
                    assert k in F.entry_keys({**recs[i], "芝(内・外)": ""}, fam, sc), (fam.name, k)
                assert k in F.entry_keys(recs[i], fam, sc), (fam.name, k)
                n += 1
    assert n > 1000


def test_select_requires_fuku_spread():
    import screen_families as S
    rep = pd.DataFrame([
        {"name": "a", "groups_ge10": 50, "coverage": 0.5, "wedge_spread": 0.5, "fuku_spread": 25.0, "abs_total": 0, "axis_total": 0},
        {"name": "b", "groups_ge10": 50, "coverage": 0.5, "wedge_spread": 0.9, "fuku_spread": 19.9, "abs_total": 0, "axis_total": 0},
        {"name": "c", "groups_ge10": 50, "coverage": 0.5, "wedge_spread": 0.2, "fuku_spread": None, "abs_total": 0, "axis_total": 2},
    ])
    r = S.select(rep, min_axis=1, min_groups=10, min_coverage=0.2, min_spread=0.10, top_a=30)
    assert r["select_A"].tolist() == [True, False, False] and r["selected"].tolist() == [True, False, True]
    r = S.select(rep, 1, 10, 0.2, 0.10, 30, min_fuku_spread=10.0)
    assert r["select_A"].tolist() == [True, True, False]


def test_ref_columns_follow_course_parts():
    import aggregate_stats_ver2 as a
    assert a.ref_columns_for(F.Family("X_plc_sd_jk", ("場所", "芝・ダ", "騎手"), "RBAND")) == ("場所", "芝・ダ")
    assert a.ref_columns_for(F.Family("X_sd_dist_jk", ("芝・ダ", "距離", "騎手"), "RBAND")) == ("芝・ダ", "距離")
    assert a.ref_columns_for(F.get("normal")) == ("場所", "芝・ダ", "距離", "芝(内・外)")
    assert a.ref_columns_for(F.get("B_jk_prevfin")) == ()


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


def test_standalone_inner_part_matches_past_side_only():
    """単独の 芝(内・外): 過去側は値の空のレース（ダート・内外の区別の無い芝）でキーを作らないので、出馬表側も作らない。
    出馬表で内外が分からない芝は、コース属性マスタでその場所・距離にある内外だけを試す。"""
    fam = F.Family("X_io_t", ("芝(内・外)",), "HBAND")
    F.validate(fam)
    base = {"偏差値帯": "51~60", "順位": "2"}
    k = lambda **f: F.entry_keys({**base, **f}, fam)
    assert k(**{"芝・ダ": "ダ", "場所": "京都", "距離_raw": "1800", "芝(内・外)": ""}) == []
    assert k(**{"芝・ダ": "芝", "場所": "東京", "距離_raw": "1600", "芝(内・外)": ""}) == []
    assert k(**{"芝・ダ": "芝", "場所": "阪神", "距離_raw": "1600", "芝(内・外)": ""}) == ["外×51~60"]
    assert k(**{"芝・ダ": "芝", "場所": "中山", "距離_raw": "2000", "芝(内・外)": ""}) == ["内×51~60"]
    assert k(**{"芝・ダ": "芝", "場所": "京都", "距離_raw": "1600", "芝(内・外)": "外"}) == ["外×51~60"]
    # 過去側（build_keys）: 空の行はキーを作らない
    hist = pd.DataFrame({"芝(内・外)": ["", "外", ""], "芝・ダ": ["ダ", "芝", "芝"], "偏差値帯": ["51~60"] * 3,
                         "順位": [2, 2, 2]})
    keys = F.build_keys(hist, fam)
    assert keys.tolist() == ["外×51~60"] and keys.index.tolist() == [1]


def test_select_new_rules_abs_always_and_adj_top():
    """基準B: 絶対軸＋軸候補 ≥ min_axis、または絶対軸が1つでも出る（always_abs）。
    基準A: 下限を満たすもののうち wedge_spread 上位 top_a か adj_wedge_spread 上位 top_a。"""
    import screen_families as S
    base = {"groups_ge10": 50, "coverage": 0.5, "fuku_spread": 30.0}
    rep = pd.DataFrame([
        {"name": "abs1", **base, "wedge_spread": 0.0, "adj_wedge_spread": 0.0, "abs_total": 1, "axis_total": 0},
        {"name": "axis30", **base, "wedge_spread": 0.0, "adj_wedge_spread": 0.0, "abs_total": 0, "axis_total": 30},
        {"name": "axis10", **base, "wedge_spread": 0.0, "adj_wedge_spread": 0.0, "abs_total": 0, "axis_total": 10},
        {"name": "w_top", **base, "wedge_spread": 0.9, "adj_wedge_spread": 0.11, "abs_total": 0, "axis_total": 0},
        {"name": "adj_top", **base, "wedge_spread": 0.12, "adj_wedge_spread": 0.8, "abs_total": 0, "axis_total": 0},
        {"name": "neither", **base, "wedge_spread": 0.11, "adj_wedge_spread": 0.1, "abs_total": 0, "axis_total": 0},
        {"name": "no_adj", **base, "wedge_spread": 0.5, "adj_wedge_spread": None, "abs_total": 0, "axis_total": 0},
    ])
    r = S.select(rep, top_a=1).set_index("name")                # 既定: min_axis=25・always_abs
    assert r["select_B"].to_dict() == {"abs1": True, "axis30": True, "axis10": False, "w_top": False,
                                      "adj_top": False, "neither": False, "no_adj": False}
    assert r.loc[["w_top", "adj_top"], "select_A"].all() and not r.loc[["neither", "no_adj"], "select_A"].any()
    assert r.loc["abs1", "reason"] == "B:絶対軸" and r.loc["adj_top", "reason"] == "A:識別力(adj)"
    r = S.select(rep, top_a=1, always_abs=False).set_index("name")
    assert not r.loc["abs1", "select_B"]
    r = S.select(rep, top_a=2).set_index("name")                # wedge 上位2 = w_top・no_adj
    assert r.loc["no_adj", "select_A"] and not r.loc["neither", "select_A"]


def test_from_report_reselects_without_scoring(screened, base, tmp_path):
    """--from-report: master を読まずに、採点済みのレポートから選抜だけやり直して JSON を書く。"""
    import screen_families as S
    report = base / "screening_report.csv"
    out_rep, out_sel = tmp_path / "rep2.csv", tmp_path / "sel2.json"
    assert S.main(["--from-report", str(report), "--report", str(out_rep), "--export-selected", str(out_sel),
                   "--master-dir", str(tmp_path / "no_masters"), "--min-axis", "1", "--min-groups", "3",
                   "--min-coverage", "0", "--min-spread", "-9", "--top-a", "5"]) == 0
    first = pd.read_csv(report, encoding="cp932")
    again = pd.read_csv(out_rep, encoding="cp932")
    key = ["name", "scheme"]
    a = first.set_index(key).sort_index()
    b = again.set_index(key).sort_index()
    assert a["selected"].tolist() == b["selected"].tolist() and a["reason"].fillna("").tolist() == b["reason"].fillna("").tolist()
    assert json.loads(out_sel.read_text(encoding="utf-8"))["families"] == \
        json.loads((base / "sel_initial.json").read_text(encoding="utf-8"))["families"]
    # 基準を厳しくすると減る。基準は JSON に残る
    assert S.main(["--from-report", str(report), "--report", str(out_rep), "--export-selected", str(out_sel),
                   "--min-axis", "1000", "--no-always-select-abs", "--min-groups", "3", "--min-coverage", "0",
                   "--min-spread", "-9", "--top-a", "1"]) == 0
    tight = pd.read_csv(out_rep, encoding="cp932")
    assert tight["selected"].sum() < first["selected"].sum() and tight["select_A"].sum() <= 2
    crit = json.loads(out_sel.read_text(encoding="utf-8"))["criteria"]
    assert crit["min_axis"] == 1000 and crit["always_select_abs"] is False and crit["top_a"] == 1


def test_from_report_errors(tmp_path):
    import screen_families as S
    assert S.main(["--from-report", str(tmp_path / "none.csv")]) == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="cp932")
    assert S.main(["--from-report", str(bad), "--report", str(tmp_path / "r.csv")]) == 2
