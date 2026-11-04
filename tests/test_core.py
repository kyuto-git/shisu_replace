"""shisu パッケージの単体テスト。"""
import itertools
import random
import sqlite3

import numpy as np
import pandas as pd
import pytest

from shisu import classify as C
from shisu import hensachi, indicators, keys, naming, normalize, odds, wedge
from shisu.csvio import norm_series, norm_text, read_csv_any, write_csv
from shisu.finish import parse_finish


# ---------------------------------------------------------------- 正規化
@pytest.mark.parametrize("raw,expected", [(None, ""), (float("nan"), ""), ("nan", ""), ("None", ""),
                                          (" Ｇ１ ", "G1"), ("髙杉　吏麒", "髙杉吏麒"), ("ｵｰﾌﾟﾝ", "オープン")])
def test_norm_text(raw, expected):
    assert norm_text(raw) == expected


def test_norm_series_matches_scalar():
    s = pd.Series([None, np.nan, "nan", " Ｃ．ルメール ", "１勝", "66~70"])
    assert norm_series(s).tolist() == [norm_text(v) for v in s]


@pytest.mark.parametrize("raw,expected", [("1勝", "1・2_勝"), ("１勝", "1・2_勝"), ("1000万", "1・2_勝"),
                                          ("3勝", "準OP・OP"), ("ｵｰﾌﾟﾝ", "準OP・OP"), ("OP(L)", "準OP・OP"),
                                          ("Ｇ３", "重賞"), ("Ｇ２", "重賞"), ("Ｇ１", "G1"), ("G1", "G1"),
                                          ("未勝利", "未勝利"), ("新馬", "新馬")])
def test_class(raw, expected):
    assert normalize.norm_class(raw) == expected


def test_age_class_rule():
    cls = pd.Series(["1・2_勝", "準OP・OP", "重賞", "1・2_勝"])
    w = pd.Series(["馬齢", "馬齢", "馬齢", "定量"])
    assert normalize.apply_age_class_rule(cls, w).tolist() == ["1勝・OP", "1勝・OP", "重賞", "1・2_勝"]


@pytest.mark.parametrize("name,age,cls,expected", [
    ("むらさＨ･3勝", "４歳以上", "準OP・OP", "ハンデ"), ("目黒記念ＨG2", "４歳以上", "重賞", "ハンデ"),
    ("安土城Ｈ(L)", "４歳以上", "準OP・OP", "ハンデ"), ("NHKマイルC", "３歳", "G1", "定量"),
    ("HTB賞", "３歳以上", "1・2_勝", "定量"), ("東京優駿G1", "３歳", "G1", "定量"),
    ("未勝利", "３歳", "未勝利", "馬齢"), ("朝日杯FSG1", "２歳", "G1", "馬齢"), ("未勝利", "", "未勝利", ""),
])
def test_infer_weight(name, age, cls, expected):
    assert normalize.infer_entry_weight(name, age, cls) == expected


def test_weight_norm():
    assert normalize.weight_series(pd.Series(["別定", "定量", "ハンデ", "馬齢"])).tolist() == ["定量", "定量", "ハンデ", "馬齢"]


def test_distance_rules():
    place = pd.Series(["東京", "東京", "京都", "京都", "京都", "新潟", "中京", "札幌"])
    sf = pd.Series(["芝", "ダ", "芝", "芝", "ダ", "芝", "芝", "芝"])
    dist = normalize.distance_series(pd.Series(["1800m", "1400", "2000", "1600", "1900", "1600", "1200", "1500.0"]))
    inner = pd.Series(["", "", "内", "外", "", "外", "", ""])
    d, i = normalize.apply_distance_rules(place, sf, dist, inner)
    assert d.tolist() == ["16-18", "13-14", "2000", "14-18", "18-19", "16-18", "1200", "1500"]
    assert i.tolist() == ["", "", "内外", "外", "", "外", "", ""]


