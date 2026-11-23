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


# 期待値は TARGET の 重量種別（★6タブの過去CSV 202610.csv の264レースで推定と全件一致することを確認した形）
@pytest.mark.parametrize("name,age,cls,expected", [
    ("むらさＨ･3勝", "４歳以上", "準OP・OP", "ハンデ"), ("目黒記念ＨG2", "４歳以上", "重賞", "ハンデ"),
    ("安土城Ｈ(L)", "４歳以上", "準OP・OP", "ハンデ"), ("清里特Ｈ･2勝", "３歳以上", "1・2_勝", "ハンデ"),
    ("北陸ＳＨ･3勝", "３歳以上", "準OP・OP", "ハンデ"), ("古都ＳＨ･3勝", "３歳以上", "準OP・OP", "ハンデ"),
    ("福島民友ＣＨ", "３歳以上", "準OP・OP", "ハンデ"), ("NHKマイルC", "３歳", "G1", "馬齢"),
    ("HTB賞", "３歳以上", "1・2_勝", "定量"), ("ＳＨＩＮ賞", "３歳以上", "1・2_勝", "定量"),
    ("菊花賞G1", "３歳", "G1", "馬齢"), ("秋華賞G1", "３歳", "G1", "馬齢"),
    ("アイビー(L)", "２歳", "準OP・OP", "定量"), ("もみじＳ", "２歳", "準OP・OP", "定量"),
    ("未勝利", "３歳", "未勝利", "馬齢"), ("朝日杯FSG1", "２歳", "G1", "馬齢"), ("アルテミスG3", "２歳", "重賞", "馬齢"),
    ("１勝ｸﾗｽ", "２歳", "1・2_勝", "馬齢"), ("未勝利", "", "未勝利", ""),
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
    ("札幌_SAV", "札幌", "SAV"), ("吉馬_SP能力値", "", "吉馬_SP能力値"), ("東京_未知の指数", "東京", "未知の指数"),
])
def test_parse_source_name(stem, region, ind):
    assert indicators.parse_source_name(stem) == (region, ind)


def test_indicator_flags():
    assert not indicators.spec_for("マイニング値").reverse          # TARGET のマイニングは大きいほど良い（★6 で確認）
    assert indicators.spec_for("想定前半3F").reverse and indicators.spec_for("想定後半").zero_missing
    assert indicators.spec_for("ZI").zi and indicators.spec_for("ZI").exclude_shinba
    assert indicators.spec_for("吉馬_SP最大").exclude_shinba
    assert not indicators.spec_for("対戦M").reverse


# ---------------------------------------------------------------- 偏差値
def _ci(vals, races, name="SAV", shinba=None, rank=None):
    return hensachi.compute_indicator(pd.Series(vals, dtype=object), pd.Series(races), indicators.spec_for(name),
                                      None if shinba is None else pd.Series(shinba), rank)


def test_hensachi_basic_and_bands():
    r = _ci([10, 20, 30], ["a"] * 3)
    assert r["偏差値"].tolist() == [38.0, 50.0, 62.0]
    assert r["偏差値帯"].tolist() == ["40以下", "41~50", "61~65"]
    assert r["順位"].tolist() == [3, 2, 1]
    assert r["順位帯_s1"].tolist() == ["2~3位", "2~3位", "1位"]
    assert r["順位帯_s2"].tolist() == ["1~3位・1~5位"] * 3      # s2 は重なる帯（表示は「・」でつなぐ）


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
    r = _ci([30, 20, 10], ["a"] * 3, name="想定前半")        # 小さいほど良い（符号反転）
    assert r["順位"].tolist() == [3, 2, 1]
    r = _ci([30, 20, 10], ["a"] * 3, name="タイムM")          # TARGET のマイニングは大きいほど良い
    assert r["順位"].tolist() == [1, 2, 3]
    r = _ci([1, 2, 3], ["a"] * 3, name="吉馬_SP能力値", shinba=[True, True, True])
    assert r["shinba_excluded"].all() and r["偏差値"].isna().all()
    r = _ci([1, 2, 3], ["a"] * 3, name="SAV", shinba=[True, True, True])
    assert not r["shinba_excluded"].any()


def test_given_rank_preferred():
    r = _ci([10, 20, 30], ["a"] * 3, rank=pd.Series(["1", "2", "3"]))
    assert r["順位"].tolist() == [1, 2, 3]


@pytest.mark.parametrize("rank,s1,s2", [(1, "1位", "1~3位・1~5位"), (2, "2~3位", "1~3位・1~5位"),
                                        (3, "2~3位", "1~3位・1~5位"), (4, "4~5位", "1~5位"), (5, "4~5位", "1~5位"),
                                        (6, "6~9位", "6位~"), (9, "6~9位", "6位~"), (10, "10位~", "6位~"),
                                        (18, "10位~", "6位~"), (None, "", "")])
def test_rank_bands(rank, s1, s2):
    assert hensachi.rank_band(rank, "s1") == s1 and hensachi.rank_band(rank, "s2") == s2


def test_band_labels_kinds():
    """8区分（s1 の5帯・s2 の3帯）は 指数順位・前走着順・前走人気・前走上がり3F順 で共通。単位だけ違う。"""
    bl = hensachi.band_labels
    assert bl(2, "s1", "finish") == ["前走2~3着"] and bl("１", "s1", "pop") == ["前走1人気"]
    assert bl("1", "s2", "pop") == ["前走1~3人気", "前走1~5人気"]
    assert bl(7, "s2", "agari") == ["前走上り6位~"] and bl("12.0", "s1", "rank") == ["10位~"]
    assert bl("0", "s1") == [] and bl("abc", "s2") == [] and bl("", "s1") == [] and bl(None, "s1") == []
    assert hensachi.all_band_labels("s1") == ["1位", "2~3位", "4~5位", "6~9位", "10位~"]
    assert hensachi.all_band_labels("s2", "finish") == ["前走1~3着", "前走1~5着", "前走6着~"]
    # s1 は排他（1〜30 のどれも帯は1つ）、s2 は 1〜3 が2つ、4〜 が1つ
    assert all(len(bl(n, "s1")) == 1 for n in range(1, 31))
    assert [len(bl(n, "s2")) for n in range(1, 8)] == [2, 2, 2, 1, 1, 1, 1]


# ---------------------------------------------------------------- キー
SEP = "×"
FIELDS = {"場所": "東京", "芝・ダ": "ダ", "距離": "1600", "芝(内・外)": "", "重量種別": "定量", "クラス名": "1・2_勝",
          "枠番グループ": "内枠", "騎手": "髙杉吏麒", "順位": "4", "偏差値帯": "61~65", "前走着順": "2", "前走人気": "7",
          "前走上り3F順": "1", "時期": "3-5月", "間隔帯": "中1週", "前走脚質": "先行", "R値帯": "R2"}


def _keys(fields: dict, fam: str, scheme: str = "") -> list[str]:
    return keys.build_keys(pd.DataFrame([fields]), fam, scheme).tolist()


def test_build_key_examples():
    assert _keys(FIELDS, "normal") == ["東京×ダ×1600×定量×1・2_勝×4"]
    assert _keys({**FIELDS, "芝(内・外)": "外"}, "rankB") == ["東京×ダ×1600×外×内枠×4"]
    assert _keys(FIELDS, "normal_band", "s1") == ["東京×ダ×1600×定量×1・2_勝×4~5位"]
    assert _keys(FIELDS, "normal_band", "s2") == ["東京×ダ×1600×定量×1・2_勝×1~5位"]
    assert _keys({**FIELDS, "順位": "2"}, "normal_band", "s2") == ["東京×ダ×1600×定量×1・2_勝×1~3位",
                                                                  "東京×ダ×1600×定量×1・2_勝×1~5位"]
    assert _keys(FIELDS, "hensachiJ") == ["東京×ダ×1600×髙杉吏麒×61~65"]
    # 帯にする条件（前走着順など）は RBAND の family では tail と同じスキーム、偏差値帯の family では s1
    assert _keys(FIELDS, "D_prevfin", "s1") == ["東京×ダ×1600×前走2~3着×4~5位"]
    assert _keys(FIELDS, "D_prevfin", "s2") == ["東京×ダ×1600×前走1~3着×1~5位", "東京×ダ×1600×前走1~5着×1~5位"]
    # 偏差値帯の family でも、前走着順は s1・s2 の両方の帯で作る（スキームごとに別の集計表）
    assert _keys(FIELDS, "D_prevfin_hen", "s1") == ["東京×ダ×1600×前走2~3着×61~65"]
    assert _keys(FIELDS, "D_prevfin_hen", "s2") == ["東京×ダ×1600×前走1~3着×61~65", "東京×ダ×1600×前走1~5着×61~65"]
    assert keys.get("D_prevfin_hen").schemes == ("s1", "s2") and keys.get("hensachiA").schemes == ("",)
    assert _keys(FIELDS, "B_jk_prevagari", "s2") == ["髙杉吏麒×前走上り1~3位×1~5位", "髙杉吏麒×前走上り1~5位×1~5位"]
    assert _keys(FIELDS, "C_sd_dist_r_hen") == []                          # 距離帯が空 → 作らない
    assert _keys({**FIELDS, "騎手": ""}, "rankJ") == []                    # 部品が空ならキーを作らない
    assert _keys({**FIELDS, "距離": ""}, "normal") == []
    assert _keys({**FIELDS, "前走着順": "取消"}, "D_prevfin", "s1") == []  # 数でない着順は帯にならない
    with pytest.raises(ValueError):
        keys.build_keys(pd.DataFrame([FIELDS]), "normal_band", "")


