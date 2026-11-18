"""指数を使わない family（tail="NONE"。条件のみ）と、第2弾の3機能を全段に通した確認
（外部レース印の前走条件・近5走の要約条件・条件のみの集計と照合）。"""
import json
import random
import sqlite3

import numpy as np
import pandas as pd
import pytest

from shisu import classify as C
from shisu import families as F
from shisu import matching, odds
from shisu.indicators import CONDITION_ONLY, condition_only_source, parse_source_name, split_source_name
from shisu.naming import base_masters
from tests import synth2
from tests.test_recent_form import oracle as recent_oracle

RID = "レースID(新)"
AGG_ARGS = ["--wedge-min-year-count", "2", "--no-compact"]
FAMILIES = [
    {"name": "normal", "parts": ["COURSE", "重量種別", "クラス名"], "tail": "RANK", "layer": "A"},
    {"name": "X_jk_r5cond", "parts": ["騎手", "近5走同条件"], "tail": "RBAND"},
    {"name": "X_pmem_hen", "parts": ["前走メンバーLv"], "tail": "HBAND"},
    {"name": "N_sd_dist", "parts": ["芝・ダ", "距離"], "tail": "NONE"},                 # 末尾が数字（距離）のキー
    {"name": "N_crs", "parts": ["COURSE"], "tail": "NONE"},                             # 末尾が数字（コースの距離）のキー
    {"name": "N_jk_r5cond", "parts": ["騎手", "近5走同条件"], "tail": "NONE"},
    {"name": "N_marks", "parts": ["前走メンバーLv", "前走タイムLv"], "tail": "NONE"},
    {"name": "N_cush_moist", "parts": ["前走クッション帯", "前走含水率帯"], "tail": "NONE"},
    {"name": "N_r5plc_pfin", "parts": ["近5走同場", "前走着順"], "tail": "NONE"},        # 帯にする条件 → s1・s2
]
NONE_NAMES = [d["name"] for d in FAMILIES if d["tail"] == "NONE"]


# ---------------------------------------------------------------------------
# 単体
# ---------------------------------------------------------------------------
def test_none_tail_family_definition_and_keys():
    assert F.TAILS == ("RANK", "RBAND", "HBAND", "NONE") and F.TAIL_LABELS["NONE"] == "条件のみ" == CONDITION_ONLY
    f = F.Family("N_sd_dist", ("芝・ダ", "距離"), "NONE")
    F.validate(f)
    assert f.no_index and not f.uses_schemes and f.schemes == ("",) and not f.is_band
    assert f.display() == "芝・ダ×距離×条件のみ" and f.columns() == ("芝・ダ", "距離")
    g = F.Family("N_jk_pfin", ("騎手", "前走着順"), "NONE")
    assert g.uses_schemes and g.schemes == ("s1", "s2")               # 帯にする条件があるときだけ s1・s2
    assert F.Family.from_dict({**g.to_dict(), "schemes": ["s2"]}).schemes == ("s2",)
    with pytest.raises(ValueError):
        F.Family.from_dict({"name": "N_x", "parts": ["芝・ダ"], "tail": "NONE", "schemes": ["s1"]})   # スキームを持たない family
    with pytest.raises(ValueError):
        F.Family.from_dict({"name": "N_x", "parts": [], "tail": "NONE"})
    assert not F.Family("normal", ("COURSE",), "RANK").no_index
    # キー: 部品の値だけ（末尾に指数の部品を付けない）。部品が空の行は作らない。順位・偏差値帯は見ない
    df = pd.DataFrame({"芝・ダ": ["芝", "ダ", "", "芝"], "距離": ["1600", "16-18", "1200", ""], "順位": ["1", "", "9", "3"],
                       "偏差値帯": ["", "", "", ""], "騎手": ["A", "B", "C", "D"], "前走着順": ["2", "7", "", "1"]})
    assert F.build_keys(df, f).to_dict() == {0: "芝×1600", 1: "ダ×16-18"}
    assert F.entry_keys({"芝・ダ": "芝", "距離": "1600"}, f) == ["芝×1600"]
    assert F.entry_keys({"芝・ダ": "芝", "距離": "1600", "順位": "12", "偏差値帯": "40以下"}, f) == ["芝×1600"]
    assert F.entry_keys({"芝・ダ": "芝", "距離": ""}, f) == []
    k1, k2 = F.build_keys(df, g, "s1"), F.build_keys(df, g, "s2")
    assert k1.tolist() == ["A×前走2~3着", "B×前走6~9着", "D×前走1着"]
    assert sorted(k2[k2.index == 0].tolist()) == ["A×前走1~3着", "A×前走1~5着"]         # s2 は重なる
    with pytest.raises(ValueError):
        F.build_keys(df, g, "")
    for sc in ("s1", "s2"):                                          # 過去側と出馬表側で同じキー
        for i, row in df.iterrows():
            past = sorted(F.build_keys(df, g, sc)[lambda s: s.index == i].tolist())
            assert sorted(F.entry_keys(row.to_dict(), g, sc)) == past
    # コースを部品にした条件のみ: 末尾は距離（内外があれば内外）
    c = F.Family("N_crs", ("COURSE",), "NONE")
    crs = pd.DataFrame({"場所": ["東京", "京都"], "芝・ダ": ["芝", "芝"], "距離": ["1600", "14-16"], "芝(内・外)": ["", "内"]})
    assert F.build_keys(crs, c).tolist() == ["東京×芝×1600", "京都×芝×14-16×内"]
    assert "東京×芝×1600" in F.entry_keys({"場所": "東京", "芝・ダ": "芝", "距離": "1600", "芝(内・外)": ""}, c)
    # 台帳・DB への保存
    F.set_active([f, g, F.Family("normal", ("COURSE", "重量種別", "クラス名"), "RANK")])
    assert F.split_no_index(F.active_names()) == (["normal"], ["N_sd_dist", "N_jk_pfin"])
    assert F.is_no_index("N_sd_dist") and not F.is_no_index("normal") and not F.is_no_index("nope")
    assert F.family_indicator("N_sd_dist", "SAV") == CONDITION_ONLY and F.family_indicator("normal", "SAV") == "SAV"
    con = sqlite3.connect(":memory:")
    F.save_to_db(con, F.active())
    assert [(x.name, x.tail, x.parts) for x in F.load_from_db(con)] == [(x.name, x.tail, x.parts) for x in F.active()]
    cands = F.entry_key_candidates({"芝・ダ": "芝", "距離": "1600", "騎手": "A", "前走着順": "2"}, ["N_sd_dist", "N_jk_pfin"])
    assert cands["N_sd_dist"] == [("芝×1600", "")] and ("A×前走1~3着", "s2") in cands["N_jk_pfin"]
    F.reset()