@pytest.mark.parametrize("waku,heads,expected", [(1, 9, ""), (5, 12, "内枠"), (6, 12, "外枠"), (4, 16, "内枠"),
                                                 (5, 16, "外枠"), (3, 18, "内枠"), (4, 18, "中枠"), (8, 18, "外枠"),
                                                 ("", 16, ""), (1, "", "")])
def test_frame_group(waku, heads, expected):
    assert normalize.frame_group(waku, heads) == expected


@pytest.mark.parametrize("place,sf,gai,expected", [("京都", "芝", "外", "外"), ("京都", "芝", "", "内"),
                                                   ("京都", "ダ", "", ""), ("新潟", "芝", "", "内"),
                                                   ("東京", "芝", "", ""), ("阪神", "芝", "外", "外")])
def test_entry_inner_outer(place, sf, gai, expected):
    assert normalize.entry_inner_outer(place, sf, gai) == expected


def test_race_ids():
    rid = pd.Series(["202605310502120101", "", "abc"])
    assert normalize.race_id_without_horse(rid).tolist() == ["2026053105021201", "", "a"]
    assert normalize.race_date(rid).tolist() == ["20260531", "", ""]


# ---------------------------------------------------------------- 指数台帳
@pytest.mark.parametrize("raw,canon", [("GoldAirplane", "Gold"), ("マイニング値", "タイムM"), ("対戦型マイニング値", "対戦M"),
                                       ("先行力", "先行"), ("持久力", "持久"), ("吉馬_先行力", "吉馬_先行力"),
                                       ("keibaai指数", "keibaAI指数"), ("想定前半３Ｆ", "想定前半"), ("ZI", "ZI")])
def test_canonical_indicator(raw, canon):
    assert indicators.canonical_indicator(raw) == canon


@pytest.mark.parametrize("stem,region,ind", [
    ("主５場（中山、東京、中京、京都、阪神）_吉馬_先行力", "主5場(中山、東京、中京、京都、阪神)", "吉馬_先行力"),
    ("ローカル５場（札幌、函館、福島、新潟、小倉）_先行", "ローカル5場(札幌、函館、福島、新潟、小倉)", "先行"),
    ("札幌_カツラキ", "札幌", "カツラキ"), ("吉馬_SP能力値", "", "吉馬_SP能力値"), ("東京_未知の指数", "東京", "未知の指数"),
])
def test_parse_source_name(stem, region, ind):
    assert indicators.parse_source_name(stem) == (region, ind)


def test_indicator_flags():
    assert indicators.spec_for("マイニング値").reverse
    assert indicators.spec_for("ZI").zi and indicators.spec_for("ZI").exclude_shinba
    assert indicators.spec_for("吉馬_SP最大").exclude_shinba
    assert not indicators.spec_for("対戦M").reverse


# ---------------------------------------------------------------- 偏差値
def _ci(vals, races, name="カツラキ", shinba=None, rank=None):
    return hensachi.compute_indicator(pd.Series(vals, dtype=object), pd.Series(races), indicators.spec_for(name),
                                      None if shinba is None else pd.Series(shinba), rank)


def test_hensachi_basic_and_bands():
    r = _ci([10, 20, 30], ["a"] * 3)
    assert r["偏差値"].tolist() == [38.0, 50.0, 62.0]
    assert r["偏差値帯"].tolist() == ["40以下", "41~50", "61~65"]
    assert r["順位"].tolist() == [3, 2, 1]
    assert r["順位帯_s1"].tolist() == ["1~3位"] * 3


def test_all_same_value_race_is_excluded():
    """全頭0（未取得）や全頭同値はレースごと使わない（旧版の出馬表側は全頭50・1位にしていた）。"""
    r = _ci([0, 0, 0, 1, 2], ["a", "a", "a", "b", "b"])
    assert r.loc[:2, "race_excluded"].all() and not r.loc[3:, "race_excluded"].any()
    assert r.loc[:2, "偏差値"].isna().all() and r.loc[:2, "順位"].isna().all()