def test_family_registry_counts():
    from shisu import families as F
    fams = F.default_families()
    assert len(F.DEFAULT_TEMPLATES) == 49
    assert {ly: sum(1 for t in F.DEFAULT_TEMPLATES if t[0] == ly) for ly in "ABCD"} == {"A": 15, "B": 9, "C": 10, "D": 15}
    assert len(fams) == 15 + 2 * 34 == 83
    assert sum(len(f.schemes) for f in fams) == 129          # 122 ＋ 前走着順・人気・上り3F順を含む偏差値帯の7種のs2
    assert len({f.name for f in fams}) == 83
    # 初期の49種は、末尾が 新順位帯（s1/s2）＋偏差値帯（B〜D）
    for f in fams:
        if f.layer != "A":
            assert f.tail in ("RBAND", "HBAND") and f.name.endswith("_hen") == (f.tail == "HBAND")


def _random_rows(n: int, seed: int) -> pd.DataFrame:
    from shisu.conditions import BANDED_CONDITIONS, CONDITION_COLUMNS
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        r = {"場所": rng.choice(["東京", "京都", "東京", ""]), "芝・ダ": rng.choice(["芝", "ダ", ""]),
             "距離": rng.choice(["1600", "16-18", ""]), "芝(内・外)": rng.choice(["", "", "内", "外", "内外"]),
             "重量種別": rng.choice(["定量", "ハンデ", ""]), "クラス名": rng.choice(["1・2_勝", "G1", ""]),
             "枠番グループ": rng.choice(["内枠", "外枠", ""]), "騎手": rng.choice(["髙杉吏麒", "C.ルメール", ""]),
             "順位": rng.choice([str(rng.randint(1, 18)), ""]),
             "偏差値帯": rng.choice(["71以上", "51~60", "40以下", ""])}
        for c in CONDITION_COLUMNS:
            if c in BANDED_CONDITIONS:
                r[c] = rng.choice([str(rng.randint(1, 18)), "", "0"])
            else:
                r[c] = rng.choice([f"{c}A", f"{c}B", ""])
        rows.append(r)
    return pd.DataFrame(rows)


def test_history_keys_equal_entry_keys():
    """過去側（ベクトル版 build_keys）と出馬表側（1件版 entry_keys）が、全 129 通りで同じキーを作る。
    出馬表側は 芝(内・外) の候補を余分に試すので、過去側のキーは必ずその中にあり、内外を合わせれば完全に一致する。"""
    from shisu import families as F
    df = _random_rows(400, 0)
    recs = df.to_dict("records")
    n_keys = 0
    for fam in F.default_families():
        for sc in fam.schemes:
            vec = F.build_keys(df, fam, sc)
            by_row = {i: set(g) for i, g in vec.groupby(level=0)} if len(vec) else {}
            for i, r in enumerate(recs):
                ent = F.entry_keys(r, fam, sc)
                hist = by_row.get(i, set())
                assert hist <= set(ent), (fam.name, sc, r)
                if F.COURSE in fam.parts:
                    head = SEP.join(r[c] for c in ("場所", "芝・ダ", "距離"))
                    exact = head + (SEP + r["芝(内・外)"] if r["芝(内・外)"] else "")
                    if r["芝(内・外)"]:
                        ent = [k for k in ent if k.startswith(exact + SEP)]
                    else:
                        ent = [k for k in ent if k.split(SEP)[3] not in ("内", "外", "内外")]
                assert hist == set(ent), (fam.name, sc, r)
                n_keys += len(hist)
    assert n_keys > 5000


def test_history_key_is_among_entry_candidates():
    """entry_key_candidates（照合で使う窓口）にも、過去側のキーと帯スキームの組が必ず入っている。"""
    from shisu import families as F
    for inner in ("", "内", "外"):
        f = {**FIELDS, "芝(内・外)": inner, "距離帯": "1600-1800", "直線帯": "長", "坂": "あり"}
        cands = keys.entry_key_candidates(f)
        for fam in F.default_families():
            for s in fam.schemes:
                for hk in _keys(f, fam.name, s):
                    assert (hk, s) in cands[fam.name], (fam.name, s, inner)


def test_unknown_inner_tries_all():
    cands = [k for k, _ in keys.entry_key_candidates(FIELDS, ["normal"])["normal"]]
    assert cands == ["東京×ダ×1600×定量×1・2_勝×4", "東京×ダ×1600×内×定量×1・2_勝×4",
                     "東京×ダ×1600×外×定量×1・2_勝×4", "東京×ダ×1600×内外×定量×1・2_勝×4"]


# ---------------------------------------------------------------- 名前・オッズ
def test_foragg_name_roundtrip():
    for name, mode in itertools.product(keys.all_families(), odds.ODDS_MODES):
        for s in keys.get(name).schemes:
            fn = naming.agg_filename(name, mode, s, "ローカル５場（札幌、函館、福島、新潟、小倉）_吉馬_先行力")
            p = naming.parse_foragg_stem(naming.file_stem(fn))
            assert (p["family"], p["mode"], p["scheme"], p["indicator"]) == (name, mode, s, "吉馬_先行力")
            t = naming.table_name_for_foragg(fn)
            assert t.startswith("foragg_") and not t.startswith("foragg_foragg_")


def test_family_name_prefixes_do_not_collide():
    """family 名が別の family 名の前方一致になっていても（D_prevfin と D_prevfin_hen）、ファイル名から正しく引ける。"""
    for a in keys.all_families():
        fn = naming.agg_filename(a, "normal", keys.get(a).schemes[0], "札幌_SAV")
        assert naming.parse_foragg_stem(naming.file_stem(fn))["family"] == a


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
    """odds10u は 2.0倍以上 10.0倍未満（2026-11-14〜。1.9倍以下は normal にだけ入る。モード名はそのまま）。"""
    o = pd.Series(["1.5", "9.9", "10.0", "49.9", "50.0", "", "abc", "1.9", "2.0", "2", "１.９", "２．０", "1.99", "0", "-3"])
    assert odds.odds_mask(o, "normal").all()
    assert odds.odds_mask(o, "odds10u").tolist() == [False, True, False, False, False, False, False,
                                                     False, True, True, False, False, False, False, False]
    assert odds.odds_mask(o, "odds10_50").tolist() == [False, False, True, True] + [False] * 11
    assert odds.odds_mask(o, "odds50p").tolist() == [False, False, False, False, True] + [False] * 10
    assert odds.odds_bucket(o).tolist() == ["", "odds10u", "odds10_50", "odds10_50", "odds50p", "", "",
                                            "", "odds10u", "odds10u", "", "", "", "", ""]
    # 1頭は normal 以外では高々1つの帯に入り、帯の分け方は odds_bucket と同じ
    for m in ("odds10u", "odds10_50", "odds50p"):
        assert odds.odds_mask(o, m).tolist() == (odds.odds_bucket(o) == m).tolist()
    num = pd.to_numeric(o, errors="coerce")
    assert odds.odds_mask_numeric(num, "odds10u").tolist() == odds.odds_mask(o, "odds10u").tolist()
    assert odds.ODDS_BREAKS == (2.0, 10.0, 50.0) and odds.ODDS_LABELS["odds10u"] == "2〜10倍未満" == odds.FAV_LABEL
    assert odds.ODDS_MODES == ("normal", "odds10u", "odds10_50", "odds50p")      # モード名は互換のため変えない
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
    # 軸候補は 件数 20 から（2026-11-14〜。それまで 10）。絶対軸は 10 のまま
    ax = {**base, "cnt": 20}
    assert C.AX_CNT == 20 == C.KS_CNT and C.ABS_CNT == 10
    assert C.classify({**ax, "fv": 80, "top5": 90}, "normal") == "axis"
    assert C.classify({**ax, "fv": 80, "top5": 89.9}, "normal") is None
    assert C.classify({**ax, "rv": 60, "top5": 90}, "normal") == "axis"
    assert C.classify({**ax, "fv": 70, "top5": 80, "frc": 100}, "normal") == "axis"
    assert C.classify({**ax, "rv": 50, "top5": 80, "frc": 100}, "normal") == "axis"
    for cnt in (9, 10, 15, 19, 19.9):                                        # 4つの条件とも、20件未満は軸にしない
        for cond in ({"fv": 90, "top5": 95}, {"rv": 60, "top5": 90}, {"fv": 70, "top5": 80, "frc": 100},
                     {"rv": 50, "top5": 80, "frc": 100}):
            for mode in odds.ODDS_MODES:
                assert C.classify({**base, "cnt": cnt, **cond}, mode) is None, (cnt, cond, mode)
    # 10〜19件でも絶対軸の条件（連対率 80・回収値 100）を満たせば、今までどおり絶対軸
    assert C.classify({**base, "cnt": 10, "rv": 80, "fv": 90, "top5": 95, "frc": 100}, "normal") == "abs"
    assert C.classify({**base, "cnt": 19, "rv": 80, "fv": 90, "top5": 95, "tra": 100}, "odds10u") == "abs"
    assert C.classify({**base, "cnt": 19, "rv": 79.9, "fv": 90, "top5": 95, "frc": 100}, "normal") is None
    assert C.classify({**base, "cnt": 30, "fv": 60, "top5": 70}, "normal") == "opp"
    assert C.classify({**base, "cnt": 30, "rv": 50, "top5": 70}, "normal") == "opp"
    assert C.classify({**base, "cnt": 30, "fv": 33, "frc": 150, "top5": 50}, "normal") == "opp"
    assert C.classify({**base, "cnt": 29, "fv": 60, "top5": 70}, "normal") is None
    assert C.classify({**base, "cnt": 20, "fv": 4.9, "top5": 9.9}, "normal") == "keshi"
    assert C.classify({**base, "cnt": 20, "fv": 4.9, "top5": None}, "normal") is None
    # 2〜10倍未満専用の消し（2026-11-23〜）: 複勝率 < 20 かつ（掲示板率 < 35 または WEDGE < 0.350）
    assert C.classify({**base, "cnt": 20, "fv": 19.9, "top5": 34.9}, "odds10u") == "keshi"
    assert C.classify({**base, "cnt": 20, "fv": 19.9, "top5": 35}, "odds10u") is None          # 掲示板率 35 以上・WEDGE なし
    assert C.classify({**base, "cnt": 20, "fv": 19.9, "top5": 50, "wedge": 0.3499}, "odds10u") == "keshi"
    assert C.classify({**base, "cnt": 20, "fv": 19.9, "top5": 50, "wedge": 0.35}, "odds10u") is None
    assert C.classify({**base, "cnt": 20, "fv": 20, "top5": 0, "wedge": 0}, "odds10u") is None
    assert C.classify({**base, "cnt": 19, "fv": 10, "top5": 20, "wedge": 0.1}, "odds10u") is None
    for mode in ("normal", "odds10_50", "odds50p"):
        assert C.classify({**base, "cnt": 20, "fv": 19.9, "top5": 20, "wedge": 0.1}, mode) is None
    # 判定順: 消し > 絶対軸
    assert C.classify({"cnt": 30, "fv": 4, "rv": 90, "top5": 5, "frc": 200, "tra": 200}, "normal") == "keshi"