def test_source_names_and_base_master_choice(tmp_path):
    assert parse_source_name("全場_条件のみ") == ("全場", CONDITION_ONLY)
    assert parse_source_name("主５場（中山、東京、中京、京都、阪神）_条件のみ") == ("主5場(中山、東京、中京、京都、阪神)", CONDITION_ONLY)
    assert split_source_name("全場_吉馬_SP能力値") == ("全場", "吉馬_SP能力値") and parse_source_name("全場_吉馬_SP能力値")[1] == "吉馬_SP能力値"
    assert condition_only_source("全場_タイムM") == "全場_条件のみ" == condition_only_source("全場_吉馬_SP能力値")
    assert condition_only_source("主５場（中山、東京、中京、京都、阪神）_対戦M") == "主５場（中山、東京、中京、京都、阪神）_条件のみ"
    d = tmp_path
    names = ["master_全場_SAV.csv.gz", "master_全場_対戦M.csv.gz", "master_全場_タイムM.csv.gz", "master_東京_ZI.csv.gz",
             "master_東京_対戦M.csv.gz", "master_京都_ZI.csv.gz", "master_京都_BC.csv.gz", "__inner_outer__.csv.gz"]
    got = base_masters([d / n for n in names])
    assert {r: p.name for r, p in got.items()} == {"全場": "master_全場_タイムM.csv.gz", "東京": "master_東京_対戦M.csv.gz",
                                                   "京都": "master_京都_BC.csv.gz"}       # タイムM → 対戦M → 名前順の先頭
    assert base_masters([]) == {}


def test_low_tail_gate_never_applies_to_condition_only_rows():
    """条件のみの family のキーは、末尾が 距離「1600」・下位の帯と同じ文字でも、指数の上限ゲートに掛からない。"""
    num = {"cnt": 40, "fv": 90, "rv": 85, "top5": 95, "frc": 120, "tra": 110}
    for key in ("芝×1600", "東京×芝×1600", "騎手×6位~", "騎手×40以下", "騎手×10位~", "x×7"):
        assert C.is_low_tail(key) and not C.is_low_tail(key, CONDITION_ONLY)
        assert C.classify({**num, "key": key}, "normal") is None                       # 指数のある行は今までどおり外す
        assert C.classify({**num, "key": key, "indicator": "SAV"}, "normal") is None
        assert C.classify({**num, "key": key, "indicator": CONDITION_ONLY}, "normal") == "abs"
        assert C.low_tail_axis_category({**num, "key": key}, "normal") == "abs"
        assert C.low_tail_axis_category({**num, "key": key, "indicator": CONDITION_ONLY}, "normal") is None   # 条件特化にも入れない
    opp = {"cnt": 40, "fv": 65, "rv": 10, "top5": 75, "frc": 50, "tra": 50, "key": "芝×1600"}
    assert C.classify(opp, "normal") is None and C.classify({**opp, "indicator": CONDITION_ONLY}, "normal") == "opp"
    keshi = {"cnt": 40, "fv": 2, "rv": 1, "top5": 5, "frc": 10, "tra": 10, "key": "芝×1600"}
    assert C.classify(keshi, "normal") == C.classify({**keshi, "indicator": CONDITION_ONLY}, "normal") == "keshi"
    # 1行版・ベクトル版・SQL 版（indicator の列を渡す）の一致
    rng = random.Random(11)
    keys = ["芝×1600", "東京×ダ×1200", "川田将雅×同条件連対あり", "A×6位~", "A×1位", "A×40以下", "G1×芝×2400", "芝×9", "芝×3", None]
    inds = [CONDITION_ONLY, "SAV", "タイムM", None, ""]
    pick = lambda vals: rng.choice(vals + [None])
    rows = [(i, pick([5, 6, 10, 15, 19, 20, 30, 60]), pick([0, 4.99, 19.99, 35, 50, 60, 70, 80, 95]), pick([0, 50, 60, 80, 90]),
             pick([0, 9.9, 50, 70, 80, 90, 99]), pick([0, 99, 100, 150, 200]), pick([0, 99, 100, 150, 200]),
             rng.choice(keys), rng.choice(inds)) for i in range(8000)]
    con = sqlite3.connect(":memory:")
    con.execute('CREATE TABLE t (id INTEGER, "件数" INTEGER, "複勝率_val" REAL, "連対率_val" REAL, "掲示板率_val" REAL, '
                '"複勝回収値" REAL, "単勝適正回収値" REAL, "結合データ" TEXT, indicator TEXT)')
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?,?,?)", rows)
    frame = pd.DataFrame(rows, columns=["id", "件数", "複勝率_val", "連対率_val", "掲示板率_val", "複勝回収値", "単勝適正回収値",
                                        "結合データ", "indicator"])
    differs = 0
    for mode in odds.ODDS_MODES:
        want = [C.classify({"cnt": r[1], "fv": r[2], "rv": r[3], "top5": r[4], "frc": r[5], "tra": r[6], "key": r[7],
                            "indicator": r[8]}, mode) for r in rows]
        assert list(C.classify_frame(frame, mode)) == want, mode
        special = [C.low_tail_axis_category({"cnt": r[1], "fv": r[2], "rv": r[3], "top5": r[4], "frc": r[5], "tra": r[6],
                                             "key": r[7], "indicator": r[8]}, mode) for r in rows]
        assert list(C.low_tail_axis_frame(frame, mode)) == special, mode
        assert not any(s for s, r in zip(special, rows) if r[8] == CONDITION_ONLY)
        for cat in C.CATEGORIES:
            got = {x[0] for x in con.execute(f"SELECT id FROM t WHERE {C.sql_condition(cat, mode, indicator_col='indicator')}")}
            assert got == {r[0] for r, w in zip(rows, want) if w == cat}, (mode, cat)
        plain = [C.classify({"cnt": r[1], "fv": r[2], "rv": r[3], "top5": r[4], "frc": r[5], "tra": r[6], "key": r[7]}, mode)
                 for r in rows]
        differs += sum(1 for a, b in zip(want, plain) if a != b)
    assert differs > 50                                               # 指数の列を見ないと結果が変わる行がある（空の一致でない）