def test_missing_policy():
    r = _ci([None, None, 5, 6, None, 7, 8], ["a"] * 4 + ["b"] * 3)
    assert r.loc[:3, "race_excluded"].all()                    # 欠損2頭 → レース除外
    assert not r.loc[4:, "race_excluded"].any()
    assert pd.isna(r.loc[4, "順位"]) and pd.isna(r.loc[4, "偏差値"])   # 欠損1頭 → その馬だけ空
    assert r.loc[5:, "順位"].tolist() == [2, 1]


def test_zi_special():
    r = _ci([0, 100, 110, None], ["a"] * 4, name="ZI")
    assert r["偏差値"].tolist()[0] == 50.0           # 0 → 非ゼロ平均
    assert pd.isna(r.loc[3, "順位"])                   # 欠損1頭は順位なし


def test_reverse_and_shinba():
    r = _ci([30, 20, 10], ["a"] * 3, name="タイムM")
    assert r["順位"].tolist() == [3, 2, 1]
    r = _ci([1, 2, 3], ["a"] * 3, name="吉馬_SP能力値", shinba=[True, True, True])
    assert r["shinba_excluded"].all() and r["偏差値"].isna().all()
    r = _ci([1, 2, 3], ["a"] * 3, name="カツラキ", shinba=[True, True, True])
    assert not r["shinba_excluded"].any()


def test_given_rank_preferred():
    r = _ci([10, 20, 30], ["a"] * 3, rank=pd.Series(["1", "2", "3"]))
    assert r["順位"].tolist() == [1, 2, 3]


@pytest.mark.parametrize("rank,s1,s2", [(1, "1~3位", "1~5位"), (3, "1~3位", "1~5位"), (4, "4~6位", "1~5位"),
                                        (6, "4~6位", "6~10位"), (9, "7~9位", "6~10位"), (10, "10位~", "6~10位"),
                                        (11, "10位~", "11位~"), (None, "", "")])
def test_rank_bands(rank, s1, s2):
    assert hensachi.rank_band(rank, "s1") == s1 and hensachi.rank_band(rank, "s2") == s2


# ---------------------------------------------------------------- キー
FIELDS = {"場所": "東京", "芝・ダ": "ダ", "距離": "1600", "芝(内・外)": "", "重量種別": "定量", "クラス名": "1・2_勝",
          "枠番グループ": "内枠", "騎手": "髙杉吏麒", "順位": "4", "偏差値帯": "61~65"}


def test_build_key_examples():
    assert keys.build_key(FIELDS, "normal") == "東京×ダ×1600×定量×1・2_勝×4"
    assert keys.build_key({**FIELDS, "芝(内・外)": "外"}, "rankB") == "東京×ダ×1600×外×内枠×4"
    assert keys.build_key(FIELDS, "normal_band", "4~6位") == "東京×ダ×1600×定量×1・2_勝×4~6位"
    assert keys.build_key({**FIELDS, "騎手": ""}, "rankJ") == ""      # 部品が空ならキーを作らない
    assert keys.build_key({**FIELDS, "距離": ""}, "normal") == ""


def test_vector_and_scalar_keys_agree():
    rng = random.Random(0)
    rows = []
    for _ in range(300):
        rows.append({k: rng.choice([v, ""]) if k in ("芝(内・外)", "騎手", "順位", "重量種別") else v
                     for k, v in {**FIELDS, "芝(内・外)": rng.choice(["", "内", "外"]),
                                  "順位": str(rng.randint(1, 18))}.items()})
    df = pd.DataFrame(rows)
    for s in keys.SCHEMES:
        df[f"rb_{s}"] = [hensachi.rank_band(int(r) if r else None, s) for r in df["順位"]]
    for fam in keys.ALL_FAMILIES:
        for s in (keys.SCHEMES if keys.is_band_family(fam) else ("",)):
            col = f"rb_{s}" if s else None
            vec = keys.build_key_series(df, fam, col).tolist()
            sca = [keys.build_key(r, fam, r.get(col, "") if col else "") for r in df.to_dict("records")]
            assert vec == sca, fam