def test_abs_has_two_conditions_and_no_rare_relaxation():
    """絶対軸（2026-11-23〜）= ① 件数 ≥ 10・連対率 ≥ 80 ／ ② 件数 ≥ 20・勝率 ≥ 40・複勝率 ≥ 80。どちらも
    複勝回収値 ≥ 100 または 単勝適正回収値 ≥ 100 が要る。希少条件でも件数は下げない。"""
    z = {"cnt": 20, "fv": 0, "rv": 0, "wv": 0, "top5": 0, "frc": 0, "tra": 0}
    assert (C.ABS_CNT, C.ABS_REN, C.ABS2_CNT, C.ABS2_WIN, C.ABS2_FUKU) == (10, 80.0, 20, 40.0, 80.0)
    assert not hasattr(C, "RARE_ABS_CNT")
    two = {**z, "wv": 40, "fv": 80, "frc": 100}
    for mode in odds.ODDS_MODES:
        assert C.classify(two, mode) == "abs", mode
        assert C.classify({**two, "frc": 0, "tra": 100}, mode) == "abs"
        for miss in ({"cnt": 19}, {"cnt": 19.9}, {"wv": 39.99}, {"wv": None}, {"fv": 79.99}, {"fv": None}, {"frc": 99.99},
                     {"frc": None}, {"cnt": None}):
            assert C.classify({**two, **miss}, mode) is None, (mode, miss)
    assert C.classify({k: v for k, v in two.items() if k != "wv"}, "normal") is None       # 勝率を渡さない rec は ② を満たさない
    assert C.classify({**two, "wv": float("nan")}, "normal") is None and C.classify({**two, "wv": "x"}, "normal") is None
    # ② を満たさなければ、今までの区分（複勝率 80・掲示板率 90 なら軸候補）。満たせば絶対軸が先
    assert C.classify({**two, "wv": 39.9, "top5": 90}, "normal") == "axis"
    assert C.classify({**two, "top5": 90}, "normal") == "abs"
    # ① は 10〜19件でも（勝率・複勝率に関係なく）。② だけの行は 20件から
    assert C.classify({**z, "cnt": 10, "rv": 80, "tra": 100}, "normal") == "abs"
    assert C.classify({**z, "cnt": 10, "rv": 79.9, "wv": 90, "fv": 90, "tra": 100}, "normal") is None
    # 希少条件でも件数は下げない: 6〜9件は絶対軸にならない（軸候補の条件を満たせば軸候補。希少条件の軸候補は 6件から）
    for key in ("東京×芝×23-24×定量×G1×3", "中山×芝×2000×内×ハンデ×重賞×1~3位", "準OP・OP×61~65", "芝×芝長×高×1位",
                "ダ×ダ長×前走1~3着×1~5位"):
        assert C.is_rare_key(key)
        for mode in odds.ODDS_MODES:
            for cnt in (6, 7, 9, 9.9):
                assert C.classify({**z, "cnt": cnt, "rv": 95, "wv": 60, "fv": 79, "top5": 79, "frc": 300, "tra": 300, "key": key},
                                  mode) is None, (key, mode, cnt)
                assert C.classify({**z, "cnt": cnt, "rv": 95, "fv": 95, "top5": 95, "frc": 300, "key": key}, mode) == "axis"
            assert C.classify({**z, "cnt": 10, "rv": 80, "frc": 100, "key": key}, mode) == "abs"
            for cnt in (10, 15, 19):                                                           # ② は希少条件でも 20件から
                assert C.classify({**z, "cnt": cnt, "wv": 60, "fv": 79.9, "rv": 79, "top5": 69, "frc": 99, "tra": 110, "key": key},
                                  mode) is None, (key, mode, cnt)
                assert C.classify({**z, "cnt": cnt, "wv": 60, "fv": 85, "rv": 79, "top5": 69, "frc": 99, "tra": 110, "key": key},
                                  mode) is None, (key, mode, cnt)
            assert C.classify({**z, "cnt": 20, "wv": 60, "fv": 85, "rv": 79, "top5": 69, "frc": 99, "tra": 110, "key": key},
                              mode) == "abs"
    # 条件特化ヒット（末尾が下位）にも同じ基準
    low = "川田将雅×前走1~3着×6位~"
    assert C.low_tail_axis_category({**two, "key": low}, "normal") == "abs" and C.classify({**two, "key": low}, "normal") is None
    assert C.low_tail_axis_category({**two, "cnt": 19, "key": low}, "normal") is None
    assert C.low_tail_axis_category({**z, "cnt": 6, "rv": 85, "frc": 120, "key": "東京×芝×23-24×定量×G1×7"}, "normal") is None
    assert C.low_tail_axis_category({**z, "cnt": 10, "rv": 85, "frc": 120, "key": "東京×芝×23-24×定量×G1×7"}, "normal") == "abs"
    # 説明の文と、設定のハッシュに入る値
    d = C.description_lines()["abs"]
    assert d[0].startswith("件数 ≥ 10 かつ 連対率 ≥ 80%") and d[1].startswith("件数 ≥ 20 かつ 勝率 ≥ 40% かつ 複勝率 ≥ 80%")
    assert all("複勝回収値 ≥ 100 または 単勝適正回収値 ≥ 100" in x for x in d[:2]) and any("件数は下げない" in x for x in d)
    st = C.rule_settings()
    assert (st["ABS2_CNT"], st["ABS2_WIN"], st["ABS2_FUKU"]) == (20, 40.0, 80.0) and "RARE_ABS_CNT" not in st


def test_keshi_10u_needs_low_top5_or_low_wedge():
    """2〜10倍未満専用の消し（2026-11-23〜）: 件数 ≥ 20（希少 12）かつ 複勝率 < 20 かつ（掲示板率 < 35 または WEDGE < 0.350）。
    WEDGE が無い（None・NaN・数でない）なら「WEDGE < 0.350」は満たさない。全モード共通の規則はそのまま。"""
    assert (C.KS_PLC_MAX_10U, C.KS_TOP5_MAX_10U, C.KS_WEDGE_MAX_10U) == (20.0, 35.0, 0.35)
    z = {"cnt": 20, "fv": 15, "rv": 0, "wv": 0, "top5": 50, "frc": 0, "tra": 0}
    table = [({"top5": 34.99}, True), ({"top5": 35}, False), ({"top5": 34.99, "wedge": 0.9}, True),
             ({"wedge": 0.3499}, True), ({"wedge": 0.35}, False), ({"wedge": 0}, True), ({"wedge": None}, False),
             ({"wedge": float("nan")}, False), ({"wedge": "x"}, False), ({"wedge": "0.2"}, True), ({"top5": None}, False),
             ({"top5": None, "wedge": 0.2}, True), ({"top5": 20, "wedge": None}, True), ({"fv": 20, "top5": 0, "wedge": 0}, False),
             ({"fv": None, "top5": 0, "wedge": 0}, False), ({"cnt": 19, "top5": 0, "wedge": 0}, False),
             ({"cnt": None, "top5": 0, "wedge": 0}, False)]
    for vals, want in table:
        rec = {**z, **vals}
        assert (C.classify(rec, "odds10u") == "keshi") == want, vals
        assert C.keshi_rule(rec, "odds10u") == ("fav" if want else None), vals
        for mode in ("normal", "odds10_50", "odds50p"):
            assert C.classify(rec, mode) is None and C.keshi_rule(rec, mode) is None, (vals, mode)
    # 希少条件は 12件から
    g1 = "東京×芝×2400×G1×6位~"
    assert C.classify({**z, "cnt": 12, "top5": 30, "key": g1}, "odds10u") == "keshi"
    assert C.classify({**z, "cnt": 12, "wedge": 0.3, "key": g1}, "odds10u") == "keshi"
    assert C.classify({**z, "cnt": 11, "wedge": 0.3, "key": g1}, "odds10u") is None
    assert C.classify({**z, "cnt": 12, "key": g1}, "odds10u") is None
    # 消しにならなくなった行は、ほかの区分の判定に進む（複勝率は低いが回収値の高い相手候補の条件 など）
    assert C.classify({**z, "cnt": 30, "fv": 19.9, "rv": 85, "top5": 60, "frc": 120, "wedge": 0.8}, "odds10u") == "abs"
    assert C.classify({**z, "cnt": 30, "fv": 19.9, "rv": 85, "top5": 60, "frc": 120, "wedge": 0.3}, "odds10u") == "keshi"
    # 全モード共通の規則は WEDGE に関係なく
    for mode in odds.ODDS_MODES:
        assert C.classify({**z, "fv": 4.9, "top5": 9.9, "wedge": 2.0}, mode) == "keshi"
        assert C.keshi_rule({**z, "fv": 4.9, "top5": 9.9, "wedge": 2.0}, mode) == "any"
    d = C.description_lines()["keshi"]
    assert any("複勝率 < 20% かつ（掲示板率 < 35% または WEDGE < 0.350）" in x and "2〜10倍未満" in x for x in d)
    st = C.rule_settings()
    assert (st["KS_PLC_MAX_10U"], st["KS_TOP5_MAX_10U"], st["KS_WEDGE_MAX_10U"]) == (20.0, 35.0, 0.35)