def test_compaction_classifies_condition_only_tables_without_the_gate():
    from shisu.aggstore import compact_mask
    row = lambda key, cnt, fuku, ren, top5, frc: {"結合データ": key, "件数": cnt, "複勝率": f"0.00%（{fuku:.2f}%）",
                                                  "連対率": f"0.00%（{ren:.2f}%）", "掲示板率_val": top5, "複勝回収値": frc,
                                                  "単勝適正回収値": 0, "WEDGE": 0.3, "adjusted_WEDGE": np.nan}
    out = pd.DataFrame([row("芝×1600", 12, 90, 85, 95, 120),          # 絶対軸の数字・末尾は数字
                        row("芝×1200", 12, 30, 20, 40, 50)])           # どの区分でもない
    assert list(compact_mask(out, "normal", "X", indicator=CONDITION_ONLY)) == [True, False]
    # 指数のある成績表なら、同じ行は「条件特化」（指数が下位で数の基準は絶対軸）として残る。どちらでも残るが理由が違う
    assert list(compact_mask(out, "normal", "X", indicator="SAV")) == [True, False]
    opp = pd.DataFrame([row("芝×1600", 40, 65, 10, 75, 50)])           # 相手候補の数字: 指数のある表では落ち、条件のみでは残る
    assert list(compact_mask(opp, "normal", "X", 10 ** 9, 10 ** 9, indicator="SAV")) == [False]
    assert list(compact_mask(opp, "normal", "X", 10 ** 9, 10 ** 9, indicator=CONDITION_ONLY)) == [True]


def test_matching_looks_condition_only_families_up_once():
    F.set_active([F.Family("normal", ("COURSE", "重量種別", "クラス名"), "RANK"), F.Family("N_sd_dist", ("芝・ダ", "距離"), "NONE")])
    con = sqlite3.connect(":memory:")
    cols = matching.META_COLUMNS + matching.STAT_COLUMNS
    con.execute(f'CREATE TABLE unified_all ("結合データ" TEXT, {", ".join(chr(34) + c + chr(34) for c in cols)})')

    def add(key, fam, ind, mode="normal"):
        vals = {"テーブル名": f"t_{fam}_{ind}", "family": fam, "odds_mode": mode, "rank_scheme": "", "indicator": ind,
                "region": "全場", "件数": 30}
        con.execute(f'INSERT INTO unified_all VALUES (?, {", ".join("?" * len(cols))})', [key] + [vals.get(c) for c in cols])
    add("芝×1600", "N_sd_dist", CONDITION_ONLY)
    add("芝×1600", "N_sd_dist", CONDITION_ONLY, "odds10u")
    add("芝×1600", "N_sd_dist", "SAV")                                 # 指数の名前が違う行（古い集計の残りなど）は引かない
    add("東京×芝×1600×定量×1・2_勝×3", "normal", "SAV")
    row = {"場所": "東京", "芝・ダ": "芝", "距離": "1600", "距離_raw": "1600", "重量種別": "定量", "クラス名": "1・2_勝",
           "馬番": "1", "SAV_順位": "3", "SAV_偏差値": "55", "SAV_偏差値帯": "51~60", "ZI_順位": "5", "ZI_偏差値": "50",
           "ZI_偏差値帯": "41~50"}
    assert matching.indicator_bases(row.keys()) == ["SAV", "ZI"]
    bases = matching.match_bases(row.keys())
    assert bases == ["SAV", "ZI", CONDITION_ONLY]
    hits = matching.match_entry_row(con, row, bases, "entries_x")
    got = sorted((h["base"], h["family"], h["key"], h["odds_mode"], h["indicator"]) for h in hits)
    assert got == [("SAV", "normal", "東京×芝×1600×定量×1・2_勝×3", "normal", "SAV"),
                   (CONDITION_ONLY, "N_sd_dist", "芝×1600", "normal", CONDITION_ONLY),
                   (CONDITION_ONLY, "N_sd_dist", "芝×1600", "odds10u", CONDITION_ONLY)]
    # 指数だけを渡せば（--mining-only の一部の作り直し）、条件のみの行は作らない
    assert all(h["base"] == "SAV" for h in matching.match_entry_row(con, row, ["SAV"], "entries_x"))
    assert matching.entry_fields(row, CONDITION_ONLY)["順位"] == "" and matching.entry_fields(row, "SAV")["順位"] == "3"
    F.set_active([F.Family("normal", ("COURSE", "重量種別", "クラス名"), "RANK")])
    assert matching.match_bases(row.keys()) == ["SAV", "ZI"]           # 条件のみの family が無ければ足さない
    F.reset()