def test_history_key_is_among_entry_candidates():
    """過去側で作るキーが、同じ条件の出馬表側の候補に必ず含まれる（全 family・帯スキーム）。"""
    for inner in ("", "内", "外"):
        f = {**FIELDS, "芝(内・外)": inner}
        cands = keys.entry_key_candidates(f)
        for fam in keys.ALL_FAMILIES:
            for s in (keys.SCHEMES if keys.is_band_family(fam) else ("",)):
                rb = hensachi.rank_band(int(f["順位"]), s) if s else ""
                hk = keys.build_key(f, fam, rb)
                assert (hk, s) in cands[fam], (fam, s, inner)


def test_unknown_inner_tries_all():
    cands = [k for k, _ in keys.entry_key_candidates(FIELDS, ["normal"])["normal"]]
    assert cands == ["東京×ダ×1600×定量×1・2_勝×4", "東京×ダ×1600×内×定量×1・2_勝×4",
                     "東京×ダ×1600×外×定量×1・2_勝×4", "東京×ダ×1600×内外×定量×1・2_勝×4"]


# ---------------------------------------------------------------- 名前・オッズ
def test_foragg_name_roundtrip():
    for fam, mode in itertools.product(keys.ALL_FAMILIES, odds.ODDS_MODES):
        for s in (keys.SCHEMES if keys.is_band_family(fam) else ("",)):
            fn = naming.agg_filename(fam, mode, s, "ローカル５場（札幌、函館、福島、新潟、小倉）_吉馬_先行力")
            p = naming.parse_foragg_stem(naming.file_stem(fn))
            assert (p["family"], p["mode"], p["scheme"], p["indicator"]) == (fam, mode, s, "吉馬_先行力")
            t = naming.table_name_for_foragg(fn)
            assert t.startswith("foragg_") and not t.startswith("foragg_foragg_")


def test_material_name_roundtrip():
    for fam in keys.ALL_FAMILIES:
        for s in (keys.SCHEMES if keys.is_band_family(fam) else ("",)):
            fn = naming.material_filename(fam, s, "札幌_先行")
            assert fn.endswith(".csv.gz")
            p = naming.parse_foragg_stem(naming.file_stem(fn))
            assert (p["family"], p["mode"], p["scheme"], p["indicator"], p["source"]) == (fam, "normal", s, "先行", "札幌_先行")


@pytest.mark.parametrize("stem", ["foragg_odds5p_normal_札幌_ZI", "foragg_normal_band_札幌_ZI",
                                  "foragg_rankband_s1_normal_札幌_ZI", "foragg_unknown_札幌_ZI", "processed_normal_x"])
def test_foragg_name_rejects(stem):
    assert naming.parse_foragg_stem(stem) is None


def test_entries_table_names():
    assert naming.table_name_for_entries("entries_merged_hensachi.csv") == "entries_merged_hensachi"
    assert naming.table_name_for_entries("shisu_20260531_1_hensachi.csv") == "entries_shisu_20260531_1_hensachi"
    assert naming.table_name_for_entries("all.csv") != naming.ENTRIES_ALL
    assert naming.xjoin_table_for("entries_merged_hensachi") == "entries_xjoin_merged_hensachi"