def test_record_passes_every_field_the_rules_read():
    """classify.record: DB・成績表の行（列名）→ rec。FIELD_COLUMNS の全部（勝率_val・WEDGE も）と key・indicator を渡す。"""
    assert C.FIELD_COLUMNS == {"cnt": "件数", "fv": "複勝率_val", "rv": "連対率_val", "wv": "勝率_val", "top5": "掲示板率_val",
                               "frc": "複勝回収値", "tra": "単勝適正回収値", "wedge": "WEDGE"}
    row = {"件数": 25, "複勝率_val": 81.0, "連対率_val": 55.0, "勝率_val": 41.0, "掲示板率_val": 85.0, "複勝回収値": 90.0,
           "単勝適正回収値": 105.0, "WEDGE": 0.9, "結合データ": "a×1位", "indicator": "SAV", "ほか": 1}
    rec = C.record(row)
    assert rec == {"cnt": 25, "fv": 81.0, "rv": 55.0, "wv": 41.0, "top5": 85.0, "frc": 90.0, "tra": 105.0, "wedge": 0.9,
                   "key": "a×1位", "indicator": "SAV"}
    assert C.classify(rec, "normal") == "abs"                                       # 勝率_val が渡っていなければ None
    assert C.record({**row, "key": "b×6位~"})["key"] == "b×6位~" and C.record(row, key="c×1")["key"] == "c×1"
    assert C.record(row, indicator="条件のみ")["indicator"] == "条件のみ" and C.record({})["key"] is None
    ks = {"件数": 30, "複勝率_val": 15.0, "掲示板率_val": 60.0, "WEDGE": 0.2}
    assert C.classify(C.record(ks), "odds10u") == "keshi" == C.classify(C.record({**ks, "WEDGE": None, "掲示板率_val": 30.0}), "odds10u")
    assert C.classify(C.record({**ks, "WEDGE": None}), "odds10u") is None          # WEDGE が渡っていなければ消しにならない


# ---------------------------------------------------------------- 指数の上限ゲート（2026-11-16）
LOW_TAILS = ["6~9位", "10位~", "6位~", "41~50", "40以下", "6", "7", "10", "18", "06", "0010", "99999999999999999999999"]
OK_TAILS = ["1位", "2~3位", "4~5位", "1~3位", "1~5位", "71以上", "66~70", "61~65", "51~60", "1", "2", "3", "4", "5", "05"]
# どの形でもない末尾（ゲートで落とさない）: 0・空・全角の数字・前後に空白や改行・似た語・前走の帯
ODD_TAILS = ["", "0", "00", "７", "１０位~", "6 ", " 6", "6\n", "6位", "6~9", "x6", "6.0", "-6", "前走6着~", "前走6~9着", "G1",
             "10位", "40以上", "6~9位 "]


def test_tail_tokens_cover_every_label_the_keys_can_end_with():
    """上位・下位の部品は、キーの末尾に出る順位帯（s1・s2）・偏差値帯の名前と同じ文字（表記がずれたらゲートが効かない）。"""
    rank = {lab for sc in ("s1", "s2") for n in range(1, 19) for lab in hensachi.band_labels(n, sc, "rank")}
    hen = {hensachi.hensachi_band(v) for v in range(0, 101)} - {""}
    assert rank == {"1位", "2~3位", "4~5位", "6~9位", "10位~", "1~3位", "1~5位", "6位~"}
    assert hen == {"71以上", "66~70", "61~65", "51~60", "41~50", "40以下"}
    assert rank | hen == set(C.TAIL_OK_TOKENS) | set(C.TAIL_LOW_TOKENS) and not set(C.TAIL_OK_TOKENS) & set(C.TAIL_LOW_TOKENS)
    assert set(C.TAIL_LOW_TOKENS) == {"6~9位", "10位~", "6位~", "41~50", "40以下"} and C.TAIL_LOW_RANK_MIN == 6
    assert C.TAIL_GATED == ("abs", "axis", "opp")
    for t in LOW_TAILS:
        assert C.is_low_tail("東京×ダ×1600×" + t) and C.is_low_tail(t), t
    for t in OK_TAILS + ODD_TAILS:
        assert not C.is_low_tail("東京×ダ×1600×" + t) and not C.is_low_tail(t), t
    assert not C.is_low_tail(None) and not C.is_low_tail("") and not C.is_low_tail("6~9位×")      # 末尾が空
    assert C.is_low_tail(7) and not C.is_low_tail(3) and C.tail_token("a×b×1~3位") == "1~3位" and C.tail_token(None) == ""


def test_low_index_conditions_are_never_abs_axis_or_opp():
    """指数が下位（結合データの末尾が下位の順位帯・偏差値帯・指数順位 6 以上）の条件は、絶対軸・軸候補・相手候補にしない。
    消し候補はそのまま。結合データが無い・末尾がどの形でもないときは今までどおり。"""
    z = {"cnt": 60, "fv": 30, "rv": 0, "top5": 40, "frc": 0, "tra": 0}          # どの区分にもならない成績
    cases = {"abs": {"rv": 85, "frc": 120}, "abs_2": {"wv": 45, "fv": 82, "tra": 110},
             "axis": {"fv": 82, "top5": 92}, "opp": {"fv": 62, "top5": 72},
             "axis_hr": {"fv": 36, "top5": 55, "tra": 210}}
    head = "川田将雅×前走1~3着"
    for name, vals in cases.items():
        want = name.split("_")[0]
        mode = "odds10_50" if name == "axis_hr" else "normal"
        for t in OK_TAILS + ODD_TAILS:
            for key in (f"{head}×{t}", t):
                assert C.classify({**z, **vals, "key": key}, mode) == want, (name, key)
        for t in LOW_TAILS:
            for key in (f"{head}×{t}", t):
                assert C.classify({**z, **vals, "key": key}, mode) is None, (name, key)
                assert C.classify({**z, **vals, C.KEY_COLUMN: key}, mode) is None            # 結合データ の名前でも
        for key in (None, ""):                                                                 # 結合データなし → 落とさない
            assert C.classify({**z, **vals, "key": key}, mode) == want
        assert C.classify({**z, **vals}, mode) == want
    # 4つのオッズ帯とも同じ。希少条件（最低件数の緩和）でも、指数が下位なら対象外
    for mode in odds.ODDS_MODES:
        assert C.classify({**z, "rv": 85, "frc": 120, "key": head + "×6位~"}, mode) is None
        assert C.classify({**z, "cnt": 10, "rv": 85, "frc": 120, "key": "東京×芝×23-24×定量×G1×7"}, mode) is None
        assert C.classify({**z, "cnt": 10, "rv": 85, "frc": 120, "key": "東京×芝×23-24×定量×G1×5"}, mode) == "abs"
        assert C.classify({**z, "cnt": 6, "fv": 82, "top5": 92, "key": "東京×芝×23-24×定量×G1×7"}, mode) is None
        assert C.classify({**z, "cnt": 6, "fv": 82, "top5": 92, "key": "東京×芝×23-24×定量×G1×5"}, mode) == "axis"
    # 消し候補は制限しない（下位の条件こそ消しになる）。消しでも軸でもある値は、今までどおり消し
    for t in LOW_TAILS + OK_TAILS + ODD_TAILS:
        assert C.classify({**z, "fv": 3, "top5": 8, "key": f"{head}×{t}"}, "normal") == "keshi", t
        assert C.classify({**z, "fv": 15, "top5": 30, "key": f"{head}×{t}"}, "odds10u") == "keshi", t
        assert C.classify({**z, "fv": 15, "top5": 60, "wedge": 0.3, "key": f"{head}×{t}"}, "odds10u") == "keshi", t
    assert C.classify({**z, "fv": 4, "rv": 90, "top5": 5, "frc": 200, "key": head + "×10位~"}, "normal") == "keshi"
    # 途中の部品が下位の形でも、見るのは末尾だけ
    assert C.classify({**z, "fv": 82, "top5": 92, "key": "6位~×41~50×前走6着~×1~3位"}, "normal") == "axis"
    assert C.classify({**z, "fv": 82, "top5": 92, "key": "1~3位×71以上×6位~"}, "normal") is None
    # 説明の文と、設定のハッシュに入る値
    d = C.description_lines()
    for c in C.TAIL_GATED:
        assert any("指数が下位の条件" in x and "6~9位・10位~・6位~・41~50・40以下" in x and "指数順位 6 位以下" in x for x in d[c]), c
    assert not any("指数が下位の条件" in x for x in d["keshi"]) and any("絞らない" in x for x in d["keshi"])
    st = C.rule_settings()
    assert st["TAIL_LOW_TOKENS"] == list(C.TAIL_LOW_TOKENS) and st["TAIL_LOW_RANK_MIN"] == 6 and st["TAIL_GATED"] == ["abs", "axis", "opp"]