# ---------------------------------------------------------------------------
# 全段（replace → aggregate → calc → build_db → 画面）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def site(tmp_path_factory, monkeypatch_module):
    root = tmp_path_factory.mktemp("round2")
    monkeypatch_module.setenv("SHISU_BASE_DIR", str(root))
    for k in ("RESULT_INDICES_DIR", "RACE_MARKS_DIR", "SHISU_EXT_DIR", "DB_PATH", "SHISU_FAMILIES_FILE", "NOTES_PATH"):
        monkeypatch_module.delenv(k, raising=False)
    base, marks, ent, names = synth2.write_site(root)
    # 指数の欠けたレース（2頭の指数が空 → そのレースは 指数有効=0）を作る: 条件のみの集計は、その馬も数える
    rid16 = base[RID].str[:16]
    for r in sorted(set(rid16))[::9]:
        idx = base.index[rid16 == r][:2]
        base.loc[idx, "指数"] = ""
    base.to_csv(root / "csv_files" / "全場_タイムM.csv", index=False, encoding="cp932")
    rng = random.Random(2)
    other = base.assign(指数=[str(rng.randint(40, 110)) for _ in range(len(base))]).drop(columns=["指数順位"])
    other.to_csv(root / "csv_files" / "全場_対戦M.csv", index=False, encoding="cp932")
    (root / "selected_families.json").write_text(json.dumps({"families": FAMILIES}, ensure_ascii=False), encoding="utf-8")
    import aggregate_stats_ver2, build_db, calc_entry_hensachi, replace_csv_ver3
    assert replace_csv_ver3.main(["--clean"]) == 0
    assert aggregate_stats_ver2.main(["--clean", *AGG_ARGS]) == 0
    assert calc_entry_hensachi.main(["--merge-only", "--clean"]) == 0
    assert build_db.main(["--fresh"]) == 0
    return root, base, marks, ent