def test_odds_modes():
    o = pd.Series(["1.5", "9.9", "10.0", "49.9", "50.0", "", "abc"])
    assert odds.odds_mask(o, "normal").all()
    assert odds.odds_mask(o, "odds10u").tolist() == [True, True, False, False, False, False, False]
    assert odds.odds_mask(o, "odds10_50").tolist() == [False, False, True, True, False, False, False]
    assert odds.odds_mask(o, "odds50p").tolist() == [False, False, False, False, True, False, False]
    assert odds.odds_bucket(o).tolist() == ["odds10u", "odds10u", "odds10_50", "odds10_50", "odds50p", "", ""]
    assert "odds5p" not in odds.ODDS_MODES


# ---------------------------------------------------------------- 着順
def test_parse_finish():
    f = parse_finish(pd.Series(["1", "2", "3", "取消", "除外", "中止", "失格", "取", "4(降)", ""]),
                     pd.Series(["16"] * 10))
    assert f["denom"].tolist() == [True, True, True, False, False, True, True, False, True, True]
    assert f["finish_num"].tolist()[5:7] == [16.0, 16.0]
    assert f["finish_num"].tolist()[8] == 4.0
    assert f["place"].sum() == 3 and f["win"].sum() == 1


# ---------------------------------------------------------------- 分類
def test_classify_boundaries():
    base = {"cnt": 10, "fv": 0, "rv": 0, "top5": 0, "frc": 0, "tra": 0}
    assert C.classify({**base, "rv": 80, "frc": 100}, "normal") == "abs"
    assert C.classify({**base, "rv": 79.99, "frc": 100}, "normal") is None
    assert C.classify({**base, "rv": 80, "tra": 100}, "normal") == "abs"
    assert C.classify({**base, "fv": 80, "top5": 90}, "normal") == "axis"
    assert C.classify({**base, "fv": 80, "top5": 89.9}, "normal") is None
    assert C.classify({**base, "rv": 60, "top5": 90}, "normal") == "axis"
    assert C.classify({**base, "fv": 70, "top5": 80, "frc": 100}, "normal") == "axis"
    assert C.classify({**base, "rv": 50, "top5": 80, "frc": 100}, "normal") == "axis"
    assert C.classify({**base, "cnt": 9, "fv": 90, "top5": 95}, "normal") is None
    assert C.classify({**base, "cnt": 30, "fv": 60, "top5": 70}, "normal") == "opp"
    assert C.classify({**base, "cnt": 30, "rv": 50, "top5": 70}, "normal") == "opp"
    assert C.classify({**base, "cnt": 30, "fv": 33, "frc": 150, "top5": 50}, "normal") == "opp"
    assert C.classify({**base, "cnt": 29, "fv": 60, "top5": 70}, "normal") is None
    assert C.classify({**base, "cnt": 20, "fv": 4.9, "top5": 9.9}, "normal") == "keshi"
    assert C.classify({**base, "cnt": 20, "fv": 4.9, "top5": None}, "normal") is None
    assert C.classify({**base, "cnt": 20, "fv": 19.9, "top5": 50}, "odds10u") == "keshi"
    assert C.classify({**base, "cnt": 20, "fv": 19.9, "top5": 50}, "odds10_50") is None
    # 判定順: 消し > 絶対軸
    assert C.classify({"cnt": 30, "fv": 4, "rv": 90, "top5": 5, "frc": 200, "tra": 200}, "normal") == "keshi"


def test_classify_small_percent_not_rescaled():
    """旧版は 0〜1 の値を割合とみなして100倍し、掲示板率0.95%を95%と読んで消し候補から外していた。"""
    assert C.classify({"cnt": 50, "fv": 0.9, "rv": 0.9, "top5": 0.95, "frc": 0, "tra": 0}, "normal") == "keshi"