def test_low_tail_axis_category_reports_the_rows_the_gate_removed():
    """low_tail_axis_category: 末尾が下位のために区分なしになったが、数の基準は絶対軸・軸候補を満たす行の、もとの区分。
    classify（ゲートつき）の仕様は変えない。"""
    z = {"cnt": 60, "fv": 30, "rv": 0, "top5": 40, "frc": 0, "tra": 0}
    head = "川田将雅×前走1~3着"
    cases = {"abs": ({"rv": 85, "frc": 120}, "normal", "abs"), "abs_tra": ({"rv": 85, "tra": 100}, "odds50p", "abs"),
             "axis": ({"fv": 82, "top5": 92}, "normal", "axis"), "axis_hr": ({"fv": 36, "top5": 55, "tra": 210}, "odds10_50", "axis"),
             "hr_in_normal": ({"fv": 36, "top5": 55, "tra": 210}, "normal", None),        # 高回収率の基準は 2〜10倍未満・10〜50倍だけ
             "opp": ({"fv": 62, "top5": 72}, "normal", None),                              # 相手候補の基準までの行は対象外
             "none": ({}, "normal", None),
             "keshi": ({"fv": 3, "top5": 8}, "normal", None),                              # 消し候補は末尾に関係なく先に決まる
             "abs_2": ({"wv": 45, "fv": 82, "tra": 100}, "normal", "abs"),                 # 絶対軸の条件②（勝率 40・複勝率 80）
             "keshi_fav": ({"fv": 15, "rv": 90, "top5": 30, "frc": 150}, "odds10u", None),
             "keshi_fav_wedge": ({"fv": 15, "rv": 90, "top5": 60, "frc": 150, "wedge": 0.3}, "odds10u", None),
             "not_keshi_fav": ({"fv": 15, "rv": 90, "top5": 60, "frc": 150, "wedge": 0.5}, "odds10u", "abs"),   # 消しでなくなった行
             "keshi_and_abs": ({"fv": 4, "rv": 90, "top5": 5, "frc": 200}, "normal", None)}
    for name, (vals, mode, want) in cases.items():
        for t in LOW_TAILS:
            for key in (f"{head}×{t}", t):
                rec = {**z, **vals, "key": key}
                assert C.low_tail_axis_category(rec, mode) == want, (name, key)
                assert C.low_tail_axis_category({**z, **vals, C.KEY_COLUMN: key}, mode) == want          # 結合データ の名前でも
                assert C.classify(rec, mode) in (None, "keshi")                                         # 通常の区分は今までどおり
        for key in [f"{head}×{t}" for t in OK_TAILS + ODD_TAILS] + [None, "", head]:                   # 末尾が下位でなければ None
            assert C.low_tail_axis_category({**z, **vals, "key": key}, mode) is None, (name, key)
        assert C.low_tail_axis_category({**z, **vals}, mode) is None
    # 件数の下限・希少条件の緩和は classify と同じ
    assert C.low_tail_axis_category({**z, "cnt": 9, "rv": 85, "frc": 120, "key": head + "×6位~"}, "normal") is None
    assert C.low_tail_axis_category({**z, "cnt": 10, "rv": 85, "frc": 120, "key": head + "×6位~"}, "normal") == "abs"
    assert C.low_tail_axis_category({**z, "cnt": 19, "fv": 82, "top5": 92, "key": head + "×6位~"}, "normal") is None
    assert C.low_tail_axis_category({**z, "cnt": 20, "fv": 82, "top5": 92, "key": head + "×6位~"}, "normal") == "axis"
    assert C.low_tail_axis_category({**z, "cnt": 9, "rv": 85, "frc": 120, "key": "東京×芝×23-24×定量×G1×7"}, "normal") is None   # 絶対軸は希少条件でも 10件から
    assert C.low_tail_axis_category({**z, "cnt": 10, "rv": 85, "frc": 120, "key": "東京×芝×23-24×定量×G1×7"}, "normal") == "abs"
    assert C.low_tail_axis_category({**z, "cnt": 6, "fv": 82, "top5": 92, "key": "騎手A×芝長×40以下"}, "normal") == "axis"
    assert C.low_tail_axis_category({**z, "cnt": 10, "rv": 85, "frc": 120, "key": "東京×芝×23-24×定量×G1×5"}, "normal") is None
    # 乱数の行で: 末尾を上位の部品に替えたときの classify が abs / axis の行だけ、その区分を返す（ほかは None）
    rng = random.Random(5)
    heads = ["東京×ダ×1600×定量×G1", "騎手A×前走6着~", "芝×芝長×高", "阪神×芝×16-18"]
    n_hit = 0
    for _ in range(6000):
        rec = {"cnt": rng.choice([5, 6, 9, 10, 15, 19, 20, 29, 30, 60]), "fv": rng.choice([3, 15, 34, 36, 50, 62, 70, 82]),
               "rv": rng.choice([0, 50, 60, 85]), "top5": rng.choice([5, 34, 49, 55, 72, 80, 92]),
               "frc": rng.choice([None, 0, 99, 100, 150, 210]), "tra": rng.choice([None, 0, 100, 150, 210]),
               "wv": rng.choice([None, 0, 39, 40, 60]), "wedge": rng.choice([None, 0.2, 0.35, 0.9])}
        mode = rng.choice(odds.ODDS_MODES)
        h = rng.choice(heads)
        t = rng.choice(LOW_TAILS + OK_TAILS + ODD_TAILS)
        got = C.low_tail_axis_category({**rec, "key": f"{h}×{t}"}, mode)
        upper = C.classify({**rec, "key": f"{h}×1位"}, mode)
        want = upper if t in LOW_TAILS and upper in ("abs", "axis") else None
        assert got == want, (rec, mode, h, t)
        if got:
            n_hit += 1
            assert C.classify({**rec, "key": f"{h}×{t}"}, mode) is None
    assert n_hit > 100


def test_low_tail_gate_is_the_same_in_row_frame_and_sql():
    """指数の上限ゲートを入れても、1行版・ベクトル版・SQL 版（結合データ／key のどちらの列名でも）が同じ区分を返す。"""
    rng = random.Random(11)
    heads = ["東京×ダ×1600×定量×G1", "騎手A×前走6着~", "", "芝×芝長×高"]
    keys = [None, ""] + [(h + "×" + t) if h else t for h in heads for t in LOW_TAILS + OK_TAILS + ODD_TAILS]
    rows = [(i, rng.choice([5, 6, 9, 10, 15, 19, 20, 29, 30, 60]), rng.choice([0, 4.99, 19.99, 35, 50, 60, 70, 80, 95]),
             rng.choice([0, 50, 60, 80, 90]), rng.choice([0, 9.9, 34.9, 50, 70, 80, 90, 99]), rng.choice([0, 100, 150, 200]),
             rng.choice([0, 100, 150, 200]), rng.choice(keys), rng.choice([None, 0, 39.9, 40, 70]),
             rng.choice([None, 0.1, 0.3499, 0.35, 1.2])) for i in range(20000)]
    con = sqlite3.connect(":memory:")
    con.execute('CREATE TABLE t (id INTEGER, "件数" INTEGER, "複勝率_val" REAL, "連対率_val" REAL, "掲示板率_val" REAL, '
                '"複勝回収値" REAL, "単勝適正回収値" REAL, "結合データ" TEXT, "勝率_val" REAL, "WEDGE" REAL, key TEXT)')
    # 2つの列（結合データ・key）には別々の値を入れる（SQL 版が、渡した列名のほうを見ていることを確かめるため）
    alt = [rng.choice(keys) for _ in rows]
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?,?,?,?,?)", [r + (a,) for r, a in zip(rows, alt)])
    assert sum(C.is_low_tail(r[7]) != C.is_low_tail(a) for r, a in zip(rows, alt)) > 1000
    cols = ["id", "件数", "複勝率_val", "連対率_val", "掲示板率_val", "複勝回収値", "単勝適正回収値", "結合データ", "勝率_val", "WEDGE"]
    df = pd.DataFrame(rows, columns=cols)
    rec_of = lambda r: {"cnt": r[1], "fv": r[2], "rv": r[3], "top5": r[4], "frc": r[5], "tra": r[6], "wv": r[8], "wedge": r[9]}
    seen = set()
    for mode in odds.ODDS_MODES:
        py = [C.classify({**rec_of(r), "key": r[7]}, mode) for r in rows]
        assert list(C.classify_frame(df, mode)) == py
        assert list(C.classify_frame(df.rename(columns={"結合データ": "key"}), mode)) == py
        py_alt = [C.classify({**rec_of(r), "key": a}, mode) for r, a in zip(rows, alt)]
        assert py_alt != py
        for cat in C.CATEGORIES:
            for col, res in (("結合データ", py), ("key", py_alt)):
                got = {x[0] for x in con.execute(f"SELECT id FROM t WHERE {C.sql_condition(cat, mode, col)}")}
                assert got == {r[0] for r, c in zip(rows, res) if c == cat}, (mode, cat, col)
        for r, c in zip(rows, py):
            low = C.is_low_tail(r[7])
            assert not (low and c in C.TAIL_GATED)
            seen.add((c, low))
    assert {(c, False) for c in C.CATEGORIES} <= seen and ("keshi", True) in seen and (None, True) in seen
    # 結合データの列が無い DataFrame・SQL の NULL は、ゲートで落とさない
    nokey = df.drop(columns="結合データ")
    for mode in odds.ODDS_MODES:
        assert list(C.classify_frame(nokey, mode)) == [C.classify(rec_of(r), mode) for r in rows]


