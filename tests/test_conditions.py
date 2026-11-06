"""22条件（shisu/conditions.py・shisu/course.py）の導出と、過去側・出馬表側の一致。"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

from shisu import conditions as C  # noqa: E402
from shisu import course  # noqa: E402
from shisu import families as F  # noqa: E402


# ---------------------------------------------------------------- 1つずつの規則
@pytest.mark.parametrize("date,expected", [("20260115", "12-2月"), ("20261201", "12-2月"), ("20260228", "12-2月"),
                                           ("20260301", "3-5月"), ("20260531", "3-5月"), ("20260601", "6-9月"),
                                           ("20260930", "6-9月"), ("20261001", "10-11月"), ("20261130", "10-11月"),
                                           ("", ""), ("2026-01", "")])
def test_season(date, expected):
    assert C.season(date) == expected


@pytest.mark.parametrize("weeks,expected", [("1", "連闘"), ("2", "中2-3"), ("3", "中2-3"), ("4", "中4-6"),
                                            ("6", "中4-6"), ("7", "中7-9"), ("9", "中7-9"), ("10", "中10-20"),
                                            ("20", "中10-20"), ("21", "中21週以上"), ("52", "中21週以上"), ("", "")])
def test_interval_band(weeks, expected):
    assert C.interval_band(weeks) == expected


def test_interval_text_forms():
    """過去側は週数、出馬表側は 連闘・初走 の文字も入る（どちらも TARGET の「間隔」。連闘=1）。"""
    assert [C._interval_weeks(v) for v in ("連闘", "1", " 3 ", "初走", "", "０", "１２")] == \
        ["1", "1", "3", "", "", "", "12"]


@pytest.mark.parametrize("n,expected", [("1", "休み明け(1戦)"), ("2", "叩き2戦"), ("4", "叩き4戦"), ("5", "叩き5戦以上"),
                                        ("12", "叩き5戦以上"), ("", "")])
def test_tataki_band(n, expected):
    assert C.tataki_band(n) == expected


@pytest.mark.parametrize("w,expected", [("50", "51以下"), ("51", "51以下"), ("51.5", "51.5-53"), ("53", "51.5-53"),
                                        ("53.5", "53.5-55"), ("55", "53.5-55"), ("57", "55.5-57"), ("58", "57.5-59"),
                                        ("59", "57.5-59"), ("59.5", "59.5以上"), ("57▲", "55.5-57"), ("５５☆", "53.5-55"),
                                        ("", ""), ("0", "")])
def test_weight_band(w, expected):
    assert C.weight_band(w) == expected


@pytest.mark.parametrize("cur,prev,expected", [("56", "56", "同じ"), ("57", "56", "+0.5~+1.5"), ("56.5", "56", "+0.5~+1.5"),
                                               ("58", "56", "+2kg以上"), ("55", "56", "-0.5~-1.5"), ("54", "56", "-2kg以下"),
                                               ("53▲", "56", "-2kg以下"), ("56", "", ""), ("", "56", "")])
def test_weight_change(cur, prev, expected):
    assert C.weight_change(cur, prev) == expected


@pytest.mark.parametrize("cur,prev,expected", [("1600", "1600", "同距離"), ("1800", "1600", "延長200"),
                                               ("2000", "1600", "延長400"), ("2400", "1600", "延長400超"),
                                               ("1400", "1600", "短縮200"), ("1200", "1600", "短縮400"),
                                               ("1000", "1600", "短縮400超"), ("1700", "1600", "延長200"),
                                               ("1600", "", "")])
def test_distance_change(cur, prev, expected):
    assert C.distance_change(cur, prev) == expected


def test_prev_rid_from_kaisai():
    """出馬表の「前走開催」→ 前走のレースID(新)先頭16桁（過去側の 前走レースID(新) と同じ形）。"""
    assert C._prev_rid16_from_kaisai("260816 2新8  1R") == "2026081604020801"
    assert C._prev_rid16_from_kaisai("261025 4東7 11R") == "2026102505040711"
    assert C._prev_rid16_from_kaisai("261025 大井 11R") == ""          # 地方は引かない
    assert C._prev_rid16_from_kaisai("") == ""


def test_course_master_lookup():
    assert course.resolve_course("東京", "芝", "1600")["今走クワドラント"] == "OT"
    # 京都芝1600 は内外で別のコース。内外が分からなければ引かない（空）
    assert course.resolve_course("京都", "芝", "1600", "外")["R値帯"] == "200以上"
    assert course.resolve_course("京都", "芝", "1600", "内")["R値帯"] == "160-180"
    assert course.resolve_course("京都", "芝", "1600") is None
    assert {("京都", "芝", "1400"), ("京都", "芝", "1600"), ("新潟", "芝", "2000")} <= course.ambiguous_courses()
    # 片方の内外にしか無い距離は、内外が分からなくても一意に引ける
    assert course.resolve_course("中山", "ダ", "1200")["CFS帯"] == "高"
    assert course.resolve_course("大井", "ダ", "1600") is None
    vals = course.course_table()
    assert len(vals) > 50


def test_course_bands_use_spec_labels():
    tbl = course.resolve_courses(*(pd.Series(v) for v in zip(*[(p, s, d, "") for p, s, d in
                                                                 (("東京", "芝", "1600"), ("中山", "ダ", "1200"),
                                                                  ("阪神", "芝", "2000"), ("小倉", "芝", "1200"))])))
    assert set(tbl["R値帯"]) <= set(course.R_BANDS) | {""}
    assert set(tbl["直線帯"]) <= set(course.STRAIGHT_BANDS) | {""}
    assert set(tbl["初角帯"]) <= set(course.CORNER_BANDS) | {""}
    assert set(tbl["CFS帯"]) <= set(course.LEVEL_BANDS) | {""} and set(tbl["F係数帯"]) <= set(course.LEVEL_BANDS) | {""}
    assert set(tbl["今走クワドラント"]) <= set(course.QUADRANTS) | {""}
    assert (tbl["R値帯"] != "").all()


@pytest.mark.parametrize("stable,place,expected", [("美浦", "東京", "110km以内"), ("栗東", "東京", "351-500"),
                                                   ("美浦", "札幌", "滞在"), ("(栗)", "京都", ""), ("地方", "東京", ""),
                                                   ("", "東京", "")])
def test_transport_band(stable, place, expected):
    assert course.transport_band(stable, place) == expected
    if stable == "(栗)":
        assert course.transport_band(C._stable(stable), place) == "110km以内"   # (栗) は 栗東 に直してから引く


@pytest.mark.parametrize("sd,dist,expected", [("芝", "1000", "芝短"), ("芝", "1399", "芝短"), ("芝", "1400", "芝マイル"),
                                              ("芝", "1800", "芝中"), ("芝", "2299", "芝中"), ("芝", "2300", "芝クラシック"),
                                              ("芝", "2600", "芝長"), ("ダ", "1200", "ダ短"), ("ダ", "1400", "ダマイル"),
                                              ("ダ", "1800", "ダ中"), ("ダ", "2100", "ダ長"), ("障", "3000", ""),
                                              ("芝", "", "")])
def test_distance_band(sd, dist, expected):
    assert course.distance_band(sd, dist) == expected


def test_turn_and_slope():
    assert [course.turn_direction(p) for p in ("東京", "中京", "新潟", "中山", "京都", "大井")] == ["左"] * 3 + ["右"] * 2 + [""]
    assert [course.slope(p) for p in ("東京", "中山", "阪神", "福島", "中京")] == ["坂あり"] * 5
    assert [course.slope(p) for p in ("京都", "新潟", "小倉", "函館", "札幌")] == ["坂なし"] * 5


def test_style_and_r3_normalization():
    std = pd.DataFrame([{c: "" for c in C.STD_COLUMNS}] * 9)
    std["前走脚質"] = ["逃げ", "逃", "先", "中団", "後", "ﾏｸﾘ", "マ", "", "差し"]
    std["前走R印3"] = ["HHH", "HH", "H", "M", "S", "SS", "SSS", "", "X"]
    out = C.derive(std)
    assert out["前走脚質"].tolist() == ["逃げ", "逃げ", "先行", "中団", "後方", "", "", "", ""]
    assert out["前走R印3"].tolist() == ["HHH~HH", "HHH~HH", "H", "M", "S", "SS~SSS", "SS~SSS", "", ""]


def test_prev_agari_zero_is_empty():
    std = pd.DataFrame([{c: "" for c in C.STD_COLUMNS}] * 3)
    std["前走上り3F順"] = ["", "1", "12"]
    assert C.derive(std)["前走上り3F順"].tolist() == ["", "1", "12"]
    raw = pd.DataFrame({"前走上り3F順": ["0", "3", "０"]})
    norm = pd.DataFrame({c: [""] * 3 for c in ("日付", "場所", "芝・ダ", "距離_raw", "芝(内・外)")})
    assert C.standardize_history(raw, norm)["前走上り3F順"].tolist() == ["", "3", ""]


def test_prev_quadrant_uses_inner_lookup_for_ambiguous_course():
    """前走が京都芝1600（内外で別コース）だと、前走レースID の内外を過去データから引く。引けなければ空。"""
    std = pd.DataFrame([{c: "" for c in C.STD_COLUMNS}] * 3)
    std["前走場所"], std["前走芝ダ"], std["前走距離"] = "京都", "芝", "1600"
    std["前走RID16"] = ["2026101108040501", "2026101108040502", "2026101108040503"]
    lookup = {"2026101108040501": "外", "2026101108040502": "内"}
    out = C.derive(std, lookup)
    assert out["前走クワドラント"].tolist() == [course.resolve_course("京都", "芝", "1600", "外")["今走クワドラント"],
                                          course.resolve_course("京都", "芝", "1600", "内")["今走クワドラント"], ""]
    assert C.derive(std)["前走クワドラント"].tolist() == ["", "", ""]


# ---------------------------------------------------------------- 過去側と出馬表側の一致
RACES = [("東京", "ダ", "1600", ""), ("京都", "芝", "1600", "外"), ("新潟", "芝", "1600", "外"),
         ("中山", "芝", "2000", ""), ("阪神", "ダ", "1200", ""), ("札幌", "芝", "1800", "")]
VCODE = {"東京": "05", "京都": "08", "新潟": "04", "中山": "06", "阪神": "09", "札幌": "01"}


def _paired_frames(seed: int = 5, heads: int = 12):
    """同じ馬・同じ前走を、過去側（★6タブの列名）と出馬表側（出馬表分析の列名）で作る。"""
    rng = random.Random(seed)
    hist, ent = [], []
    for r, (place, sd, dist, inner) in enumerate(RACES, 1):
        rid16 = f"20260531{VCODE[place]}0212{r:02d}"
        mare_race = r % 3 == 0
        for uma in range(1, heads + 1):
            pr = synth.prev_run(rng, place, sd, dist, "20260531")
            h, e = dict(pr["history"]), dict(pr["entries"])
            # 牝馬限定はレース単位（過去側は 性別限定 列、出馬表側は全頭が牝かで決まる）
            h["性別限定"] = "牝" if mare_race else ""
            e["性"] = "牝" if mare_race else ("牡" if uma % 2 else "牝")
            common = {"レースID(新)": f"{rid16}{uma:02d}", "場所": place, "距離": dist, "馬番": str(uma),
                      "枠番": str(min(8, (uma + 1) // 2)), "騎手": synth.JOCKEYS[uma % len(synth.JOCKEYS)]}
            hist.append({**common, **h, "芝・ダ": sd, "芝(内・外)": inner, "頭数": str(heads), "クラス名": "1勝",
                         "重量種別": "定量", "指数": "50", "着順": "1", "単勝オッズ": "3.0"})
            ent.append({**common, **e, "芝ダ": sd, "外回り": inner, "頭": str(heads), "クラス": "1勝",
                        "年限定": "４歳以上", "レース名": "テスト特別", "場 R": f"{place[0]}{r}"})
    return pd.DataFrame(hist), pd.DataFrame(ent)


def _history_conditions(hist: pd.DataFrame, lookup) -> pd.DataFrame:
    import replace_csv_ver3 as r
    norm = r.normalize_history(hist)
    return pd.concat([norm, C.derive(C.standardize_history(hist, norm), lookup)], axis=1)


def _entry_conditions(ent: pd.DataFrame, lookup) -> pd.DataFrame:
    import build_db
    return build_db.normalize_entries_df(ent, lookup)


@pytest.fixture(scope="module")
def paired():
    hist, ent = _paired_frames()
    # 前走の内外の表: 過去データ（前走レースID の先頭16桁）→ 芝(内・外)。前走が京都芝1600 の馬にも効くように全部入れる
    lookup = {rid[:16]: random.Random(i).choice(["内", "外"])
              for i, rid in enumerate(hist["前走レースID(新)"].fillna("")) if rid}
    return _history_conditions(hist, lookup), _entry_conditions(ent, lookup)


def test_conditions_equal_between_history_and_entries(paired):
    h, e = paired
    assert len(h) == len(e) == len(RACES) * 12
    for c in C.CONDITION_COLUMNS:
        assert h[c].tolist() == e[c].tolist(), c
    # 一致が空どうしの一致ばかりでないこと
    filled = {c: (h[c] != "").mean() for c in C.CONDITION_COLUMNS}
    assert all(v > 0.5 for k, v in filled.items() if k not in ("前走クワドラント", "輸送距離", "左右遷移")), filled
    assert filled["輸送距離"] > 0.3 and filled["前走クワドラント"] > 0.3
    assert set(h["牝馬限定"]) == {"牝馬限定", "混合"}
    assert set(h["間隔帯"]) >= {"連闘", "中2-3"}
    assert set(h["前走脚質"]) >= {"逃げ", "先行", "中団", "後方"}


def test_keys_equal_between_history_and_entries(paired):
    """同じ馬なら、過去側で作る全 122 variant のキーが、出馬表側の照合候補（matching.entry_fields 経由）に必ず入る。"""
    from shisu.matching import entry_fields
    h, e = paired
    h = h.assign(順位=[str(i % 12 + 1) for i in range(len(h))], 偏差値帯="51~60")
    e = e.assign(SAV_順位=h["順位"].to_numpy(), SAV_偏差値帯="51~60")
    n = 0
    fams = F.default_families()
    cands = [F.entry_key_candidates(entry_fields(row, "SAV"), [f.name for f in fams]) for row in e.to_dict("records")]
    for fam in fams:
        for sc in fam.schemes:
            keys = F.build_keys(h, fam, sc)
            for i, k in zip(keys.index, keys):
                pos = h.index.get_loc(i)
                assert (k, sc) in cands[pos].get(fam.name, []), (fam.name, sc, k)
                n += 1
    assert n > 5000


def test_entries_mare_from_race_name_or_all_entrants():
    import build_db
    rows = []
    for r, (name, sexes) in enumerate([("牝馬S", ["牡", "牝"]), ("一般", ["牝", "牝"]), ("一般", ["牝", "牡"])], 1):
        for uma, sex in enumerate(sexes, 1):
            rows.append({"レースID(新)": f"202605310502120{r}{uma:02d}", "場所": "東京", "芝ダ": "芝", "距離": "1600",
                         "馬番": str(uma), "レース名": name, "性": sex})
    out = build_db.normalize_entries_df(pd.DataFrame(rows))
    assert out["牝馬限定"].tolist() == ["牝馬限定"] * 4 + ["混合"] * 2


def test_entries_stable_from_second_column_and_weight_diff():
    """出馬表は 所属 が2列（(美) と 美浦。pandas では 所属.1）。前走斤量が無く 斤量差 だけでも増減を出す。"""
    import build_db
    raw = pd.DataFrame([{"レースID(新)": "202605310502120101", "場所": "東京", "芝ダ": "芝", "距離": "1600", "馬番": "1",
                         "所属": "", "所属.1": "栗東", "斤量": "57", "斤量差": "+1.0"}])
    out = build_db.normalize_entries_df(raw)
    assert out.loc[0, "輸送距離"] == "351-500" and out.loc[0, "斤量増減"] == "+0.5~+1.5"