def test_sql_condition_matches_python():
    rng = random.Random(3)
    con = sqlite3.connect(":memory:")
    con.execute('CREATE TABLE t (id INTEGER, "件数" INTEGER, "複勝率_val" REAL, "連対率_val" REAL, '
                '"掲示板率_val" REAL, "複勝回収値" REAL, "単勝適正回収値" REAL)')
    rows = []
    pick = lambda vals: rng.choice(vals + [None])
    for i in range(4000):
        rows.append((i, pick([5, 9, 10, 19, 20, 29, 30, 60]), pick([0, 4.99, 5, 19.99, 33, 60, 70, 80, 95]),
                     pick([0, 49.9, 50, 60, 80, 90]), pick([0, 9.9, 10, 50, 70, 80, 90, 99]),
                     pick([0, 99, 100, 150, 200]), pick([0, 99, 100, 150])))
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?)", rows)
    for mode in odds.ODDS_MODES:
        expected = {c: set() for c in C.CATEGORIES}
        for r in rows:
            cat = C.classify({"cnt": r[1], "fv": r[2], "rv": r[3], "top5": r[4], "frc": r[5], "tra": r[6]}, mode)
            if cat:
                expected[cat].add(r[0])
        for cat in C.CATEGORIES:
            got = {x[0] for x in con.execute(f"SELECT id FROM t WHERE {C.sql_condition(cat, mode)}")}
            assert got == expected[cat], (mode, cat)


# ---------------------------------------------------------------- WEDGE
@pytest.mark.parametrize("o,th", [(1.0, 1), (1.4, 1), (1.5, 1), (2.9, 1), (3.0, 2), (4.9, 2), (5.0, 3), (9.9, 3),
                                  (10.0, 4), (19.9, 4), (20.0, 6), (49.9, 6), (50.0, 8), (100.0, 8), (350.0, 8)])
def test_edge_threshold(o, th):
    assert wedge.edge_threshold(o) == th


def test_edge_success_and_wedge():
    s = wedge.edge_success(pd.Series([1.4, 3.5, 3.5, 60, None]), pd.Series([1, 2, 3, 8, 1]))
    assert s.tolist()[:4] == [1, 1, 0, 1] and pd.isna(s.iloc[4])
    assert wedge.win_score(1, 1, 1, 2) == pytest.approx((3 + 1.5 + 1) / 6)
    df = pd.DataFrame({"finish_num": [1, 2, 9, 4], "単勝オッズ": [2.0, 4.0, 60.0, 15.0]})
    r = wedge.wedge_from_frame(df)
    assert r["WIN"] == pytest.approx((3 + 1.5) / 12) and r["EDGE"] == pytest.approx(3 / 4)
    assert r["WEDGE"] == pytest.approx(r["WIN"] + r["EDGE"])


def test_year_weights():
    assert wedge.year_weights(5).tolist() == [0.06, 0.11, 0.18, 0.25, 0.40]
    assert wedge.year_weights(4).tolist() == [0.12, 0.19, 0.27, 0.42]
    w3 = wedge.year_weights(3)
    assert w3.sum() == pytest.approx(1.0) and w3[0] < w3[1] < w3[2]
    assert len(wedge.year_weights(7)) == 5
    yearly = {2019: 9.0, 2020: 1.0, 2021: 1.0, 2022: 1.0, 2023: 1.0, 2024: 2.0}
    assert wedge.adjusted_wedge(yearly) == pytest.approx(0.06 + 0.11 + 0.18 + 0.25 + 0.8)   # 2019 は切り捨て
    assert wedge.adjusted_wedge({2023: 1.0, 2024: 2.0}, {2023: 3, 2024: 40}, min_year_count=30) == pytest.approx(2.0)


# ---------------------------------------------------------------- CSV 入出力
@pytest.mark.parametrize("name", ["a.csv", "a.csv.gz"])
def test_cp932_roundtrip(tmp_path, name):
    df = pd.DataFrame({"騎手": ["髙杉吏麒", "①"], "x": ["～", ""]})
    write_csv(df, tmp_path / name)
    back = read_csv_any(tmp_path / name)
    assert back["騎手"].tolist() == ["髙杉吏麒", "①"] and back["x"].tolist() == ["～", ""]
    if name.endswith(".gz"):
        assert (tmp_path / name).read_bytes()[:2] == b"\x1f\x8b"