def test_keshi_rule_tells_the_favorite_only_rule_from_the_common_rule():
    """消し候補の理由: 全モード共通の規則（複勝率 < 5・掲示板率 < 10）か、2〜10倍未満専用の規則
    （複勝率 < 20 かつ（掲示板率 < 35 または WEDGE < 0.350））だけか。"""
    z = {"cnt": 20, "fv": 0, "top5": 0}
    assert C.keshi_rule({**z, "fv": 4.9, "top5": 9.9}, "odds10u") == "any" == C.keshi_rule({**z, "fv": 4.9, "top5": 9.9}, "normal")
    assert C.keshi_rule({**z, "fv": 19.9, "top5": 34.9}, "odds10u") == "fav"
    assert C.keshi_rule({**z, "fv": 19.9, "top5": 50, "wedge": 0.34}, "odds10u") == "fav"
    assert C.keshi_rule({**z, "fv": 19.9, "top5": 50}, "odds10u") is None            # 掲示板率 35 以上・WEDGE なし → 消しではない
    assert C.keshi_rule({**z, "fv": 19.9, "top5": 50, "wedge": 0.35}, "odds10u") is None
    assert C.keshi_rule({**z, "fv": 4.9, "top5": 10}, "odds10u") == "fav"            # 掲示板率が 10 以上 → 共通の規則ではない
    for mode in ("normal", "odds10_50", "odds50p"):
        assert C.keshi_rule({**z, "fv": 19.9, "top5": 20, "wedge": 0.1}, mode) is None
    assert C.keshi_rule({**z, "fv": 20, "top5": 20}, "odds10u") is None
    assert C.keshi_rule({**z, "cnt": 19, "fv": 10, "top5": 20}, "odds10u") is None
    assert C.keshi_rule({**z, "cnt": 12, "fv": 10, "top5": 20, "key": "東京×芝×2400×G1×6位~"}, "odds10u") == "fav"   # 希少条件は12件から
    assert C.keshi_rule({**z, "fv": None, "top5": 5}, "odds10u") is None
    rng = random.Random(5)
    n_fav = 0
    for _ in range(6000):                                # classify が消し候補と言う行だけ理由が付く
        rec = {"cnt": rng.choice([11, 12, 19, 20, 40]), "fv": rng.choice([0, 4.9, 5, 19.9, 20, None]),
               "top5": rng.choice([0, 9.9, 10, 34.9, 35, 60, None]), "rv": 0, "frc": 0, "tra": 0,
               "wedge": rng.choice([None, 0.1, 0.3499, 0.35, 0.8]),
               "key": rng.choice([None, "a×1位", "a×G1×6位~"])}
        n_fav += C.keshi_rule(rec, "odds10u") == "fav"
        for mode in odds.ODDS_MODES:
            assert (C.classify(rec, mode) == "keshi") == (C.keshi_rule(rec, mode) is not None)
    assert n_fav > 300


def test_tradeoff_groups_use_registered_indicator_names():
    """負の相関の組は台帳の正式名で、双方向に引ける。"""
    assert [g[0] for g in indicators.TRADEOFF_GROUPS] == ["StridePlus", "吉馬", "安井式"]
    for _, left, right in indicators.TRADEOFF_GROUPS:
        assert not set(left) & set(right)
        for name in left + right:
            assert name in indicators.INDICATOR_ORDER and indicators.canonical_indicator(name) == name, name
        for a in left:
            assert indicators.tradeoff_opponents(a) == frozenset(right)
        for b in right:
            assert indicators.tradeoff_opponents(b) == frozenset(left)
    assert indicators.tradeoff_opponents("先行") == {"瞬発", "想定後半", "追走", "持久", "持続"}
    assert indicators.tradeoff_opponents("持続") == {"先行", "想定前半", "ST指数"}
    assert indicators.tradeoff_opponents("吉馬_末脚力") == {"吉馬_先行力"}
    assert indicators.tradeoff_opponents("安井_根性") == {"安井_スピード"}
    for name in ("SAV", "タイムM", "吉馬_SP能力値", "安井_ランク", "安井_賢さ", "仕上り", "展開", "", None):
        assert indicators.tradeoff_opponents(name) == frozenset()


def test_classify_small_percent_not_rescaled():
    """旧版は 0〜1 の値を割合とみなして100倍し、掲示板率0.95%を95%と読んで消し候補から外していた。"""
    assert C.classify({"cnt": 50, "fv": 0.9, "rv": 0.9, "top5": 0.95, "frc": 0, "tra": 0}, "normal") == "keshi"


def test_sql_condition_matches_python():
    rng = random.Random(3)
    con = sqlite3.connect(":memory:")
    con.execute('CREATE TABLE t (id INTEGER, "件数" INTEGER, "複勝率_val" REAL, "連対率_val" REAL, '
                '"掲示板率_val" REAL, "複勝回収値" REAL, "単勝適正回収値" REAL, "結合データ" TEXT, key TEXT, '
                '"勝率_val" REAL, "WEDGE" REAL)')
    rows = []
    pick = lambda vals: rng.choice(vals + [None])
    for i in range(30000):
        key = pick(RARE_KEY_SAMPLES)
        rows.append((i, pick([5, 6, 9, 10, 11, 12, 14, 15, 19, 20, 29, 30, 60]),
                     pick([0, 4.99, 5, 19.99, 33, 34.99, 35, 49.99, 50, 60, 70, 79.99, 80, 95]),
                     pick([0, 49.9, 50, 60, 79.99, 80, 90]), pick([0, 9.9, 10, 34.99, 35, 49.99, 50, 70, 80, 90, 99]),
                     pick([0, 99, 100, 149.99, 150, 199.99, 200]), pick([0, 99, 100, 149.99, 150, 199.99, 200]),
                     key, key, pick([0, 39.99, 40, 75]), pick([0, 0.3499, 0.35, 0.62, 1.4])))
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    seen, n_abs2, n_fav = set(), 0, 0
    for mode in odds.ODDS_MODES:
        expected = {c: set() for c in C.CATEGORIES}
        for r in rows:
            rec = {"cnt": r[1], "fv": r[2], "rv": r[3], "top5": r[4], "frc": r[5], "tra": r[6], "key": r[7], "wv": r[9],
                   "wedge": r[10]}
            cat = C.classify(rec, mode)
            n_abs2 += cat == "abs" and not (r[3] is not None and r[3] >= 80)                # 条件②だけで絶対軸の行
            n_fav += C.keshi_rule(rec, mode) == "fav"
            if cat:
                expected[cat].add(r[0])
                seen.add((cat, C.is_rare_key(r[7])))
        for cat in C.CATEGORIES:
            got = {x[0] for x in con.execute(f"SELECT id FROM t WHERE {C.sql_condition(cat, mode)}")}
            assert got == expected[cat], (mode, cat)
            got = {x[0] for x in con.execute(f"SELECT id FROM t WHERE {C.sql_condition(cat, mode, 'key')}")}
            assert got == expected[cat], (mode, cat, "key")
    assert seen == {(c, r) for c in C.CATEGORIES for r in (True, False)}       # どの区分も、希少・ふつうの両方を試している
    assert n_abs2 > 20 and n_fav > 200                                         # 絶対軸の条件②・2〜10倍未満専用の消しも試している
    # ベクトル版も同じ（勝率_val・WEDGE の NULL / NaN はどの比較も満たさない）
    df = pd.DataFrame(rows, columns=["id", "件数", "複勝率_val", "連対率_val", "掲示板率_val", "複勝回収値", "単勝適正回収値",
                                     "結合データ", "key", "勝率_val", "WEDGE"]).drop(columns="key")
    for mode in odds.ODDS_MODES:
        want = [C.classify({"cnt": r[1], "fv": r[2], "rv": r[3], "top5": r[4], "frc": r[5], "tra": r[6], "key": r[7],
                            "wv": r[9], "wedge": r[10]}, mode) for r in rows]
        assert list(C.classify_frame(df, mode)) == want, mode
        # 勝率_val・WEDGE の列が無い表は、その値が無いものとして判定する（1行版で渡さないのと同じ）
        bare = [C.classify({"cnt": r[1], "fv": r[2], "rv": r[3], "top5": r[4], "frc": r[5], "tra": r[6], "key": r[7]}, mode)
                for r in rows]
        assert list(C.classify_frame(df.drop(columns=["勝率_val", "WEDGE"]), mode)) == bare and bare != want