def read_master(root, indicator="タイムM"):
    return pd.read_csv(root / "master_csv" / f"master_全場_{indicator}.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)


def expect_marks(m: dict | None, prev_sd: str, shown_r3: str) -> dict:
    """別に書いた区分（印 m は synth2 の値）。"""
    r3 = {"HHH": "HHH~HH", "HH": "HHH~HH", "H": "H", "M": "M", "S": "S", "SS": "SS~SSS", "SSS": "SS~SSS"}
    out = {"前走メンバーLv": "", "前走タイムLv": "", "前走クッション帯": "", "前走含水率帯": "", "前走R印3": r3.get(shown_r3, "")}
    if m is None:
        return out
    out["前走メンバーLv"] = m["member"] if m["member"] in "ABCDE" else ""
    out["前走タイムLv"] = m["time"] if m["time"] in "SABCDE" else ""
    out["前走R印3"] = out["前走R印3"] or r3[m["pace"]]
    if prev_sd == "芝" and m["cushion"] != "----":
        v = float(m["cushion"])
        out["前走クッション帯"] = "軟(8.9以下)" if v < 9 else ("標準(9.0~9.9)" if v < 10 else "硬(10.0以上)")
    if prev_sd == "芝":
        v = float(m["moist_turf"])
        out["前走含水率帯"] = "芝乾(9.9以下)" if v < 10 else ("芝標準(10.0~14.9)" if v < 15 else "芝湿(15.0以上)")
    elif prev_sd == "ダ":
        v = float(m["moist_dirt"])
        out["前走含水率帯"] = "ダ乾(4.9以下)" if v < 5 else ("ダ標準(5.0~9.9)" if v < 10 else "ダ湿(10.0以上)")
    return out


def test_masters_carry_the_mark_and_recent_form_conditions(site):
    root, base, marks, _ent = site
    from shisu import race_marks as RM
    assert (root / "master_csv" / RM.FILE).exists() and len(RM.load_cache(root / "master_csv")) == len(marks)
    by_rid = base.set_index(RID)
    recent = recent_oracle(base).set_index(base[RID])
    for ind in ("タイムM", "対戦M"):
        m = read_master(root, ind)
        assert len(m) == len(base)
        src = by_rid.loc[m[RID]]
        want = [expect_marks(marks.get(p[:16]) if p else None, sd, r3)
                for p, sd, r3 in zip(src["前走レースID(新)"], src["前芝・ダ"], src["前走レース印３"])]
        for c in ("前走メンバーLv", "前走タイムLv", "前走クッション帯", "前走含水率帯", "前走R印3"):
            assert m[c].tolist() == [w[c] for w in want], (ind, c)
        for c in ("近5走同条件", "近5走同場"):
            assert m[c].tolist() == recent.loc[m[RID], c].tolist(), (ind, c)
    m = read_master(root)
    # 空の一致でないこと。前走R印3 は、基本CSVが空の馬が印で埋まり、値のある馬は基本CSVの値のまま
    for c, n in (("前走メンバーLv", 5), ("前走タイムLv", 6), ("前走クッション帯", 3), ("前走含水率帯", 6), ("近5走同条件", 3),
                 ("近5走同場", 3)):
        assert len(set(m[c]) - {""}) == n, c
    shown = by_rid.loc[m[RID], "前走レース印３"].to_numpy()
    has_prev = (by_rid.loc[m[RID], "前走レースID(新)"] != "").to_numpy()
    assert ((m["前走R印3"] != "") == has_prev).all() and (shown[has_prev] == "").sum() > 300
    assert {"H", "SS~SSS"} <= set(m["前走R印3"][shown == ""])               # 基本CSVに無い区分は印から来ている


def test_entries_side_gets_the_same_conditions_from_marks_and_history(site, monkeypatch, capsys):
    root, base, marks, ent = site
    import build_db
    con = sqlite3.connect(root / "racing.db")
    e = pd.read_sql('SELECT * FROM entries_merged_hensachi ORDER BY "レースID(新)"', con)
    ent = ent.sort_values(RID).reset_index(drop=True)
    assert e[RID].tolist() == ent[RID].tolist()
    ran = base[base["着順"] != "取消"]
    last = ran.groupby("血統登録番号").tail(1).set_index("血統登録番号")
    want = []
    for _, r in ent.iterrows():
        prev = last.loc[r["血統登録番号"]] if r["血統登録番号"] in last.index else None
        want.append(expect_marks(marks.get(prev[RID][:16]) if prev is not None else None,
                                 prev["距離"][0] if prev is not None else "", r["前レース印3"]))
    for c in ("前走メンバーLv", "前走タイムLv", "前走クッション帯", "前走含水率帯", "前走R印3"):
        assert e[c].fillna("").tolist() == [w[c] for w in want], c
    assert sum(1 for w in want if w["前走メンバーLv"]) >= 10 and sum(1 for w in want if w["前走クッション帯"]) >= 3
    as_base = pd.DataFrame({RID: ent[RID], "距離": ent["芝ダ"] + ent["距離"], "場所": ent["場所"],
                            "血統登録番号": ent["血統登録番号"], "着順": ""})
    recent = recent_oracle(base, as_base)
    for c in ("近5走同条件", "近5走同場"):
        assert e[c].fillna("").tolist() == recent[c].tolist(), c
    assert (recent["近5走同条件"] != "").sum() >= 8
    # 出馬表側は race_marks/ を先に読む（先週のレースの印が足されても引ける）。無ければ master_csv の表
    raw = pd.read_csv(root / "entries_out" / "entries_merged_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False)
    marks_obj, history = build_db.entries_side_sources(log=lambda m: None)
    a = build_db.normalize_entries_df(raw, None, marks_obj, history)
    monkeypatch.setenv("RACE_MARKS_DIR", str(root / "nope"))
    marks_cached, _h = build_db.entries_side_sources(log=lambda m: None)
    b = build_db.normalize_entries_df(raw, None, marks_cached, history)
    order = raw[RID].argsort(kind="stable").to_numpy()               # DB の表（e）は レースID(新) の順
    assert raw[RID].to_numpy()[order].tolist() == e[RID].tolist()
    for c in ("前走メンバーLv", "前走タイムLv", "前走クッション帯", "前走含水率帯", "前走R印3", "近5走同条件", "近5走同場"):
        in_db = e[c].fillna("").tolist()
        assert a[c].to_numpy()[order].tolist() == in_db, c            # race_marks/ から
        assert b[c].to_numpy()[order].tolist() == in_db, c            # master_csv の表から
    plain = build_db.normalize_entries_df(raw)                       # 印・履歴を渡さなければ空
    assert all((plain[c] == "").all() for c in ("前走メンバーLv", "近5走同条件", "近5走同場"))


def test_condition_only_tables_are_aggregated_once_from_the_base_master(site):
    root, _base, _marks, _ent = site
    from shisu.aggstore import read_agg_master, read_stamp
    files = sorted(p.name for p in (root / "agg_csv").glob("agg_master_*.csv.gz"))
    assert files == ["agg_master_全場_タイムM.csv.gz", "agg_master_全場_対戦M.csv.gz", "agg_master_全場_条件のみ.csv.gz"]
    none = read_agg_master(root / "agg_csv" / "agg_master_全場_条件のみ.csv.gz")
    assert set(none["family"]) == set(NONE_NAMES) and set(none["indicator"]) == {CONDITION_ONLY} and set(none["region"]) == {"全場"}
    assert all(t.endswith("_全場_条件のみ") for t in none["テーブル名"])
    assert set(none.loc[none["family"] == "N_r5plc_pfin", "rank_scheme"]) == {"s1", "s2"}
    assert set(none.loc[none["family"] != "N_r5plc_pfin", "rank_scheme"]) == {""}
    for ind in ("タイムM", "対戦M"):                                   # 指数ごとの成績表には入らない
        t = read_agg_master(root / "agg_csv" / f"agg_master_全場_{ind}.csv.gz")
        assert set(t["family"]) == {"normal", "X_jk_r5cond", "X_pmem_hen"} and set(t["indicator"]) == {ind}
    st = read_stamp(root / "agg_csv" / "agg_master_全場_条件のみ.csv.gz")
    assert st["master"] == "master_全場_タイムM.csv.gz"                # 基準の master = タイムM
    # 件数は master の全部の行（指数有効=0 の馬も）から。取消は分母に入れない
    m = read_master(root)
    assert (m["指数有効"] == "0").sum() >= 100
    ran = m[m["着順"] != "取消"]
    for fam, parts in (("N_sd_dist", ["芝・ダ", "距離"]), ("N_jk_r5cond", ["騎手", "近5走同条件"]), ("N_marks", ["前走メンバーLv", "前走タイムLv"]),
                       ("N_cush_moist", ["前走クッション帯", "前走含水率帯"])):
        ok = ran[(ran[parts] != "").all(axis=1)]
        want = ok.groupby(parts).size()
        want = {"×".join(k if isinstance(k, tuple) else (k,)): int(n) for k, n in want.items() if n >= 5}
        t = none[(none["family"] == fam) & (none["odds_mode"] == "normal")]
        assert dict(zip(t["結合データ"], t["件数"].astype(int))) == want, fam
        only_valid = ok[ok["指数有効"] == "1"].groupby(parts).size()
        assert int(only_valid.sum()) < sum(want.values()) or fam != "N_sd_dist"      # 指数有効=1 だけなら少ない
    crs = none[(none["family"] == "N_crs") & (none["odds_mode"] == "normal")]
    # 距離はレンジ統合後の値（東京芝1600 → 16-18、東京ダ1400 → 13-14）。末尾が数字だけのキーもある
    assert set(crs["結合データ"]) == {"東京×芝×16-18", "東京×芝×2000", "東京×ダ×13-14", "東京×ダ×1600", "中山×芝×1800", "中山×ダ×1200"}
    win = ran[(ran["場所"] == "東京") & (ran["芝・ダ"] == "ダ") & (ran["距離"] == "1600")]
    row = crs[crs["結合データ"] == "東京×ダ×1600"].iloc[0]
    assert int(row["件数"]) == len(win) and abs(float(row["勝率_val"]) - (win["着順"] == "1").mean() * 100) < 0.01


def test_resume_covers_the_condition_only_job_and_stale_files_are_removed(site, capsys):
    root, _base, _marks, _ent = site
    import aggregate_stats_ver2
    path = root / "agg_csv" / "agg_master_全場_条件のみ.csv.gz"
    before = path.read_bytes()
    capsys.readouterr()
    assert aggregate_stats_ver2.main(["--resume", *AGG_ARGS]) == 0
    out = capsys.readouterr().out
    assert "--resume: 3 本は今の設定で済んでいるので飛ばします（残り 0 本）" in out and path.read_bytes() == before
    assert "条件のみの family 6 種は、地域ごとの基準の master だけで集計します" in out and "全場 = master_全場_タイムM.csv.gz" in out
    path.unlink()                                                     # 条件のみの成績表だけ消えた → その分だけ作り直す
    assert aggregate_stats_ver2.main(["--resume", *AGG_ARGS]) == 0
    out = capsys.readouterr().out
    assert "（残り 1 本）" in out and "[OK] master_全場_タイムM.csv.gz（条件のみ）" in out and path.read_bytes() == before
    # 条件のみの family をやめたら、前の成績表を残さない（別の場所で）
    alt = root / "agg_alt"
    assert aggregate_stats_ver2.main(["--out-dir", str(alt), *AGG_ARGS]) == 0
    assert (alt / path.name).exists()
    fams = root / "no_none.json"
    fams.write_text(json.dumps({"families": [d for d in FAMILIES if d["tail"] != "NONE"]}, ensure_ascii=False), encoding="utf-8")
    assert aggregate_stats_ver2.main(["--out-dir", str(alt), "--families", str(fams), *AGG_ARGS]) == 0
    assert not (alt / path.name).exists() and not (alt / (path.name + ".done")).exists()
    assert sorted(p.name for p in alt.glob("agg_master_*.csv.gz")) == ["agg_master_全場_タイムM.csv.gz", "agg_master_全場_対戦M.csv.gz"]
    # 条件のみの family だけにしたら、指数ごとの集計はしない
    only = root / "only_none.json"
    only.write_text(json.dumps({"families": [d for d in FAMILIES if d["tail"] == "NONE"]}, ensure_ascii=False), encoding="utf-8")
    alt2 = root / "agg_alt2"
    assert aggregate_stats_ver2.main(["--out-dir", str(alt2), "--families", str(only), *AGG_ARGS]) == 0
    assert sorted(p.name for p in alt2.glob("agg_master_*.csv.gz")) == [path.name]
    F.configure(root / "selected_families.json", log=lambda m: None)


def db(root):
    con = sqlite3.connect(root / "racing.db")
    con.row_factory = sqlite3.Row
    return con


def test_db_and_xjoin_hold_condition_only_rows_once(site, monkeypatch):
    root, _base, _marks, _ent = site
    import app as am
    monkeypatch.setattr(am, "DB_PATH", str(root / "racing.db"))      # app は import したときの DB を覚えている
    con = db(root)
    rows = [dict(r) for r in con.execute('SELECT family, indicator, COUNT(*) n FROM unified_all GROUP BY family, indicator')]
    by = {(r["family"], r["indicator"]): r["n"] for r in rows}
    assert {f for f, i in by if i == CONDITION_ONLY} == set(NONE_NAMES)
    assert all(i == CONDITION_ONLY for f, i in by if f in NONE_NAMES)   # 条件のみの family は「条件のみ」でだけ
    assert {i for f, i in by if f == "normal"} == {"タイムM", "対戦M"}
    dup = con.execute('SELECT COUNT(*) FROM (SELECT 1 FROM unified_all WHERE indicator = ? GROUP BY family, rank_scheme, '
                      'odds_mode, region, "結合データ" HAVING COUNT(*) > 1)', (CONDITION_ONLY,)).fetchone()[0]
    assert dup == 0
    fams = {r["name"]: r["tail"] for r in con.execute("SELECT name, tail FROM __families__")}
    assert fams["N_sd_dist"] == "NONE" and fams["normal"] == "RANK"
    meta = [dict(r) for r in con.execute("SELECT * FROM __tables_meta__ WHERE kind = 'foragg' AND indicator = ?", (CONDITION_ONLY,))]
    assert meta and {m["family"] for m in meta} == set(NONE_NAMES)
    # 照合結果: 条件のみの行は base = 条件のみ で、1頭・1キー・1オッズ帯につき1行
    x = [dict(r) for r in con.execute("SELECT * FROM entries_xjoin_merged_hensachi")]
    plain = [h for h in x if h["family"] in NONE_NAMES]
    assert plain and all(h["base"] == CONDITION_ONLY == h["indicator"] for h in plain)
    assert all(h["base"] in ("タイムM", "対戦M") for h in x if h["family"] not in NONE_NAMES)
    ident = [(h["馬番"], h[RID], h["family"], h["rank_scheme"], h["odds_mode"], h["key"]) for h in plain]
    assert len(ident) == len(set(ident))
    # 出馬表の全馬に 芝・ダ×距離 の「通常」の行がある（コース固定でない条件でも当たる）
    n_horses = con.execute("SELECT COUNT(*) FROM entries_merged_hensachi").fetchone()[0]
    sd = [h for h in plain if h["family"] == "N_sd_dist" and h["odds_mode"] == "normal"]
    assert len(sd) == n_horses == 24 and {h["key"] for h in sd} == {"芝×16-18", "ダ×1600"}
    # その場照合（xjoin なし）と同じ中身
    with am.connect() as c2:
        race = [dict(r) for r in c2.execute("SELECT * FROM entries_merged_hensachi")]
        bases = matching.match_bases(race[0].keys())
        assert bases == ["タイムM", CONDITION_ONLY]               # 出馬表の指数（マイニング値）と、条件のみ
        fly = [h for r in race for h in matching.match_entry_row(c2, r, bases, "entries_merged_hensachi")]
    key = lambda h: (h["馬番"], h[RID], h["base"], h["family"], h["rank_scheme"] or "", h["odds_mode"], h["key"], h["件数"])
    assert sorted(map(key, fly)) == sorted(map(key, x))


def test_pages_presets_and_drill_for_condition_only_rows(site, monkeypatch):
    root, _base, _marks, ent = site
    import app as am
    monkeypatch.setattr(am, "DB_PATH", str(root / "racing.db"))
    from shisu import drill
    c = am.app.test_client()
    rid = ent[RID].iloc[0][:16]
    page = c.get(f"/entries/hits?table=entries_merged_hensachi&rid={rid}").get_data(as_text=True)
    assert "条件ヒット馬" in page
    wedge = c.get(f"/entries/wedge?table=entries_merged_hensachi&rid={rid}&odds_mode=all").get_data(as_text=True)
    assert CONDITION_ONLY in wedge and "芝・ダ×距離×条件のみ" in wedge
    csv = c.get(f"/entries/wedge.csv?table=entries_merged_hensachi&rid={rid}&odds_mode=all").get_data(as_text=True)
    assert f",{CONDITION_ONLY}," in csv and "N_sd_dist" in csv
    top = c.get("/", query_string={"indicator": CONDITION_ONLY, "view": "all"}).get_data(as_text=True)
    assert "N_sd_dist" in top or "芝・ダ×距離" in top
    # 横断検索のプリセット（SQL）と1行版の区分が、条件のみの行でも同じ（指数の上限ゲートを掛けない）
    con = db(root)
    rows = [dict(r) for r in con.execute("SELECT rowid AS id, * FROM unified_all")]
    n_plain_cat = 0
    for mode in odds.ODDS_MODES:
        sub = [r for r in rows if r["odds_mode"] == mode]
        want = {r["id"]: C.classify({"cnt": r["件数"], "fv": r["複勝率_val"], "rv": r["連対率_val"], "top5": r["掲示板率_val"],
                                     "frc": r["複勝回収値"], "tra": r["単勝適正回収値"], "key": r["結合データ"],
                                     "indicator": r["indicator"]}, mode) for r in sub}
        for cat in C.CATEGORIES:
            got = {x[0] for x in con.execute(f"SELECT rowid FROM unified_all WHERE odds_mode = ? AND "
                                             f"{C.sql_condition(cat, mode, indicator_col='indicator')}", (mode,))}
            assert got == {i for i, w in want.items() if w == cat}, (mode, cat)
        n_plain_cat += sum(1 for r in sub if r["indicator"] == CONDITION_ONLY and want[r["id"]])
    assert n_plain_cat > 0
    # 逆引き: 条件のみの行は基準の master（タイムM）の全部の行から。件数は成績表と同じ
    assert drill.find_master("全場", CONDITION_ONLY, root / "master_csv").name == "master_全場_タイムM.csv.gz"
    assert drill.find_master("東京", CONDITION_ONLY, root / "master_csv") is None
    checked = 0
    for r in rows:
        if r["indicator"] != CONDITION_ONLY or r["odds_mode"] not in ("normal", "odds10u") or r["id"] % 4:
            continue
        df = drill.extract(root / "master_csv" / "master_全場_タイムM.csv.gz", F.get(r["family"]), r["rank_scheme"] or "",
                           r["odds_mode"], r["結合データ"])
        assert len(drill.counted(df)) == r["件数"], (r["family"], r["結合データ"])
        checked += 1
    assert checked >= 20
    one = next(r for r in rows if r["family"] == "N_sd_dist" and r["odds_mode"] == "normal" and r["結合データ"] == "ダ×1600")
    page = c.get("/entries/drill", query_string={"region": "全場", "indicator": CONDITION_ONLY, "family": "N_sd_dist",
                                                 "rank_scheme": "", "odds_mode": "normal", "key": "ダ×1600"})
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "master_全場_タイムM.csv.gz" in text and str(one["件数"]) in text
    assert "件数が違います" not in text and "合いません" not in text


def test_mining_only_refresh_keeps_condition_only_rows_and_matches_a_full_rebuild(site, capsys):
    root, _base, _marks, ent = site
    import build_db, calc_entry_hensachi
    table = "entries_xjoin_merged_hensachi"
    snap = lambda: sorted(tuple(r) for r in sqlite3.connect(root / "racing.db").execute(
        f'SELECT "馬番", "レースID(新)", base, family, rank_scheme, odds_mode, key, "件数", "複勝率_val" FROM {table}'))
    before = snap()
    new = ent.copy()
    new["マイニング値"] = [str(120 - int(v)) for v in new["マイニング値"]]            # 順位が入れ替わる
    new["マイニング順位"] = ""
    new.to_csv(root / "entries_in" / f"shisu_{synth2.ENTRY_DATE}_1.csv", index=False, encoding="cp932")
    capsys.readouterr()
    assert calc_entry_hensachi.main(["--mining-only"]) == 0
    assert build_db.main(["--entries-only", "--mining-only"]) == 0
    out = capsys.readouterr().out
    assert "全部の指数を照合し直します" not in out
    partial = snap()
    assert partial != before
    assert [r for r in partial if r[2] == CONDITION_ONLY] == [r for r in before if r[2] == CONDITION_ONLY]
    assert build_db.main(["--entries-only"]) == 0
    assert snap() == partial


def test_screening_scores_condition_only_candidates_on_the_base_master_only(site, monkeypatch, capsys):
    root, _base, _marks, _ent = site
    import screen_families as SF
    monkeypatch.setattr(SF, "templates", lambda space="default", pool=SF.POOL: [
        (("芝・ダ", "距離"), "s3:test"), (("騎手", "近5走同条件"), "s0:pair"), (("前走メンバーLv", "前走タイムLv"), "s0:pair")])
    report = root / "report.csv"
    assert SF.main(["--tails", "RBAND,XYZ", "--report", str(report)]) == 2
    assert SF.main(["--tails", "", "--report", str(report)]) == 2
    capsys.readouterr()
    out_json = root / "picked.json"
    assert SF.main(["--tails", "rband, none", "--report", str(report), "--wedge-min-year-count", "2", "--min-axis", "0",
                    "--export-selected", str(out_json)]) == 0
    log = capsys.readouterr().out
    assert "条件のみ（NONE）の候補 3 family は、基準の master だけで採点します" in log and "全場 = master_全場_タイムM.csv.gz" in log
    rep = pd.read_csv(report, dtype=str, keep_default_na=False, encoding="cp932")
    none = rep[rep["tail"] == "NONE"]
    assert sorted(none["name"]) == ["X_jk_r5cond_none", "X_pmem_ptime_none", "X_sd_dist_none"]
    assert set(none["masters"]) == {"1"} and set(rep.loc[rep["tail"] == "RBAND", "masters"]) == {"2"}    # 基準の master 1本だけ
    assert (none["wedge_spread"] == "").all() and (none["fuku_spread"] == "").all()                       # 指数の帯が無い
    # 基準B の数は、本番の DB（同じ部品の family を条件のみで集計した成績表）の区分の数と同じ
    con = db(root)
    for name, fam in (("X_sd_dist_none", "N_sd_dist"), ("X_jk_r5cond_none", "N_jk_r5cond"), ("X_pmem_ptime_none", "N_marks")):
        r = none[none["name"] == name].iloc[0]
        for mode in odds.ODDS_MODES:
            rows = [dict(x) for x in con.execute("SELECT * FROM unified_all WHERE family = ? AND odds_mode = ?", (fam, mode))]
            cats = [C.classify({"cnt": x["件数"], "fv": x["複勝率_val"], "rv": x["連対率_val"], "top5": x["掲示板率_val"],
                                "frc": x["複勝回収値"], "tra": x["単勝適正回収値"], "key": x["結合データ"],
                                "indicator": x["indicator"]}, mode) for x in rows]
            for c in ("abs", "axis", "opp", "keshi"):
                assert int(r[f"{c}_{mode}"]) == cats.count(c), (name, mode, c)
        assert int(r["groups"]) == con.execute("SELECT COUNT(*) FROM unified_all WHERE family = ? AND odds_mode = 'normal'",
                                               (fam,)).fetchone()[0]
    picked = F.load_families(out_json)
    assert any(f.tail == "NONE" and f.name.endswith("_none") for f in picked)
    # 採点し直さずに選抜だけやり直しても、条件のみの family がそのまま出る
    assert SF.main(["--from-report", str(report), "--min-axis", "0", "--export-selected", str(root / "picked2.json")]) == 0
    assert sorted(f.name for f in F.load_families(root / "picked2.json")) == sorted(f.name for f in picked)
    F.configure(root / "selected_families.json", log=lambda m: None)