def test_fullwidth_values_are_numbers():
    r = _ci(["８５", "－12.5", "　85", "100"], ["a"] * 4)
    assert r["偏差値"].notna().all()


def test_recomputed_rank_ties_share_rank():
    """順位列が無いときの再計算は同値同順位（TARGET の 指数順位 と同じ。サンプルの ZI・オメガ指数で確認済み）。"""
    r = _ci([30, 50, 30, 10], ["a"] * 4)
    assert r["順位"].tolist() == [2, 1, 2, 4]


# ---------------------------------------------------------------- WEDGE ベクトル版（Phase 3）
def test_edge_thresholds_vector_matches_scalar():
    vals = [None, "", "abc", 0, 0.9, 0.99, 1.0, 1.4, 1.45, 1.5, 2.9, 2.95, 3.0, 4.9, 5.0, 9.9, 10.0, 19.9, 20.0,
            49.9, 50.0, 99.9, 100.0, 100.1, 350.0, 9999.0, "12.3"]   # 素材のオッズは NFKC 済み
    vec = wedge.edge_thresholds(pd.Series(vals, dtype=object)).tolist()
    sca = [wedge.edge_threshold(v) for v in vals]
    assert [None if x != x else x for x in vec] == [None if x != x else x for x in sca]


def _random_material(seed, n=4000, keys=30, years=range(2017, 2026)):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "k": rng.integers(0, keys, n).astype(str),
        "finish_num": rng.integers(1, 19, n).astype(float),
        "単勝オッズ": np.round(rng.choice([1.0, 1.4, 1.5, 2.9, 3.0, 4.9, 5.0, 9.9, 10.0, 19.9, 20.0, 49.9, 50.0, 100.0,
                                       150.0, np.nan], n), 1),
        "y": rng.choice(list(years), n).astype(float),
    })
    df.loc[rng.random(n) < 0.03, "y"] = np.nan
    return df


@pytest.mark.parametrize("window", ["data", "key"])
@pytest.mark.parametrize("min_year", [0, 30])
def test_wedge_columns_match_reference(window, min_year):
    df = _random_material(1)
    ref_year = 2026 if window == "data" else None      # data 窓: 素材の最終年より後を指定しても動く
    out = wedge.wedge_columns(df["k"], df["finish_num"], df["単勝オッズ"], df["y"], ref_year, min_year, window)
    for k, g in df.groupby("k"):
        r = wedge.wedge_from_frame(g)
        assert out.loc[k, "WIN"] == pytest.approx(r["WIN"])
        assert out.loc[k, "EDGE"] == pytest.approx(r["EDGE"])
        assert out.loc[k, "WEDGE"] == pytest.approx(r["WEDGE"])
        gy = g[g["y"].notna()]
        yearly = {int(y): wedge.wedge_from_frame(x)["WEDGE"] for y, x in gy.groupby("y")}
        counts = {int(y): len(x) for y, x in gy.groupby("y")}
        end = ref_year if window == "data" else max(counts)
        adj = wedge.adjusted_wedge(yearly, counts, min_year, end)
        if adj != adj:
            assert pd.isna(out.loc[k, "adjusted_WEDGE"]) and out.loc[k, "adjusted_WEDGE_年数"] == 0
        else:
            assert out.loc[k, "adjusted_WEDGE"] == pytest.approx(adj)