RARE_KEY_SAMPLES = ["東京×芝×23-24×定量×G1×3", "中山×芝×2000×内×ハンデ×重賞×1~3位", "準OP・OP×61~65",
                    "芝×芝長×高×1位", "ダ×ダ長×前走1~3着×6位~", "G1", "重賞×1",
                    # 希少条件ではない（部品の一部・大文字小文字・似た語）
                    "東京×ダ×1600×定量×1・2_勝×4", "東京×芝×16-18×馬齢×1勝・OP×2", "芝×芝マイル×61~65",
                    "騎手G1×1", "g1×1", "芝長い×1", "新馬×重賞級×2", "", "髙杉吏麒×前走上り1~3位×1~5位",
                    # 末尾の改行・正規表現の記号（1行版・ベクトル版・SQL 版で同じ見方。部品の完全一致だけ）
                    "東京×芝×2400×定量×G1\n", "G1\n", "(G1)×1", "準OP.OP×1", "x×G1 ×1"]


def test_classify_rare_keys_and_high_return():
    assert [C.is_rare_key(k) for k in RARE_KEY_SAMPLES] == [True] * 7 + [False] * 14
    assert not C.is_rare_key(None)
    z = {"fv": 0, "rv": 0, "top5": 0, "frc": 0, "tra": 0}
    g1, plain = "東京×芝×23-24×定量×G1×3", "東京×芝×16-18×定量×1・2_勝×3"
    # 希少条件は最低件数が下がる（軸 6、相手 15、消し 12。絶対軸は 2026-11-23 から下げない）。ふつうの条件はそのまま
    for key, n_abs, n_ax, n_opp, n_ks in ((g1, 10, 6, 15, 12), (plain, 10, 20, 30, 20)):
        assert C.classify({**z, "cnt": n_abs, "rv": 80, "frc": 100, "key": key}, "normal") == "abs"
        assert C.classify({**z, "cnt": n_abs - 1, "rv": 80, "frc": 100, "key": key}, "normal") is None
        assert C.classify({**z, "cnt": n_ax, "fv": 80, "top5": 90, "key": key}, "normal") == "axis"
        assert C.classify({**z, "cnt": n_ax - 1, "fv": 80, "top5": 90, "key": key}, "normal") is None
        assert C.classify({**z, "cnt": n_opp, "fv": 60, "top5": 70, "key": key}, "normal") == "opp"
        assert C.classify({**z, "cnt": n_opp - 1, "fv": 60, "top5": 70, "key": key}, "normal") is None
        assert C.classify({**z, "cnt": n_ks, "fv": 4.9, "top5": 9.9, "key": key}, "normal") == "keshi"
        assert C.classify({**z, "cnt": n_ks - 1, "fv": 4.9, "top5": 9.9, "key": key}, "normal") is None
        assert C.classify({**z, "cnt": n_ks, "fv": 19.9, "top5": 34.9, "key": key}, "odds10u") == "keshi"
        assert C.classify({**z, "cnt": n_ks - 1, "fv": 19.9, "top5": 34.9, "key": key}, "odds10u") is None
        assert C.classify({**z, "cnt": n_ks, "fv": 19.9, "top5": 50, "wedge": 0.34, "key": key}, "odds10u") == "keshi"
        assert C.classify({**z, "cnt": n_ks - 1, "fv": 19.9, "top5": 50, "wedge": 0.34, "key": key}, "odds10u") is None
    # 結合データを渡さなければ希少条件ではない（KEY_COLUMN の名前でも渡せる）
    assert C.classify({**z, "cnt": 6, "fv": 80, "top5": 90}, "normal") is None
    assert C.classify({**z, "cnt": 6, "fv": 80, "top5": 90, "結合データ": g1}, "normal") == "axis"
    # 高回収率基準（軸候補）: 2〜10倍未満・10〜50倍のときだけ
    # （回収値は 単勝適正回収値 で試す。複勝回収値 ≥ 150 だと、もとからある相手候補の条件にも当たるため）
    hr1 = {**z, "cnt": 30, "fv": 35, "top5": 50, "tra": 200}
    hr2 = {**z, "cnt": 30, "fv": 50, "tra": 150}
    for mode, want in (("odds10u", "axis"), ("odds10_50", "axis"), ("odds50p", None), ("normal", None)):
        assert C.classify(hr1, mode) == want and C.classify(hr2, mode) == want, mode
        # 複勝回収値で満たしても同じ（50倍以上・通常では、もとの相手候補の条件に当たる）
        assert C.classify({**hr1, "tra": 0, "frc": 200}, mode) == (want or "opp"), mode
        assert C.classify({**hr2, "tra": 0, "frc": 150}, mode) == want, mode
    for miss in ({"cnt": 29}, {"fv": 34.99}, {"top5": 49.99}, {"tra": 199.99}):
        assert C.classify({**hr1, **miss}, "odds10_50") is None, miss
    for miss in ({"cnt": 29}, {"fv": 49.99}, {"tra": 149.99}):
        assert C.classify({**hr2, **miss}, "odds10_50") is None, miss
    # 希少条件なら高回収率基準は 15件から
    assert C.classify({**hr1, "cnt": 15, "key": g1}, "odds10_50") == "axis"
    assert C.classify({**hr1, "cnt": 14, "key": g1}, "odds10_50") is None
    assert C.classify({**hr1, "cnt": 15, "key": plain}, "odds10_50") is None
    # 判定順は変わらない: 消し → 絶対軸 → 軸（高回収率でも絶対軸の条件を満たせば絶対軸）
    assert C.classify({**hr2, "rv": 80, "frc": 150}, "odds10u") == "abs"
    assert C.classify({**z, "cnt": 30, "fv": 19.9, "top5": 34, "frc": 300, "tra": 300}, "odds10u") == "keshi"
    assert C.classify({**z, "cnt": 30, "fv": 19.9, "top5": 60, "frc": 300, "tra": 300, "wedge": 0.34}, "odds10u") == "keshi"
    # 説明の文と規則の値（設定のハッシュに入る）
    d = C.description_lines()
    assert any("高回収率" in x for x in d["axis"]) and all(any("希少条件" in x for x in d[c]) for c in C.CATEGORIES)
    s = C.rule_settings()
    assert s["RARE_TOKENS"] == list(C.RARE_TOKENS) and s["HR_CNT"] == 30 and s["RARE_KS_CNT"] == 12
    # 軸候補の最低件数（2026-11-14 に 10 → 20）。ほかの下限はそのまま。説明の文も同じ値
    assert (s["AX_CNT"], s["ABS_CNT"], s["OP_CNT"], s["KS_CNT"]) == (20, 10, 30, 20)
    assert (s["RARE_AX_CNT"], s["RARE_OP_CNT"], s["RARE_HR_CNT"]) == (6, 15, 15) and "RARE_ABS_CNT" not in s
    assert all(x.startswith("件数 ≥ 20 かつ") for x in d["axis"][:4]) and d["abs"][0].startswith("件数 ≥ 10 かつ")
    assert any("件数 ≥ 6（高回収率は 件数 ≥ 15）" in x for x in d["axis"]) and "件数 ≥ 30" in d["axis"][4]


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
    """新しい年ほど重い指数減衰（合計1）。decay=0.65 で Phase 3 の固定ウェイトをほぼ再現する。"""
    w5 = wedge.year_weights(range(2021, 2026))
    assert w5.sum() == pytest.approx(1.0) and all(np.diff(w5) > 0)
    assert w5 == pytest.approx([0.06, 0.11, 0.18, 0.25, 0.40], abs=0.013)
    assert wedge.year_weights(range(2022, 2026)) == pytest.approx([0.12, 0.19, 0.27, 0.42], abs=0.011)
    w10 = wedge.year_weights(range(2016, 2026))
    assert len(w10) == 10 and w10.sum() == pytest.approx(1.0)
    assert w10[-1] == pytest.approx(1 / sum(0.65 ** k for k in range(10)))
    assert w10[:5].sum() == pytest.approx(0.104, abs=0.002)                  # 古い5年の合計は約1割
    assert wedge.year_weights([2025]).tolist() == [1.0]
    assert wedge.year_weights([2025, 2021, 2024]) == pytest.approx(
        np.array([1.0, 0.65 ** 4, 0.65]) / (1 + 0.65 ** 4 + 0.65))           # 並びは入力どおり、暦の年の差で効く
    assert wedge.year_weights(range(2021, 2026), decay=1.0) == pytest.approx([0.2] * 5)
    with pytest.raises(ValueError):
        wedge.year_weights([2025], decay=0)


def test_adjusted_wedge_scalar():
    yearly = {2013: 9.0, 2016: 1.0, 2020: 1.0, 2024: 2.0, 2025: 2.0}
    w = wedge.year_weights([2016, 2020, 2024, 2025])
    assert wedge.adjusted_wedge(yearly) == pytest.approx(np.dot(w, [1.0, 1.0, 2.0, 2.0]))   # 2013 は10年の窓の外
    assert wedge.adjusted_wedge(yearly, max_years=20) == pytest.approx(
        np.dot(wedge.year_weights([2013, 2016, 2020, 2024, 2025]), [9.0, 1.0, 1.0, 2.0, 2.0]))
    # 件数 < 10 の年は除く（既定の最低件数）
    assert wedge.adjusted_wedge({2023: 1.0, 2024: 2.0}, {2023: 9, 2024: 10},
                                min_year_count=wedge.DEFAULT_MIN_YEAR_COUNT) == pytest.approx(2.0)
    assert wedge.DEFAULT_MIN_YEAR_COUNT == 10 and wedge.DEFAULT_MAX_YEARS == 10
    assert np.isnan(wedge.adjusted_wedge({2015: 1.0}, window_end=2025))                     # 窓の外だけ


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
@pytest.mark.parametrize("min_year", [0, 10, 30])
@pytest.mark.parametrize("max_years,decay", [(10, 0.65), (5, 0.5), (3, 1.0)])
def test_wedge_columns_match_reference(window, min_year, max_years, decay):
    df = _random_material(1, years=range(2012, 2026))
    ref_year = 2026 if window == "data" else None      # data 窓: 素材の最終年より後を指定しても動く
    out = wedge.wedge_columns(df["k"], df["finish_num"], df["単勝オッズ"], df["y"], ref_year, min_year, window,
                              max_years=max_years, decay=decay)
    for k, g in df.groupby("k"):
        r = wedge.wedge_from_frame(g)
        assert out.loc[k, "WIN"] == pytest.approx(r["WIN"])
        assert out.loc[k, "EDGE"] == pytest.approx(r["EDGE"])
        assert out.loc[k, "WEDGE"] == pytest.approx(r["WEDGE"])
        gy = g[g["y"].notna()]
        yearly = {int(y): wedge.wedge_from_frame(x)["WEDGE"] for y, x in gy.groupby("y")}
        counts = {int(y): len(x) for y, x in gy.groupby("y")}
        end = ref_year if window == "data" else max(counts)
        adj = wedge.adjusted_wedge(yearly, counts, min_year, end, max_years=max_years, decay=decay)
        if adj != adj:
            assert pd.isna(out.loc[k, "adjusted_WEDGE"]) and out.loc[k, "adjusted_WEDGE_年数"] == 0
        else:
            assert out.loc[k, "adjusted_WEDGE"] == pytest.approx(adj)


def test_wedge_hand_example():
    """手計算の例。1キー、2014〜2025年の各年40件（2016年だけ9件）。"""
    rows = []
    for y in range(2014, 2026):
        for i in range(40):
            fin = 1 if i < 4 else (2 if i < 8 else (3 if i < 12 else 10))      # 1着4・2着4・3着4・着外28
            odds = 2.0 if i < 20 else 30.0                                    # 2倍→1着のみ成功、30倍→6着以内
            rows.append({"k": "A", "f": fin, "o": odds, "y": y})
    df = pd.DataFrame(rows)
    df.loc[(df["y"] == 2016) & (df.index % 40 >= 9), "y"] = np.nan             # 2016年は9件（<10）にする
    out = wedge.wedge_columns(df["k"], df["f"], df["o"], df["y"], ref_year=2025)   # 既定: 10件・10年・0.65
    # 年40件: WIN = (4*3 + 4*1.5 + 4*1) / (40*3) = 22/120
    #         EDGE = 2倍(20頭)の1着4頭 + 30倍(20頭)の6着以内0頭（i<20 が2倍）→ 4/40
    w_y = 22 / 120 + 4 / 40
    # 窓は2016〜2025。2016は9件で除外 → 2017〜2025の9年。どの年も同じ値なので加重平均もその値
    assert out.loc["A", "adjusted_WEDGE_年数"] == 9
    assert out.loc["A", "adjusted_WEDGE_件数"] == 360
    assert out.loc["A", "adjusted_WEDGE_最終年"] == 2025
    assert out.loc["A", "adjusted_WEDGE"] == pytest.approx(w_y)
    # 全期間（年が欠けた行も含む）: 480件
    assert out.loc["A", "WIN"] == pytest.approx((12 * 22) / (480 * 3))


def test_wedge_year_weighting_uses_decay_weights():
    """年ごとに WEDGE が違うとき、新しい年ほど重い減衰ウェイトが掛かる（3〜10年の可変年数）。"""
    for n_years in (3, 4, 7, 10):
        rows = []
        years = list(range(2026 - n_years, 2026))
        for j, y in enumerate(years):
            for i in range(10):                                                # 各年ちょうど10件（足切りの境界）
                rows.append({"k": "A", "f": 1 if i < j % 10 + 1 else 18, "o": 5.0, "y": y})
        df = pd.DataFrame(rows)
        out = wedge.wedge_columns(df["k"], df["f"], df["o"], df["y"], ref_year=2025)
        per_year = [((j % 10 + 1) * 3 / 30) + ((j % 10 + 1) / 10) for j in range(n_years)]
        w = np.array([0.65 ** (2025 - y) for y in years])
        assert out.loc["A", "adjusted_WEDGE"] == pytest.approx(np.dot(w / w.sum(), per_year))
        assert out.loc["A", "adjusted_WEDGE_年数"] == n_years


def test_wedge_gap_year_is_older():
    """開催の無い年（または件数不足の年）をはさむと、それより古い年は暦の差の分だけ軽くなる。"""
    rows = [{"k": "A", "f": 1, "o": 2.0, "y": 2021} for _ in range(10)]          # 2021: 全勝 → WEDGE=2
    rows += [{"k": "A", "f": 18, "o": 2.0, "y": 2023} for _ in range(10)]        # 2023: 全敗 → WEDGE=0（2022 は無い）
    df = pd.DataFrame(rows)
    out = wedge.wedge_columns(df["k"], df["f"], df["o"], df["y"], ref_year=2023)
    w_old = 0.65 ** 2 / (1 + 0.65 ** 2)
    assert out.loc["A", "adjusted_WEDGE"] == pytest.approx(2.0 * w_old)


def test_wedge_min_year_count_boundary():
    rows = [{"k": "A", "f": 1, "o": 2.0, "y": 2024} for _ in range(9)]
    rows += [{"k": "B", "f": 1, "o": 2.0, "y": 2024} for _ in range(10)]
    df = pd.DataFrame(rows)
    out = wedge.wedge_columns(df["k"], df["f"], df["o"], df["y"])
    assert pd.isna(out.loc["A", "adjusted_WEDGE"]) and out.loc["A", "adjusted_WEDGE_年数"] == 0
    assert out.loc["B", "adjusted_WEDGE"] == pytest.approx(2.0) and out.loc["B", "adjusted_WEDGE_年数"] == 1


def test_wedge_key_window_ignores_years_without_wedge():
    """window=key の窓の終わりは、WEDGE が出る年（オッズのある馬がいる年）の最終年。1グループ版と同じ。"""
    rows = [{"k": "A", "f": 1, "o": 2.0, "y": 2022} for _ in range(10)]
    rows += [{"k": "A", "f": 18, "o": None, "y": 2025} for _ in range(10)]       # 2025 はオッズ無し → WEDGE 空
    df = pd.DataFrame(rows)
    out = wedge.wedge_columns(df["k"], df["f"], df["o"], df["y"], window="key", max_years=3)
    scalar = wedge.adjusted_wedge({2022: 2.0, 2025: float("nan")}, {2022: 10, 2025: 10}, 10, None, max_years=3)
    assert scalar == pytest.approx(2.0) and out.loc["A", "adjusted_WEDGE"] == pytest.approx(scalar)


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
    with pytest.raises(ValueError):
        wedge.wedge_columns(pd.Series(["a"]), pd.Series([1]), pd.Series([2.0]), pd.Series([2024]), decay=1.5)
    with pytest.raises(ValueError):
        wedge.wedge_columns(pd.Series(["a"]), pd.Series([1]), pd.Series([2.0]), pd.Series([2024]), max_years=0)


def test_unknown_finish_is_edge_failure():
    """着順が数値にならない馬は WIN と同じく分母に残して失敗扱い（オッズの無い馬だけ EDGE の分母から外す）。"""
    fin = pd.Series([1, np.nan, np.nan, np.nan, 10, 10, 10, 10, 10, 10], dtype=float)
    odds = pd.Series([2.0] * 9 + [None])
    out = wedge.wedge_columns(pd.Series(["a"] * 10), fin, odds, pd.Series([2025] * 10), min_year_count=0)
    assert out.loc["a", "EDGE"] == pytest.approx(1 / 9)          # 分母 = オッズのある9頭
    assert out.loc["a", "WIN"] == pytest.approx(3 / 30)          # 分母 = 10頭
    r = wedge.wedge_from_frame(pd.DataFrame({"finish_num": fin, "単勝オッズ": odds}))
    assert r["EDGE"] == pytest.approx(1 / 9)
    assert wedge.edge_success(odds, fin).tolist()[:4] == [1, 0, 0, 0]


def test_invalid_years_are_ignored():
    y = pd.Series(["2024", "2024.5", "99999", "inf", "", "1899", "2025"])
    assert wedge.valid_years(y).tolist()[0] == 2024 and wedge.valid_years(y).tolist()[-1] == 2025
    assert wedge.valid_years(y).iloc[1:6].isna().all()
    out = wedge.wedge_columns(pd.Series(["a"] * 7), pd.Series([1] * 7), pd.Series([2.0] * 7), y, min_year_count=0)
    assert out.loc["a", "adjusted_WEDGE_年数"] == 2 and out.loc["a", "adjusted_WEDGE_最終年"] == 2025