def test_wedge_hand_example():
    """手計算の例。1キー、2021〜2025年の各年40件。"""
    rows = []
    for y in range(2020, 2026):
        for i in range(40):
            fin = 1 if i < 4 else (2 if i < 8 else (3 if i < 12 else 10))      # 1着4・2着4・3着4・着外28
            odds = 2.0 if i < 20 else 30.0                                    # 2倍→1着のみ成功、30倍→6着以内
            rows.append({"k": "A", "f": fin, "o": odds, "y": y})
    df = pd.DataFrame(rows)
    df.loc[(df["y"] == 2024) & (df.index % 40 >= 20), "y"] = np.nan             # 2024年は20件（<30）にする
    out = wedge.wedge_columns(df["k"], df["f"], df["o"], df["y"], ref_year=2025, min_year_count=30)
    # 年40件: WIN = (4*3 + 4*1.5 + 4*1) / (40*3) = 22/120
    #         EDGE = 2倍(20頭)の1着4頭 + 30倍(20頭)の6着以内0頭（i<20 が2倍）→ 4/40
    win_y, edge_y = 22 / 120, 4 / 40
    w_y = win_y + edge_y
    assert out.loc["A", "adjusted_WEDGE_年数"] == 4                      # 2021,2022,2023,2025（2020は窓外、2024は件数不足）
    assert out.loc["A", "adjusted_WEDGE_件数"] == 160
    assert out.loc["A", "adjusted_WEDGE_最終年"] == 2025
    assert out.loc["A", "adjusted_WEDGE"] == pytest.approx(w_y * (0.12 + 0.19 + 0.27 + 0.42))
    # 全期間（年が欠けた行も含む）: 240件。うち2024年の後半20頭（i>=20 → 着外10着・30倍）も含む
    assert out.loc["A", "WIN"] == pytest.approx((6 * 22) / (240 * 3))


def test_wedge_year_weighting_uses_spec_weights():
    """年ごとに WEDGE が違うとき、古い順にウェイトが掛かる。"""
    rows = []
    for j, y in enumerate(range(2019, 2026)):       # 7年分。直近5年だけ使う
        for i in range(30):
            rows.append({"k": "A", "f": 1 if i < j else 18, "o": 5.0, "y": y})   # 年ごとに1着数が増える
    df = pd.DataFrame(rows)
    out = wedge.wedge_columns(df["k"], df["f"], df["o"], df["y"], ref_year=2025, min_year_count=30)
    per_year = [(j * 3 / 90) + (j / 30) for j in range(2, 7)]                # 2021..2025
    assert out.loc["A", "adjusted_WEDGE"] == pytest.approx(np.dot([0.06, 0.11, 0.18, 0.25, 0.40], per_year))
    assert out.loc["A", "adjusted_WEDGE_年数"] == 5


@pytest.mark.parametrize("n,expected", [(1, [1.0]), (2, [0.1 / 0.6, 0.5 / 0.6]), (3, [0.1 / 0.9, 0.3 / 0.9, 0.5 / 0.9])])
def test_year_weights_short(n, expected):
    assert wedge.year_weights(n).tolist() == pytest.approx(expected)


def test_wedge_columns_edge_cases():
    # オッズ全欠損 → EDGE/WEDGE は空、WIN は出る
    out = wedge.wedge_columns(pd.Series(["a"] * 3), pd.Series([1, 2, 3]), pd.Series([None] * 3),
                              pd.Series([2024] * 3), min_year_count=0)
    assert out.loc["a", "WIN"] == pytest.approx((3 + 1.5 + 1) / 9)
    assert pd.isna(out.loc["a", "EDGE"]) and pd.isna(out.loc["a", "WEDGE"]) and pd.isna(out.loc["a", "adjusted_WEDGE"])
    # 年が全部無い → adjusted は空、年数0
    out = wedge.wedge_columns(pd.Series(["a"] * 3), pd.Series([1, 2, 3]), pd.Series([2.0] * 3),
                              pd.Series(["", "", ""]), min_year_count=0)
    assert out.loc["a", "adjusted_WEDGE_年数"] == 0 and pd.isna(out.loc["a", "adjusted_WEDGE_最終年"])
    with pytest.raises(ValueError):
        wedge.wedge_columns(pd.Series(["a"]), pd.Series([1]), pd.Series([2.0]), pd.Series([2024]), window="bad")
