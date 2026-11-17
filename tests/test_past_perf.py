"""結果指数（SP指数・ST指数）から作る「過去3走／5走の平均・最高」の8指数（shisu/past_perf_indices.py）と、
replace・calc_entry_hensachi・/ingest への組み込み。"""
import datetime as dt
import io
import json
import random
import zipfile
from decimal import Decimal as D

import pandas as pd
import pytest

from shisu import ext_indices, indicators
from shisu import past_perf_indices as PP

RID = "レースID(新)"


def ordinal(ymd: str) -> int:
    return dt.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])).toordinal()


def run(ymd, value, surface="芝", dist=1600, baba="良", rid=None):
    return (ordinal(ymd), rid or f"{ymd}0501010101", surface, dist, baba, D(str(value)))


# ---------------------------------------------------------------- 台帳・小さな部品
def test_eight_indicators_are_registered_with_zero_as_missing():
    names = ["過去SP_3走平均", "過去SP_5走平均", "過去SP_3走最高", "過去SP_5走最高",
             "過去ST_3走平均", "過去ST_5走平均", "過去ST_3走最高", "過去ST_5走最高"]
    assert list(PP.INDICATORS) == names == list(indicators.PAST_PERF_INDICATORS)
    assert PP.INDICATORS["過去SP_3走平均"] == ("SP", 3, "avg") and PP.INDICATORS["過去ST_5走最高"] == ("ST", 5, "max")
    for n in names:
        spec = indicators.spec_for(n)
        assert spec.name == n and spec.zero_missing and not spec.reverse and not spec.zi and not spec.exclude_shinba
        assert indicators.is_known_indicator(n) and not indicators.is_discontinued(n)
        assert indicators.parse_source_name(f"全場_{n}") == ("全場", n)
        assert n in ext_indices.SUPPORTED and ext_indices.PATTERN_OF[n] == "A" and n in indicators.INDICATOR_ORDER
    assert PP.KEYS["過去SP_3走平均"] == "past_sp_avg3" and PP.KEYS["過去ST_5走最高"] == "past_st_max5"
    assert len(set(PP.KEYS.values())) == 8
    # ext_indices/past_sp_st/{key}/{日付}_{key}.csv は、その指数のファイルとして見つかる（フォルダ名 past_sp_st だけでは決めない）
    for n, key in PP.KEYS.items():
        assert ext_indices._name_indicator(f"20261115_{key}") == n and ext_indices._name_indicator(key) == n
    assert ext_indices._name_indicator("past_sp_st") is None and ext_indices._name_indicator("past_sp_avg30") is None


def test_going_age_and_name_helpers():
    assert [PP.baba3(v) for v in ("良", "稍", "稍重", "重", "不", "不良", "", "?", None, " 良 ", "稍・重")] == [
        "良", "稍・重", "稍・重", "稍・重", "不", "不", "", "", "", "良", "稍・重"]
    assert [PP.norm_baba_choice(v) for v in ("auto", "", None, "自動", "良", "稍・重", "稍重", "重", "不", "不良")] == [
        "auto", "auto", "auto", "auto", "良", "稍・重", "稍・重", "稍・重", "不", "不"]
    with pytest.raises(ValueError):
        PP.norm_baba_choice("晴")
    cases = [(("2歳", "", ""), True), (("3歳", "", ""), True), (("３歳", "", ""), True), (("3上", "", ""), False),
             (("3歳以上", "", ""), False), (("4上", "", ""), False), (("4歳以上", "", ""), False), (("3歳上", "", ""), False),
             (("3上", "未勝利", "3歳未勝利"), False),                       # 列に値があれば、その値で決める
             (("", "新馬", ""), True), (("", "未勝利", ""), True), (("", "1勝", "3歳1勝クラス"), True),
             (("", "ｵｰﾌﾟﾝ", "札幌2歳S"), True), (("", "1勝", "3歳以上1勝クラス"), False), (("", "2勝", "4歳以上2勝クラス"), False),
             (("", "ｵｰﾌﾟﾝ", "皐月賞"), False), (("", "", ""), False), ((None, None, None), False),
             (("牝", "未勝利", "3歳未勝利"), True), (("牝", "1勝", "3歳以上1勝クラス"), False),      # 年齢として読めない値は、列が空のときと同じ
             (("3才", "", ""), False), (("国際", "新馬", ""), True)]
    for args, want in cases:
        assert PP.age_limited(*args) is want, args
    assert PP.norm_horse_name(" (外)エア ジハード ") == "エアジハード" and PP.norm_horse_name("[地]○ハルウララ") == "ハルウララ"
    assert PP.norm_horse_name("ウマ") == "ウマ" and PP.norm_horse_name(None) == ""
    assert PP.norm_horse_id("2019104567") == "2019104567" and PP.norm_horse_id("2019104567.0") == "2019104567"
    assert PP.norm_horse_id("123") == "" and PP.norm_horse_id("") == ""


def test_result_text_values_and_rounding():
    text = "レースID(新),値\n202610100504030705,80\n202610100504030706,-10\n202610100504030707, 52.5 \n" \
           "202610100504030708,\n0520380101,54\n202610100504030709,abc\n２０２６１０１００５０４０３０７１０,７１\n202610100504030711,0\n"
    values, bad, examples = PP.parse_result_text(text)
    assert values == {"202610100504030705": D("80"), "202610100504030706": D("-10"), "202610100504030707": D("52.5"),
                      "202610100504030710": D("71"), "202610100504030711": D("0")}       # 負の値・0 も値。空の値は入れない
    assert bad == 2 and "0520380101" in examples[0] and "abc" in examples[1]              # 18桁でない ID・数でない値
    assert PP.parse_result_text("")[0] == {} and PP.parse_result_text("202610100504030705,80")[0] == {"202610100504030705": D("80")}
    # 依頼の例: 古い順に 65, 48, 50, 55, 52 → 新しい順 52, 55, 50, 48, 65
    agg = PP.aggregate([D(52), D(55), D(50), D(48), D(65)])
    assert agg == {(3, "avg"): "52.3", (5, "avg"): "54", (3, "max"): "55", (5, "max"): "65"}
    assert PP.aggregate([]) == {(3, "avg"): "", (5, "avg"): "", (3, "max"): "", (5, "max"): ""}
    assert PP.aggregate([D(70)]) == {(3, "avg"): "70", (5, "avg"): "70", (3, "max"): "70", (5, "max"): "70"}
    assert PP.aggregate([D(60), D(61)])[(3, "avg")] == "60.5" and PP.aggregate([D(60), D(61)])[(5, "max")] == "61"
    # 小数第2位を四捨五入（ROUND_HALF_UP。偶数丸めではない）
    assert PP.aggregate([D("1"), D("1.1")])[(3, "avg")] == "1.1"              # 1.05 → 1.1
    assert PP.aggregate([D("1.2"), D("1.3")])[(3, "avg")] == "1.3"            # 1.25 → 1.3（偶数丸めなら 1.2）
    assert PP.aggregate([D(1), D(1), D(2)])[(3, "avg")] == "1.3" and PP.aggregate([D(1), D(2), D(2)])[(3, "avg")] == "1.7"
    assert PP.aggregate([D(-10), D(-11)])[(3, "avg")] == "-10.5" and PP.aggregate([D(-10), D(-11)])[(3, "max")] == "-10"
    # 対象の走があるのに 0（丸めて 0）になる値は 0.1 にする（0 は「対象の走なし」＝欠損の印なので、区別する）
    assert PP.aggregate([D(-5), D(5)])[(3, "avg")] == "0.1" and PP.aggregate([D(0)]) == {k: "0.1" for k in PP.aggregate([D(0)])}
    assert PP.aggregate([D("0.04")])[(3, "max")] == "0.1" and PP.aggregate([D("-0.04"), D("-9")])[(5, "max")] == "0.1"
    assert PP.aggregate([D(-3)])[(3, "avg")] == "-3" and PP.aggregate([D("0.05")])[(3, "avg")] == "0.1"
    # 桁あふれ・無限大（ID が2つつながった行・1e30 など）は読めない行に数える（あとの計算を止めない）
    junk = "202601010501010101,202601010501010102202601010501010103\n202601010501010102,1e30\n202601010501010103,1e1000000\n" \
           "202601010501010104,inf\n202601010501010105,nan\n202601010501010106,1000000\n202601010501010107,999999.9\n" \
           "202601010501010108,1e3\n202601010501010109,-999999\n"
    values, bad, _ex = PP.parse_result_text(junk)
    assert bad == 6 and values == {"202601010501010107": D("999999.9"), "202601010501010108": D("1e3"), "202601010501010109": D(-999999)}
    assert PP.aggregate(list(values.values()))[(3, "avg")] == "333.6"                  # (999999.9 + 1000 − 999999) / 3
    assert [PP.fmt_value(D(v)) for v in ("54.0", "54.04", "54.05", "-0.04", "100")] == ["54", "54", "54.1", "0", "100"]


def test_result_files_are_found_in_subfolders_and_zips(tmp_path):
    root = tmp_path / "result_indices"
    (root / "2026" / "deep").mkdir(parents=True)
    a, b, c, d = "202601050501010101", "202601050501010102", "202601050501010103", "202601050501010104"
    (root / "SP01260105.csv").write_text(f"{a},80\n{b},70\n", encoding="cp932")
    (root / "2026" / "sp_lower.CSV").write_text(f"{b},71\n", encoding="utf-8-sig")              # 後のファイルが優先
    (root / "2026" / "deep" / "st_se_20260105.csv").write_text(f"{a},-10\n", encoding="utf-8")
    (root / "ST_SE_upper.csv").write_text(f"{b},33\n", encoding="cp932")
    with zipfile.ZipFile(root / "2026" / "Speed_2026.zip", "w") as z:
        z.writestr("inner/SP02260105.csv", f"{c},60\n")
        z.writestr("readme.csv", f"{d},1\n")                                                    # SP で始まらない → 使わない
        z.writestr("SP_note.txt", "x")
    with zipfile.ZipFile(root / "st_se_2026.zip", "w") as z:
        z.writestr("anything.csv", f"{c},44\n")
        z.writestr("memo.txt", "x")
    for name in ("other.csv", "st_20260105.csv", "speed_notes.txt", "SP.txt", ".hidden.csv"):
        (root / name).write_text(f"{d},99\n", encoding="utf-8")
    (root / "other.zip").write_bytes(b"not a zip")
    (root / "speed_broken.zip").write_bytes(b"not a zip")
    logs = []
    files = PP.find_result_files(root, logs.append)
    got = sorted((f.kind, f.path.name, f.member) for f in files)
    assert got == [("SP", "SP01260105.csv", ""), ("SP", "Speed_2026.zip", "inner/SP02260105.csv"), ("SP", "sp_lower.CSV", ""),
                   ("ST", "ST_SE_upper.csv", ""), ("ST", "st_se_2026.zip", "anything.csv"), ("ST", "st_se_20260105.csv", "")]
    assert any("speed_broken.zip" in m and "[WARN]" in m for m in logs)
    res = PP.load_results(files, logs.append)
    assert res["SP"] == {a: D(80), b: D(71), c: D(60)} and res["ST"] == {a: D(-10), b: D(33), c: D(44)}
    assert PP.has_result_files(root) and not PP.has_result_files(tmp_path / "nope") and PP.find_result_files(tmp_path / "nope") == []


# ---------------------------------------------------------------- 対象の走の決め方
def test_runs_must_be_strictly_before_the_race_day_and_within_600_days():
    day = ordinal("20260601")
    first = (dt.date(2026, 6, 1) - dt.timedelta(days=600)).strftime("%Y%m%d")
    before = (dt.date(2026, 6, 1) - dt.timedelta(days=601)).strftime("%Y%m%d")
    runs = [run("20260608", 99), run("20260601", 98), run("20260531", 70), run("20260101", 60), run(first, 50), run(before, 40)]
    got, stage = PP.select_runs(runs, day, "芝", 1600, "良", True)
    assert [r[5] for r in got] == [D(70), D(60), D(50)] and stage == 9          # 当日・未来・601日前は入らない。600日前は入る
    assert PP.select_runs(runs[:2], day, "芝", 1600, "良", True) == ([], 0)
    assert PP.select_runs([], day, "芝", 1600, "良", False) == ([], 0)
    # 新しい順（同じ日なら レースID の大きい順）は History が並べる
    frame = pd.DataFrame([["", "ウマ", "20260101", "202601010501010201", "東京", "芝", "1600", "良", "1", "10", ""],
                          ["", "ウマ", "20260301", "202603010501010101", "東京", "芝", "1600", "良", "2", "30", ""],
                          ["", "ウマ", "20260101", "202601010501011101", "東京", "芝", "1600", "良", "3", "20", ""]],
                         columns=PP.HISTORY_COLUMNS)
    assert [r[5] for r in PP.History(frame).runs("SP", "", "ウマ")] == [D(30), D(20), D(10)]


def test_older_horse_races_relax_the_filters_only_when_a_stage_finds_nothing():
    day = ordinal("20260601")
    full = run("20260501", 1, "芝", 1600, "良")
    full_far = run("20260420", 2, "芝", 2000, "良")              # 距離差 400 ちょうど → 入る
    other_baba = run("20260410", 3, "芝", 1800, "稍・重")
    other_surface = run("20260401", 4, "ダ", 1600, "良")
    too_far = run("20260320", 5, "芝", 2001, "良")               # 距離差 401
    far_dirt = run("20260310", 6, "ダ", 2400, "不")
    runs = [full, full_far, other_baba, other_surface, too_far, far_dirt]

    def pick(rs, **kw):
        a = {"surface": "芝", "dist": 1600, "baba": "良", "limited": False, **kw}
        got, stage = PP.select_runs(rs, day, a["surface"], a["dist"], a["baba"], a["limited"])
        return [int(r[5]) for r in got], stage
    assert pick(runs) == ([1, 2], 1)                                               # ① 2走だけでも、ここで止める
    assert pick([full] + runs[2:]) == ([1], 1)                                     # 1走でも止める
    assert pick(runs, baba="") == ([1, 2, 3], 2)                                   # 今走の馬場状態が無ければ ② から
    assert pick(runs[2:]) == ([3], 2)                                              # ② 馬場状態を緩和
    assert pick(runs[3:]) == ([4], 3)                                              # ③ 芝・ダを緩和（距離差・同じ馬場）
    assert pick(runs[3:], baba="") == ([4], 3)                                     # ③ 今走の馬場状態が無ければ距離差だけ
    assert pick([too_far, far_dirt]) == ([5], 4)                                   # ④ 同じ芝・ダ
    assert pick([far_dirt]) == ([6], 5)                                            # ⑤ 全部
    assert pick(runs, baba="不") == ([1, 2, 3], 2)                                 # ①に無い（芝で不 の走は無い）→ ②
    assert pick([other_surface, far_dirt], surface="芝", dist=1600, baba="不") == ([4, 6], 5)   # ①〜④ どれにも無い → ⑤
    # 2歳戦・3歳限定戦は絞らない
    assert pick(runs, limited=True) == ([1, 2, 3, 4, 5, 6], 9)
    # 今走の芝・ダ・距離が分からないときは、その条件の段階に当たらない
    assert pick(runs, surface="") == ([1, 2, 4], 3) and pick(runs, dist=None) == ([1, 2, 3, 5], 4)
    assert pick(runs, surface="", dist=None) == ([1, 2, 3, 4, 5, 6], 5)
    # 過去走の距離・馬場状態が分からない走は、その条件の段階に入らない
    blank = (ordinal("20260520"), "x", "芝", None, "", D(9))
    assert pick([blank, full]) == ([1], 1) and pick([blank]) == ([9], 4)


# ---------------------------------------------------------------- 履歴と計算
def base_row(date, r, uma, name, hid="", sd="芝", dist=1600, baba="良", fin=None, age="3上", cls="1勝", code="05", **more):
    return {RID: f"{date}{code}0101{r:02d}{uma:02d}", "場所": "東京", "距離": f"{sd}{dist}", "馬場状態": baba, "頭数": "12",
            "枠番": str(min(8, (uma + 1) // 2)), "馬番": str(uma), "馬名": name, "血統登録番号": hid, "騎手": f"騎手{uma}",
            "指数": str(40 + uma), "指数順位": str(uma), "着順": str(uma) if fin is None else fin, "単勝オッズ": "5.0",
            "クラス名": cls, "重量種別": "定量", "年齢限定": age, "レース名": "", **more}


def test_history_rows_keep_only_real_runs_with_a_result_index():
    rows = [base_row("20260101", 1, 1, "アルファ", "2020100001"), base_row("20260101", 1, 2, "ベータ", "", fin="取消"),
            base_row("20260101", 1, 3, "ガンマ", "", fin="除外"), base_row("20260101", 1, 4, "デルタ", "", fin="中止"),
            base_row("20260101", 1, 5, "イプシロン"), base_row("20260101", 1, 6, "", ""),
            base_row("20260101", 2, 1, "ジャンプ", sd="障", dist=3000), base_row("20260101", 3, 1, "コード", **{"トラックコード(JV)": "52"}),
            base_row("20260108", 1, 7, "(外)アルファ", "2020100001", sd="ダ", dist=1400, baba="重")]
    raw = pd.DataFrame(rows)
    results = {"SP": {r[RID]: D(50 + i) for i, r in enumerate(rows) if r["馬名"] != "イプシロン"},
               "ST": {rows[0][RID]: D("-3.50"), rows[4][RID]: D(7)}}
    h = PP.history_rows(raw, results)
    assert list(h.columns) == PP.HISTORY_COLUMNS
    assert h["馬名"].tolist() == ["アルファ", "デルタ", "イプシロン", "アルファ"]           # 取消・除外・障害・馬の分からない行は入れない。中止は走った
    assert h["馬ID"].tolist() == ["2020100001", "", "", "2020100001"] and h["日付"].tolist() == ["20260101"] * 3 + ["20260108"]
    assert h[["芝・ダ", "距離", "馬場"]].values.tolist() == [["芝", "1600", "良"]] * 3 + [["ダ", "1400", "稍・重"]]
    assert h["SP"].tolist() == ["50", "53", "", "58"] and h["ST"].tolist() == ["-3.5", "", "7", ""]
    hist = PP.History(h)
    assert [r[5] for r in hist.runs("SP", "2020100001", "")] == [D(58), D(50)]                 # 血統登録番号で
    assert [r[5] for r in hist.runs("SP", "", "アルファ")] == [D(58), D(50)]                   # 馬名で
    assert hist.runs("SP", "9999999999", "アルファ") == []                                       # 同じ馬名でも別の番号の行は別の馬
    assert hist.runs("SP", "", "イプシロン") == [] and [r[5] for r in hist.runs("ST", "", "イプシロン")] == [D(7)]
    assert hist.runs("SP", "", "") == [] and hist.runs("SP", "", "いない") == []


def test_compute_uses_only_earlier_runs_and_the_assumed_going():
    """そのレースの結果指数は、そのレースの値に使わない（未来の情報）。想定の馬場状態は芝・ダ別に指定できる。"""
    past = [base_row("20260301", 1, 1, "ウマA", sd="芝", dist=1600, baba="良"),        # SP 60
            base_row("20260315", 1, 1, "ウマA", sd="芝", dist=1800, baba="重"),        # SP 70
            base_row("20260401", 1, 1, "ウマA", sd="ダ", dist=1600, baba="良"),        # SP 80
            base_row("20260415", 1, 1, "ウマA", sd="芝", dist=1600, baba="不")]        # SP 90
    today = base_row("20260501", 1, 1, "ウマA", sd="芝", dist=1600, baba="")
    raw = pd.DataFrame(past + [today])
    results = {"SP": dict(zip(raw[RID], [D(60), D(70), D(80), D(90), D(999)])), "ST": {}}
    hist = PP.History(PP.history_rows(raw, results))
    t = PP.target_frame(raw)

    def val(name, **kw):
        return PP.compute(t, hist, **kw).values[name].tolist()
    # 馬場状態の列のある過去のレース: その日の前の走だけ・その日の馬場状態で
    assert val("過去SP_3走平均") == ["", "60", "60", "65", "73.3"]      # 2走目 = 60（①に無く②）、3走目 ダ → ③距離差・同じ馬場 = 60、
    assert val("過去SP_3走最高")[-1] == "90"                            # 4走目 不 → ② 60・70、今走（列が空）→ ② 90・70・60
    assert val("過去ST_3走平均") == [""] * 5                            # ST の値が無ければ空
    # 想定の馬場状態（芝）を指定: 列のある過去のレースも、その芝のレースは指定の値で見る
    assert val("過去SP_3走平均", baba_turf="良")[-1] == "60" and val("過去SP_5走最高", baba_turf="良")[-1] == "60"
    assert val("過去SP_3走平均", baba_turf="稍・重")[-1] == "70" and val("過去SP_3走平均", baba_turf="不")[-1] == "90"
    assert val("過去SP_3走平均", baba_dirt="不")[-1] == "73.3"                                   # ダの指定は芝のレースに効かない
    assert val("過去SP_3走平均", baba_dirt="不")[2] == "65" and val("過去SP_3走平均")[2] == "60"   # ダのレース: ①②に無く、③は指定の馬場が無ければ④…
    with pytest.raises(ValueError):
        PP.compute(t, hist, baba_turf="晴")
    # kinds で絞れる・Computed.lookups は値のある馬だけの形式A
    comp = PP.compute(t, hist, kinds=["SP"])
    assert set(comp.values) == {n for n in PP.INDICATORS if "SP" in n} and comp.hit("過去SP_3走平均") == 4
    look = comp.lookups(t[RID])["過去SP_5走平均"]
    assert look.pattern == "A" and look.ranks is None and look.files == [] and look.values[today[RID]] == "73.3"
    assert past[0][RID] not in look.values
    # 日付の読めない行は空
    bad = pd.DataFrame([{**today, RID: "xx"}])
    assert PP.compute(PP.target_frame(bad), hist).values["過去SP_3走平均"].tolist() == [""]


def test_horse_is_identified_by_id_plus_id_less_rows_of_the_same_name():
    """血統登録番号を優先する。番号の列が無い期間の行（番号が空）は、同じ馬名ならその馬の走に数える。
    同じ馬名でも別の番号の行は別の馬。番号の無い今走（出馬表など）は馬名で引く。"""
    rows = [base_row("20260101", 1, 1, "アルファ", ""), base_row("20260201", 1, 1, "アルファ", ""),          # 古い基本CSV（番号の列なし）
            base_row("20260301", 1, 1, "アルファ", "2020100001"), base_row("20260401", 1, 1, "アルファ", "2020100001"),
            base_row("20260115", 2, 2, "ベータ", "2020100002"), base_row("20260410", 2, 2, "ベータ", "2021100003")]   # 同じ馬名の別の馬
    raw = pd.DataFrame(rows)
    results = {"SP": dict(zip(raw[RID], [D(90), D(80), D(10), D(5), D(77), D(66)])), "ST": {}}
    hist = PP.History(PP.history_rows(raw, results))
    comp = PP.compute(PP.target_frame(raw), hist)
    assert comp.values["過去SP_3走平均"].tolist() == ["", "90", "85", "60", "", ""]          # 3走目 = 90・80、4走目 = 10・80・90
    assert comp.values["過去SP_5走最高"].tolist() == ["", "90", "90", "90", "", ""]          # ベータ（番号 …003）に 別の馬（…002）の 77 を使わない
    assert [r[5] for r in hist.runs("SP", "2020100001", "アルファ")] == [D(5), D(10), D(80), D(90)]
    assert [r[5] for r in hist.runs("SP", "2020100001", "")] == [D(5), D(10)]
    assert [r[5] for r in hist.runs("SP", "", "アルファ")] == [D(5), D(10), D(80), D(90)]
    assert [r[5] for r in hist.runs("SP", "", "ベータ")] == [D(66), D(77)]                    # 番号の無い今走は馬名（見分けられない）
    assert [r[5] for r in hist.runs("SP", "2021100003", "ベータ")] == [D(66)] and hist.runs("SP", "2029999999", "ベータ") == []
    # 出馬表（番号の列なし）の同じレースは、過去側と同じ値
    ent = raw.drop(columns=["血統登録番号"]).iloc[[2, 3]]
    comp2 = PP.compute(PP.target_frame(ent), hist)
    assert comp2.values["過去SP_3走平均"].tolist() == ["85", "60"]


def test_columns_with_different_names_are_read_row_by_row():
    """列の名前が違うファイルを縦につないだ表（片方の行は NaN）でも、行ごとに値のある列から読む。NaN は "nan" にしない。"""
    raw = pd.DataFrame({RID: ["202605300501010101", "202605300501010102", "202605300501010103"],
                        "馬名": ["アルファ", float("nan"), float("nan")], "馬名S": [float("nan"), "(外)ベータ", float("nan")],
                        "年齢限定": [float("nan"), float("nan"), float("nan")], "年限定": ["3歳", float("nan"), "3上"],
                        "クラス": ["1勝", "未勝利", "1勝"], "芝・ダ": ["芝", float("nan"), float("nan")],
                        "芝ダ": [float("nan"), "ダ", float("nan")], "距離": ["1600", "1400", float("nan")],
                        "血統登録番号": [float("nan"), "2021100001", float("nan")], "馬場状態": [float("nan"), "稍重", pd.NA]})
    t = PP.target_frame(raw)
    assert t["馬名"].tolist() == ["アルファ", "ベータ", ""] and t["馬ID"].tolist() == ["", "2021100001", ""]
    assert t["芝・ダ"].tolist() == ["芝", "ダ", ""] and t["馬場"].tolist() == ["", "稍・重", ""]
    assert t["限定"].tolist() == [True, True, False]
    assert t["距離"].tolist()[:2] == [1600, 1400] and pd.isna(t["距離"].iloc[2])
    assert PP.norm_horse_name(float("nan")) == "" and PP.baba3(None) == "" and PP.age_limited(float("nan"), "1勝") is False


def test_column_notes_warn_about_missing_columns():
    full = ["レースID(新)", "年齢限定", "馬場状態", "血統登録番号"]
    assert PP.column_notes(full) == [] and PP.column_notes(["年限定", "馬場", "血統登録番号"]) == []
    notes = PP.column_notes(["レースID(新)", "馬名"], "base.csv")
    assert len(notes) == 3 and notes[0].startswith("[WARN] base.csv: 年齢限定（年限定）の列が無い")
    assert "馬場状態" in notes[1] and "血統登録番号" in notes[2] and all(n.startswith("[INFO] base.csv: ") for n in notes[1:])


# ---------------------------------------------------------------- 合成データ（複数回走る馬）と、別に書いた照合用の計算
HORSES = [(f"{2019100000 + i}", f"ホース{i:02d}") for i in range(36)]
CONDS = [("東京", "05", "芝", 1600), ("東京", "05", "ダ", 1400), ("中山", "06", "芝", 2000), ("中山", "06", "ダ", 1800),
         ("東京", "05", "芝", 2400), ("中山", "06", "芝", 1200)]
AGES = [("3上", "1勝"), ("4上", "2勝"), ("3歳", "未勝利"), ("2歳", "新馬"), ("", "未勝利"), ("", "2勝")]


def make_base(seed=3, days=46, with_ids=True) -> tuple[pd.DataFrame, dict, dict]:
    rng = random.Random(seed)
    rows, sp, st = [], {}, {}
    day0 = dt.date(2024, 3, 2)
    for d in range(days):
        date = (day0 + dt.timedelta(days=13 * d)).strftime("%Y%m%d")
        for r in range(1, 4):
            place, code, sd, dist = rng.choice(CONDS)
            baba = rng.choice(["良", "良", "稍", "重", "不", ""])
            age, cls = rng.choice(AGES)
            for uma, (hid, name) in enumerate(rng.sample(HORSES, 12), 1):
                rid = f"{date}{code}0101{r:02d}{uma:02d}"
                fin = rng.choice(["取消", "除外"]) if rng.random() < 0.04 else ("中止" if rng.random() < 0.02 else str(uma))
                rows.append({RID: rid, "場所": place, "距離": f"{sd}{dist}", "馬場状態": baba, "頭数": "12",
                             "枠番": str(min(8, (uma + 1) // 2)), "馬番": str(uma), "馬名": name,
                             "血統登録番号": hid if with_ids else "", "騎手": f"騎手{uma}", "指数": str(rng.randint(40, 90)),
                             "指数順位": str(uma), "着順": fin, "単勝オッズ": f"{rng.uniform(1.5, 80):.1f}", "クラス名": cls,
                             "重量種別": "定量", "年齢限定": age, "レース名": ""})
                if rng.random() < 0.93:
                    sp[rid] = rng.randint(30, 100)
                if rng.random() < 0.8:
                    st[rid] = rng.choice([rng.randint(-10, 90), rng.randint(-10, 90) + 0.5])
    return pd.DataFrame(rows), sp, st


def oracle(base: pd.DataFrame, results: dict, kind: str, n: int, how: str, baba_turf="auto", baba_dirt="auto") -> list[str]:
    """別に書いた計算（1頭ずつ、素直に）。base の各行の値（文字列。対象の走が無ければ ""）。"""
    b3 = {"良": "良", "稍": "稍・重", "重": "稍・重", "不": "不"}
    df = base.copy()
    df["date"] = pd.to_datetime(df[RID].str[:8])
    df["sd"] = df["距離"].str[0]
    df["dist"] = df["距離"].str[1:].astype(int)
    df["baba"] = df["馬場状態"].map(lambda v: b3.get(v, ""))
    df["val"] = df[RID].map(results)
    df["ok"] = ~df["着順"].isin(["取消", "除外"]) & df["val"].notna()
    out = []
    for _, t in df.iterrows():
        mine = df[(df["馬名"] == t["馬名"]) & df["ok"] & (df["date"] < t["date"])
                  & (df["date"] >= t["date"] - pd.Timedelta(days=600))].sort_values(["date", RID], ascending=False)
        limited = t["年齢限定"] in ("2歳", "3歳") or (t["年齢限定"] == "" and t["クラス名"] in ("新馬", "未勝利"))
        baba = t["baba"]
        if t["sd"] == "芝" and baba_turf != "auto":
            baba = baba_turf
        if t["sd"] == "ダ" and baba_dirt != "auto":
            baba = baba_dirt
        if not limited and len(mine):
            near = (mine["dist"] - t["dist"]).abs() <= 400
            same = mine["sd"] == t["sd"]
            sb = mine["baba"] == baba
            steps = ([same & near & sb] if baba else []) + [same & near, near & sb if baba else near, same]
            for m in steps:
                if m.any():
                    mine = mine[m]
                    break
        vals = [D(str(v)) for v in mine["val"].tolist()[:n]]
        if not vals:
            out.append("")
            continue
        x = max(vals) if how == "max" else sum(vals) / len(vals)
        s = str(x.quantize(D("0.1"), rounding="ROUND_HALF_UP"))
        s = s[:-2] if s.endswith(".0") else s
        out.append("0.1" if s in ("0", "-0") else s)            # 対象の走がある馬の 0 は 0.1（0 は欠損の印）
    return out


def test_compute_agrees_with_an_independent_reimplementation():
    base, sp, st = make_base()
    results = {"SP": {k: D(str(v)) for k, v in sp.items()}, "ST": {k: D(str(v)) for k, v in st.items()}}
    hist = PP.History(PP.history_rows(base, results))
    t = PP.target_frame(base)
    for kw in ({}, {"baba_turf": "良", "baba_dirt": "不"}, {"baba_turf": "稍・重"}):
        comp = PP.compute(t, hist, **kw)
        for name, (kind, n, how) in PP.INDICATORS.items():
            want = oracle(base, sp if kind == "SP" else st, kind, n, how, **kw)
            got = comp.values[name].tolist()
            assert got == want, (name, kw, [(i, g, w) for i, (g, w) in enumerate(zip(got, want)) if g != w][:5])
        assert sum(comp.stages["SP"].values()) == len(base) and comp.stages["SP"].get(9, 0) > 100
        assert all(comp.stages["SP"].get(s, 0) > 0 for s in (0, 2, 3, 4, 5))
    assert PP.compute(t, hist).stages["SP"].get(1, 0) > 50
    # 血統登録番号が無くても（馬名で）同じ
    base2, _sp, _st = make_base(with_ids=False)
    comp2 = PP.compute(PP.target_frame(base2), PP.History(PP.history_rows(base2, results)))
    comp1 = PP.compute(t, hist)
    assert all(comp2.values[n].tolist() == comp1.values[n].tolist() for n in PP.INDICATORS)


# ---------------------------------------------------------------- 履歴のキャッシュ・設定
def write_sources(root, base, sp, st):
    (root / "csv_files").mkdir(parents=True, exist_ok=True)
    (root / "result_indices").mkdir(exist_ok=True)
    base.to_csv(root / "csv_files" / "全場_タイムM.csv", index=False, encoding="cp932")
    items = sorted(sp.items())
    half = len(items) // 2
    (root / "result_indices" / "SP01240302.csv").write_text("".join(f"{k},{v}\n" for k, v in items[:half]), encoding="cp932")
    with zipfile.ZipFile(root / "result_indices" / "speed_rest.zip", "w") as z:
        z.writestr("SP_rest.csv", "".join(f"{k},{v}\n" for k, v in items[half:]))
    (root / "result_indices" / "st_se_all.csv").write_text("".join(f"{k},{v}\n" for k, v in sorted(st.items())), encoding="utf-8")


def test_history_cache_is_reused_until_the_sources_change(tmp_path):
    base, sp, st = make_base(days=12)
    write_sources(tmp_path, base, sp, st)
    csv_dir, res_dir, m = tmp_path / "csv_files", tmp_path / "result_indices", tmp_path / "master_csv"
    logs = []
    h1 = PP.get_history(csv_dir, res_dir, m, logs.append)
    assert h1 is not None and (m / PP.HISTORY_FILE).exists() and any("書きました" in x for x in logs)
    meta = json.loads((m / PP.HISTORY_META).read_text(encoding="utf-8"))
    assert meta["version"] == PP.HISTORY_VERSION and meta["rows"] == h1.rows and len(meta["signature"]) == 40
    frame, sig = PP.load_history_cache(m)
    assert list(frame.columns) == PP.HISTORY_COLUMNS and len(frame) == h1.rows and sig == meta["signature"]
    assert frame["馬ID"].str.fullmatch(r"\d{10}").all()                             # 文字列のまま（先頭の 0 を落とさない）
    stamp = (m / PP.HISTORY_FILE).stat().st_mtime_ns
    logs.clear()
    h2 = PP.get_history(csv_dir, res_dir, m, logs.append)
    assert h2.rows == h1.rows and any("を使います" in x for x in logs) and (m / PP.HISTORY_FILE).stat().st_mtime_ns == stamp
    # 結果指数のファイルが増えたら作り直す
    rid = base[RID].iloc[-1]
    (res_dir / "SP_new.csv").write_text(f"{rid},88\n202912310501010101,77\n", encoding="utf-8")
    logs.clear()
    h3 = PP.get_history(csv_dir, res_dir, m, logs.append)
    assert any("作り直します" in x for x in logs) and h3.rows >= h1.rows
    assert json.loads((m / PP.HISTORY_META).read_text(encoding="utf-8"))["signature"] != sig
    # 大きさが同じまま中身だけ変わったファイル（値の訂正・馬場状態の直し）でも作り直す
    sig3 = json.loads((m / PP.HISTORY_META).read_text(encoding="utf-8"))["signature"]
    p_st = res_dir / "st_se_all.csv"
    text = p_st.read_text(encoding="utf-8")
    first = text.split("\n", 1)[0]
    rid0, val0 = first.split(",")
    new_val = "7" * len(val0) if val0 != "7" * len(val0) else "8" * len(val0)
    p_st.write_text(text.replace(first, f"{rid0},{new_val}", 1), encoding="utf-8")
    assert p_st.stat().st_size == len(text.encode("utf-8"))
    logs.clear()
    h5 = PP.get_history(csv_dir, res_dir, m, logs.append)
    assert any("作り直します" in x for x in logs) and json.loads((m / PP.HISTORY_META).read_text(encoding="utf-8"))["signature"] != sig3
    assert PP.load_history_cache(m)[0].set_index(RID).loc[rid0, "ST"] == new_val and h5.rows == h3.rows
    base_path = csv_dir / "全場_タイムM.csv"
    raw = base_path.read_bytes()
    swapped = raw.replace("良".encode("cp932"), "不".encode("cp932"), 1)
    assert len(swapped) == len(raw) and swapped != raw
    base_path.write_bytes(swapped)
    logs.clear()
    PP.get_history(csv_dir, res_dir, m, logs.append)
    assert any("作り直します" in x for x in logs)
    base_path.write_bytes(raw)
    PP.get_history(csv_dir, res_dir, m, logs.append)
    # 行数が印と違う履歴（途中で切れた・入れ替わった）は、元と違うものとして作り直す
    frame = PP.load_history_cache(m)[0]
    import gzip
    (m / PP.HISTORY_FILE).write_bytes(gzip.compress(frame.head(5).to_csv(index=False).encode("utf-8")))
    assert PP.load_history_cache(m)[1] == ""
    logs.clear()
    assert PP.get_history(csv_dir, res_dir, m, logs.append).rows == len(frame) and any("作り直します" in x for x in logs)
    # save=False なら書かない・rebuild=True なら同じ元でも作り直す
    (m / PP.HISTORY_FILE).unlink()
    assert PP.get_history(csv_dir, res_dir, m, save=False) is not None and not (m / PP.HISTORY_FILE).exists()
    logs.clear()
    PP.get_history(csv_dir, res_dir, m, logs.append)
    logs.clear()
    PP.get_history(csv_dir, res_dir, m, logs.append, rebuild=True)
    assert any("書きました" in x for x in logs)
    # 元が無くても（master_csv だけ持ってきた）、履歴のファイルがあれば使う。どちらも無ければ None
    logs.clear()
    h4 = PP.get_history(tmp_path / "nope", tmp_path / "nope2", m, logs.append)
    assert h4.rows == h3.rows and any("前に作った" in x for x in logs)
    assert PP.get_history(tmp_path / "nope", tmp_path / "nope2", tmp_path / "empty") is None
    logs.clear()
    assert PP.get_history(tmp_path / "nope", res_dir, tmp_path / "empty", logs.append) is None      # 結果指数はあるが基本CSVが無い
    assert any("[WARN]" in x and "基本CSV" in x for x in logs)
    # 壊れた履歴・版の違う履歴は使わない
    (m / PP.HISTORY_META).write_text('{"version": 0, "signature": "x"}', encoding="utf-8")
    assert PP.load_history_cache(m) is None
    (m / PP.HISTORY_META).write_text("{", encoding="utf-8")
    assert PP.load_history_cache(m)[1] == ""                                         # 印が読めなければ「元と違う」として作り直す
    (m / PP.HISTORY_FILE).write_bytes(b"broken")
    assert PP.load_history_cache(m) is None
    # 読めない履歴しか無く、作り直す元も無いときは、理由を出す（黙って8指数を落とさない）
    logs.clear()
    assert PP.get_history(tmp_path / "nope", tmp_path / "nope2", m, logs.append) is None
    assert any("[WARN]" in x and PP.HISTORY_FILE in x and "読めません" in x for x in logs)


def test_history_cache_keeps_names_that_cp932_cannot_encode(tmp_path):
    """履歴のファイルは UTF-8（cp932 で書けない文字のある馬名を落とさない。番号の無い基本CSVは馬名で引くので）。"""
    rows = [base_row("20260101", 1, 1, "ホース🐴A"), base_row("20260101", 1, 2, "ホース🐎A"),
            base_row("20260201", 1, 1, "ホース🐴A"), base_row("20260201", 1, 2, "ホース🐎A")]
    raw = pd.DataFrame(rows)
    (tmp_path / "csv_files").mkdir()
    (tmp_path / "result_indices").mkdir()
    raw.to_csv(tmp_path / "csv_files" / "全場_タイムM.csv", index=False, encoding="utf-8-sig")
    (tmp_path / "result_indices" / "SP_a.csv").write_text(f"{rows[0][RID]},80\n{rows[1][RID]},40\n", encoding="utf-8")
    m = tmp_path / "master_csv"
    assert PP.get_history(tmp_path / "csv_files", tmp_path / "result_indices", m) is not None
    frame, _sig = PP.load_history_cache(m)
    assert sorted(frame["馬名"]) == ["ホース🐎A", "ホース🐎A", "ホース🐴A", "ホース🐴A"]        # 結果指数の無い走も履歴に入る
    assert sorted(frame.loc[frame["SP"] != "", "馬名"]) == ["ホース🐎A", "ホース🐴A"]
    comp = PP.compute(PP.target_frame(raw), PP.History(frame))
    assert comp.values["過去SP_3走平均"].tolist() == ["", "", "80", "40"]


def test_base_csv_choice_and_settings_file(tmp_path, monkeypatch):
    d = tmp_path / "csv_files"
    d.mkdir()
    for name in ("主５場（中山、東京、中京、京都、阪神）_タイムM.csv", "主５場（中山、東京、中京、京都、阪神）_対戦M.csv",
                 "ローカル５場（札幌、函館、福島、新潟、小倉）_対戦M.csv", "主５場（中山、東京、中京、京都、阪神）_SAV.csv"):
        (d / name).write_text("x\n", encoding="utf-8")
    assert [p.name for p in PP.base_csv_paths(d)] == ["ローカル５場（札幌、函館、福島、新潟、小倉）_対戦M.csv",
                                                      "主５場（中山、東京、中京、京都、阪神）_タイムM.csv"]
    (d / "全場_対戦M.csv").write_text("x\n", encoding="utf-8")
    assert [p.name for p in PP.base_csv_paths(d)] == ["全場_対戦M.csv"]
    (d / "全場_タイムM.csv").write_text("x\n", encoding="utf-8")
    assert [p.name for p in PP.base_csv_paths(d)] == ["全場_タイムM.csv"] and PP.base_csv_paths(tmp_path / "nope") == []
    # 想定の馬場状態の保存
    p = tmp_path / "s.json"
    assert PP.load_settings(p) == {"baba_turf": "auto", "baba_dirt": "auto"}
    PP.save_settings("稍重", "不良", p)
    assert PP.load_settings(p) == {"baba_turf": "稍・重", "baba_dirt": "不"} and not list(tmp_path.glob("*.part"))
    p.write_text('{"baba_turf": "晴", "baba_dirt": "良"}', encoding="utf-8")
    assert PP.load_settings(p) == {"baba_turf": "auto", "baba_dirt": "良"}
    p.write_text("[1]", encoding="utf-8")
    assert PP.load_settings(p) == {"baba_turf": "auto", "baba_dirt": "auto"}
    with pytest.raises(ValueError):
        PP.save_settings("晴", "良", p)
    PP.save_settings("良", "良", p)
    with monkeypatch.context() as m:                    # 書き込みは一時ファイルから置き換える: 置き換えに失敗したら前のまま
        m.setattr(PP.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("満杯")))
        with pytest.raises(OSError):
            PP.save_settings("不", "不", p)
    assert PP.load_settings(p) == {"baba_turf": "良", "baba_dirt": "良"}
    monkeypatch.setenv("PAST_PERF_SETTINGS", str(tmp_path / "env.json"))
    monkeypatch.setenv("RESULT_INDICES_DIR", str(tmp_path / "res"))
    from shisu import config
    assert config.past_perf_settings_path() == tmp_path / "env.json" and config.result_indices_dir() == tmp_path / "res"
    assert PP.baba_text("auto", "良") == "芝 自動・ダ 良"


# ---------------------------------------------------------------- replace・calc・ingest への組み込み
def make_entries(base: pd.DataFrame, date="20260530", seed=9, baba=None) -> pd.DataFrame:
    """出馬表の形（列名が過去側と違う）。base の馬から2レース。baba を渡すと 馬場状態 の列を付ける。"""
    rng = random.Random(seed)
    rows = []
    for r, (sd, dist, age, cls) in enumerate([("芝", 1600, "3上", "1勝"), ("ダ", 1400, "3歳", "未勝利")], 1):
        for uma, (hid, name) in enumerate(rng.sample(HORSES, 12), 1):
            row = {RID: f"{date}050101{r:02d}{uma:02d}", "場 R": f"東{r}", "Ｒ": str(r), "場所": "東京", "芝ダ": sd,
                   "距離": str(dist), "外回り": "", "クラス": cls, "レース名": f"レース{r}", "年限定": age, "頭": "12",
                   "枠番": str(min(8, (uma + 1) // 2)), "馬番": str(uma), "馬名S": name, "騎手": f"騎手{uma}",
                   "マイニング値": str(rng.randint(40, 90)), "マイニング順位": str(uma)}
            if baba is not None:
                row["馬場状態"] = baba
            rows.append(row)
    return pd.DataFrame(rows)


def entries_oracle(base, results, ent, kind, n, how, baba_turf="auto", baba_dirt="auto"):
    """出馬表の行を過去側の列名に直して、oracle にかける（末尾に足して、その部分だけ返す）。"""
    add = pd.DataFrame({RID: ent[RID], "距離": ent["芝ダ"] + ent["距離"], "馬場状態": ent.get("馬場状態", ""), "馬名": ent["馬名S"],
                        "着順": "", "クラス名": ent["クラス"], "年齢限定": ent["年限定"]})
    both = pd.concat([base[[RID, "距離", "馬場状態", "馬名", "着順", "クラス名", "年齢限定"]], add], ignore_index=True)
    return oracle(both, results, kind, n, how, baba_turf, baba_dirt)[len(base):]


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def site(tmp_path_factory, monkeypatch_module):
    """基本CSV・結果指数・出馬表を置いて replace まで回した場所。"""
    root = tmp_path_factory.mktemp("pastperf")
    monkeypatch_module.setenv("SHISU_BASE_DIR", str(root))
    for k in ("RESULT_INDICES_DIR", "PAST_PERF_SETTINGS", "SHISU_EXT_DIR", "DB_PATH"):
        monkeypatch_module.delenv(k, raising=False)
    base, sp, st = make_base()
    write_sources(root, base, sp, st)
    (root / "entries_in").mkdir()
    make_entries(base).to_csv(root / "entries_in" / "shisu_20260530_1.csv", index=False, encoding="cp932")
    import replace_csv_ver3
    assert replace_csv_ver3.main(["--clean"]) == 0
    return root, base, sp, st


def read_master(root, indicator):
    return pd.read_csv(root / "master_csv" / f"master_全場_{indicator}.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)


def test_replace_writes_eight_masters_from_earlier_runs_only(site):
    root, base, sp, st = site
    assert (root / "master_csv" / PP.HISTORY_FILE).exists() and (root / "master_csv" / PP.HISTORY_META).exists()
    time_m = read_master(root, "タイムM")
    for name, (kind, n, how) in PP.INDICATORS.items():
        m = read_master(root, name)
        assert len(m) == len(base) == len(time_m) and (m["指数名"] == name).all() and (m["地域"] == "全場").all()
        want = dict(zip(base[RID], oracle(base, sp if kind == "SP" else st, kind, n, how)))
        got = dict(zip(m[RID], m["指数"]))
        assert {k: (v if v != "" else "0") for k, v in want.items()} == got, name            # 対象の走が無い馬は 0
        # 0（欠損）の馬は順位なし。欠損が2頭以上のレースは、その指数では使わない（台帳の zero_missing の決まり）
        ran = ~m["着順"].isin(["取消", "除外"])
        zero = (m["指数"] == "0") & ran
        per_race = zero.groupby(m["レースID(新/馬番無)"]).transform("sum")
        assert ((m["指数有効"] == "1") == (ran & ~zero & (per_race < 2))).all(), name
        ok = m[m["指数有効"] == "1"]
        assert len(ok) > 300 and (ok["順位"] != "").all() and (m.loc[zero, "順位"] == "").all()
        # 順位は値から（大きいほど上。同じ値は同じ順位）。基本CSVの タイムM の順位は使わない
        for _rid, g in list(ok.groupby("レースID(新/馬番無)"))[:25]:
            v = g["指数"].astype(float)
            assert g["順位"].astype(int).tolist() == v.rank(ascending=False, method="min").astype(int).tolist()
        assert (m["指数順位_元"] == "").all()
        # その馬の最初の走には値が無い（そのレースの結果を使っていない）
        first = m.sort_values(RID).groupby("馬名").head(1)
        assert (first["指数"] == "0").all()
    # 22条件・キーの部品は タイムM の master と同じ（同じ基本CSVの行）
    m = read_master(root, "過去SP_3走平均")
    for c in ("場所", "芝・ダ", "距離", "クラス名", "騎手", "着順", "単勝オッズ", "間隔帯", "距離帯"):
        assert m[c].tolist() == time_m[c].tolist(), c
    from shisu import drill
    assert drill.find_master("全場", "過去ST_5走最高").name == "master_全場_過去ST_5走最高.csv.gz"


def test_replace_precedence_and_switches(site, tmp_path, monkeypatch, capsys):
    root, base, sp, st = site
    import replace_csv_ver3 as R
    out = tmp_path / "m"
    # --no-past-perf: 8指数を作らない（履歴は、近5走の条件のために書く）
    assert R.main(["--master-dir", str(out), "--no-past-perf"]) == 0
    assert sorted(p.name for p in out.glob("master_*")) == ["master_全場_タイムM.csv.gz"] and (out / PP.HISTORY_FILE).exists()
    # 結果指数のフォルダが空・無い: 作らない
    out2 = tmp_path / "m2"
    assert R.main(["--master-dir", str(out2), "--result-dir", str(tmp_path / "nope")]) == 0
    assert sorted(p.name for p in out2.glob("master_*")) == ["master_全場_タイムM.csv.gz"]
    # ext_indices/past_sp_st（/ingest の出力）があっても、過去側は計算した値。csv_files に同じ名前のファイルがあれば、そちら
    ext = tmp_path / "ext" / "past_sp_st" / "past_sp_avg3"
    ext.mkdir(parents=True)
    (ext / "20240302_past_sp_avg3.csv").write_text("".join(f"{r},999\n" for r in base[RID]), encoding="utf-8")
    csv2 = tmp_path / "csv_files"
    csv2.mkdir()
    base.to_csv(csv2 / "全場_タイムM.csv", index=False, encoding="cp932")
    own = base.copy()
    own["指数"] = "77"
    own.loc[own["馬番"] == "1", "指数"] = "88"
    own.to_csv(csv2 / "全場_過去ST_5走最高.csv", index=False, encoding="cp932")
    out3 = tmp_path / "m3"
    capsys.readouterr()
    assert R.main(["--csv-dir", str(csv2), "--master-dir", str(out3), "--ext-dir", str(tmp_path / "ext"), "--no-raw-merge"]) == 0
    log = capsys.readouterr().out
    assert "過去SP_3走平均: 過去走の結果指数から計算した値を使います（ext_indices のファイルは使いません）" in log
    assert "全場_過去ST_5走最高: csv_files のファイルを使います（過去走の結果指数からは計算しません）" in log
    m = pd.read_csv(out3 / "master_全場_過去SP_3走平均.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert "999" not in set(m["指数"]) and m["指数"].tolist() == read_master(root, "過去SP_3走平均")["指数"].tolist()
    own_m = pd.read_csv(out3 / "master_全場_過去ST_5走最高.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert set(own_m["指数"]) == {"77", "88"}
    assert len(list(out3.glob("master_全場_過去*.csv.gz"))) == 8
    # 結果指数が無く ext_indices のファイルだけなら、ほかの外部指数と同じに ext から付ける
    out4 = tmp_path / "m4"
    assert R.main(["--csv-dir", str(csv2), "--master-dir", str(out4), "--ext-dir", str(tmp_path / "ext"), "--no-raw-merge",
                   "--result-dir", str(tmp_path / "nope")]) == 0
    m4 = pd.read_csv(out4 / "master_全場_過去SP_3走平均.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert set(m4["指数"]) == {"999"}
    # 同じプロセスで、結果指数を足してもう一度回すと、新しい値の master になる（前の実行の計算を使い回さない）
    res2 = tmp_path / "res2"
    res2.mkdir()
    for p in (root / "result_indices").iterdir():
        (res2 / p.name).write_bytes(p.read_bytes())
    out6 = tmp_path / "m6"
    assert R.main(["--master-dir", str(out6), "--result-dir", str(res2)]) == 0
    before = pd.read_csv(out6 / "master_全場_過去SP_5走最高.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert before["指数"].tolist() == read_master(root, "過去SP_5走最高")["指数"].tolist()
    (res2 / "spz_all.csv").write_text("".join(f"{r},101\n" for r in base[RID]), encoding="utf-8")     # 全部の走を 101 に
    assert R.main(["--master-dir", str(out6), "--result-dir", str(res2)]) == 0
    after = pd.read_csv(out6 / "master_全場_過去SP_5走最高.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert set(after["指数"]) == {"0", "101"} and (after["指数"] == "101").sum() >= (before["指数"] != "0").sum()
    # 履歴を作る基本CSVが読めなくても、ほかの master は作る（8指数だけ作らず、終了コード1）
    csv3 = tmp_path / "csv3"
    csv3.mkdir()
    (csv3 / "全場_タイムM.csv").write_bytes(b"")
    base.to_csv(csv3 / "東京_対戦M.csv", index=False, encoding="cp932")
    out7 = tmp_path / "m7"
    capsys.readouterr()
    assert R.main(["--csv-dir", str(csv3), "--master-dir", str(out7), "--no-raw-merge", "--no-ext"]) == 1
    log = capsys.readouterr().out
    assert "[ERROR] 過去SP・ST の履歴: " in log and "過去SP・ST の8指数は作りません" in log
    assert [p.name for p in out7.glob("master_*")] == ["master_東京_対戦M.csv.gz"]
    # 並列でも同じ master
    out5 = tmp_path / "m5"
    assert R.main(["--master-dir", str(out5), "--workers", "2"]) == 0
    for name in PP.INDICATORS:
        assert (out5 / f"master_全場_{name}.csv.gz").read_bytes() == (root / "master_csv" / f"master_全場_{name}.csv.gz").read_bytes()


def read_merged(root):
    return pd.read_csv(root / "entries_out" / "entries_merged_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False)


def test_calc_attaches_the_eight_indices_with_the_assumed_going(site, capsys, monkeypatch):
    root, base, sp, st = site
    import calc_entry_hensachi as C
    ent = make_entries(base)
    capsys.readouterr()
    assert C.main(["--merge-only", "--clean"]) == 0
    log = capsys.readouterr().out
    assert "過去SP・ST: 想定の馬場状態 芝 自動・ダ 自動・24 頭" in log and "想定の馬場状態は 既定 から" in log
    assert "外部指数 過去SP_3走平均 を 過去走の結果指数から計算して付けました（値のある馬" in log
    out = read_merged(root)
    for name, (kind, n, how) in PP.INDICATORS.items():
        want = entries_oracle(base, sp if kind == "SP" else st, ent, kind, n, how)
        assert out[name].tolist() == [v or "0" for v in want], name
        for sfx in ("_偏差値", "_偏差値帯", "_順位", "_順位帯_s1", "_順位帯_s2"):
            assert name + sfx in out.columns
    r1 = out[out["Ｒ"] == "1"]
    v = r1["過去SP_3走平均"].astype(float)
    if (v == 0).sum() < 2:                                                    # 欠損が2頭以上ならそのレースは使わない
        ok = r1[v != 0]
        assert ok["過去SP_3走平均_順位"].astype(int).tolist() == v[v != 0].rank(ascending=False, method="min").astype(int).tolist()
    # ext_indices/past_sp_st（/ingest の出力）に同じ馬の値があっても、計算した値を使う（全部の計算でも、--indicators の差し替えでも）
    ext = root / "ext_indices" / "past_sp_st" / "past_sp_avg3"
    ext.mkdir(parents=True)
    (ext / "20260530_past_sp_avg3.csv").write_text("".join(f"{r},999\n" for r in ent[RID]), encoding="utf-8")
    assert C.main(["--merge-only", "--clean"]) == 0
    assert read_merged(root)["過去SP_3走平均"].tolist() == out["過去SP_3走平均"].tolist()
    assert C.main(["--indicators", "過去SP_3走平均"]) == 0
    assert read_merged(root)["過去SP_3走平均"].tolist() == out["過去SP_3走平均"].tolist()
    assert C.main(["--merge-only", "--clean", "--no-past-perf"]) == 0             # 計算しないときは ext のファイルの値
    assert set(read_merged(root)["過去SP_3走平均"]) == {"999"}
    import shutil
    shutil.rmtree(root / "ext_indices")
    capsys.readouterr()
    # 想定の馬場状態を指定（芝・ダ 別）
    assert C.main(["--merge-only", "--clean", "--baba-turf", "良", "--baba-dirt", "不良"]) == 0
    log = capsys.readouterr().out
    assert "想定の馬場状態 芝 良・ダ 不・24 頭" in log and "想定の馬場状態は 引数 から" in log
    out2 = read_merged(root)
    want = entries_oracle(base, sp, ent, "SP", 3, "avg", "良", "不")
    assert out2["過去SP_3走平均"].tolist() == [v or "0" for v in want]
    assert out2["過去SP_3走平均"].tolist() != out["過去SP_3走平均"].tolist()          # 芝の古馬戦は ① の走に変わる
    assert out2[out2["Ｒ"] == "2"]["過去SP_3走平均"].tolist() == out[out["Ｒ"] == "2"]["過去SP_3走平均"].tolist()   # 3歳限定は同じ
    # 省いたほうは設定ファイル（/ingest の画面で選んだ値）
    PP.save_settings("稍・重", "auto")
    assert C.main(["--merge-only", "--clean"]) == 0
    log = capsys.readouterr().out
    assert "想定の馬場状態 芝 稍・重・ダ 自動" in log and "想定の馬場状態は 設定ファイル・既定 から" in log
    want = entries_oracle(base, sp, ent, "SP", 3, "avg", "稍・重")
    assert read_merged(root)["過去SP_3走平均"].tolist() == [v or "0" for v in want]
    assert C.main(["--merge-only", "--clean", "--baba-turf", "auto"]) == 0             # 引数が設定ファイルより先
    assert read_merged(root)["過去SP_3走平均"].tolist() == out["過去SP_3走平均"].tolist()
    (root / "past_perf_settings.json").unlink()
    # 知らない値は終了コード2（何も書かない）
    assert C.main(["--merge-only", "--baba-turf", "晴"]) == 2
    # --no-past-perf: 付けない。--mining-only: 履歴を読まない（ほかの指数の列は前のまま）
    assert C.main(["--merge-only", "--clean", "--no-past-perf"]) == 0
    assert not any(c.startswith("過去") for c in read_merged(root).columns)
    assert C.main(["--merge-only", "--clean"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(PP, "get_history", lambda *a, **k: (_ for _ in ()).throw(AssertionError("履歴を読んだ")))
    assert C.main(["--mining-only"]) == 0
    assert read_merged(root)["過去SP_3走平均"].tolist() == out["過去SP_3走平均"].tolist()
    monkeypatch.undo()
    # --indicators で 過去SP の1指数だけ差し替える（想定の馬場状態を変えて）
    monkeypatch.setenv("SHISU_BASE_DIR", str(root))
    assert C.main(["--indicators", "過去SP_3走平均", "--baba-turf", "良", "--baba-dirt", "不"]) == 0
    part = read_merged(root)
    assert part["過去SP_3走平均"].tolist() == out2["過去SP_3走平均"].tolist()
    assert part["過去SP_3走平均_順位"].tolist() == out2["過去SP_3走平均_順位"].tolist()
    assert part["過去SP_5走平均"].tolist() == out["過去SP_5走平均"].tolist()             # ほかの指数は前のまま
    assert C.main(["--merge-only", "--clean"]) == 0


def test_entries_side_agrees_with_the_past_side_for_the_same_race(site, tmp_path, monkeypatch):
    """過去の1開催日を出馬表の形にして計算すると、master（過去側）と値・順位・偏差値・帯が同じ（同じ馬場状態なら）。"""
    root, base, sp, st = site
    import calc_entry_hensachi as C
    date = sorted(base[RID].str[:8].unique())[-3]
    day = base[base[RID].str[:8] == date]
    ent = pd.DataFrame({RID: day[RID], "場 R": "東" + day[RID].str[14:16].astype(int).astype(str), "Ｒ": day[RID].str[14:16],
                        "場所": day["場所"], "芝ダ": day["距離"].str[0], "距離": day["距離"].str[1:], "外回り": "",
                        "クラス": day["クラス名"], "レース名": "", "年限定": day["年齢限定"], "頭": "12", "枠番": day["枠番"],
                        "馬番": day["馬番"], "馬名S": day["馬名"], "騎手": day["騎手"], "馬場状態": day["馬場状態"],
                        "血統登録番号": day["血統登録番号"], "マイニング値": day["指数"], "マイニング順位": day["指数順位"]})
    ent = ent[~day["着順"].isin(["取消", "除外"]).to_numpy()]                    # 過去側は取消・除外の馬を計算に入れない
    ein, eout = tmp_path / "in", tmp_path / "out"
    ein.mkdir()
    ent.to_csv(ein / "e.csv", index=False, encoding="cp932")
    assert C.main(["--in-dir", str(ein), "--out-dir", str(eout), "--merge-only"]) == 0
    got = pd.read_csv(eout / "entries_merged_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False).set_index(RID)
    n_checked = 0
    for name in PP.INDICATORS:
        m = read_master(root, name).set_index(RID).loc[got.index]
        assert got[name].tolist() == m["指数"].tolist(), name
        ok = m["指数有効"] == "1"
        n_checked += int(ok.sum())
        for a, b in ((f"{name}_順位", "順位"), (f"{name}_偏差値", "偏差値"), (f"{name}_偏差値帯", "偏差値帯"), (f"{name}_順位帯_s1", "順位帯_s1")):
            assert got.loc[ok.to_numpy(), a].tolist() == m.loc[ok, b].tolist(), (name, a)
        assert (got.loc[~ok.to_numpy(), f"{name}_順位帯_s1"] == "").all()             # 過去側で使わないレース・馬は、出馬表側でも帯なし
    assert n_checked > 100


def test_calc_rebuilds_a_stale_history_and_works_from_the_cache_alone(site, tmp_path, capsys):
    root, base, sp, st = site
    import calc_entry_hensachi as C
    ent = make_entries(base)
    ein = tmp_path / "in"
    ein.mkdir()
    ent.to_csv(ein / "e.csv", index=False, encoding="cp932")
    master = tmp_path / "master"
    master.mkdir()
    for f in (PP.HISTORY_FILE, PP.HISTORY_META):
        (master / f).write_bytes((root / "master_csv" / f).read_bytes())
    # 履歴のファイルだけ（基本CSV・結果指数の無い場所）でも計算できる
    args = ["--in-dir", str(ein), "--out-dir", str(tmp_path / "o1"), "--merge-only", "--master-dir", str(master)]
    assert C.main(args + ["--csv-dir", str(tmp_path / "nope"), "--result-dir", str(tmp_path / "nope")]) == 0
    a = pd.read_csv(tmp_path / "o1" / "entries_merged_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False)
    assert a["過去SP_3走平均"].tolist() == [v or "0" for v in entries_oracle(base, sp, ent, "SP", 3, "avg")]
    assert "前に作った" in capsys.readouterr().out
    # 結果指数が増えたら（先週の結果を足した）、履歴を作り直して新しい値で計算する
    res = tmp_path / "res"
    res.mkdir()
    for p in (root / "result_indices").iterdir():
        (res / p.name).write_bytes(p.read_bytes())
    target = ent["馬名S"].iloc[12]                                                   # 3歳限定のレースの馬（600日以内の全走を使う）
    last = base[base["馬名"] == target].sort_values(RID).iloc[-1][RID]
    (res / "spz_fix.csv").write_text(f"{last},100.5\n", encoding="utf-8")           # その馬の最後の走の値を上書き（名前の順で後のファイルが優先）
    sp2 = {**sp, last: 100.5}
    assert C.main(args[:3] + [str(tmp_path / "o2"), "--merge-only", "--master-dir", str(master), "--result-dir", str(res)]) == 0
    assert "作り直します" in capsys.readouterr().out
    b = pd.read_csv(tmp_path / "o2" / "entries_merged_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False)
    assert b["過去SP_5走最高"].tolist() == [v or "0" for v in entries_oracle(base, sp2, ent, "SP", 5, "max")]
    assert b["過去SP_5走最高"].iloc[12] == "100.5" and a["過去SP_5走最高"].iloc[12] != "100.5"
    # 結果指数も履歴も無ければ、8指数の列は付かない（ほかの処理はそのまま）
    assert C.main(args[:3] + [str(tmp_path / "o3"), "--merge-only", "--master-dir", str(tmp_path / "none"),
                              "--csv-dir", str(tmp_path / "nope"), "--result-dir", str(tmp_path / "nope")]) == 0
    c = pd.read_csv(tmp_path / "o3" / "entries_merged_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False)
    assert not any(col.startswith("過去") for col in c.columns) and "タイムM_順位" in c.columns


def test_ingest_source_writes_target_files_copies_to_ext_and_saves_the_going(site, tmp_path):
    root, base, sp, st = site
    from shisu.ingest import IngestRequest, SOURCES, common, run_ingest, validate
    assert "past" in SOURCES and not SOURCES["past"].scrape and SOURCES["past"].needs == "base"
    assert any("ベースCSV" in e for e in validate(IngestRequest(sources=["past"])))
    assert any("想定馬場状態（芝）" in e for e in validate(IngestRequest(sources=["past"], base_paths=[tmp_path], baba_turf="晴")))
    ent = make_entries(base)
    path = root / "entries_in" / "shisu_20260530_1.csv"
    logs = []
    ext = tmp_path / "ext"
    req = IngestRequest(sources=["past"], base_paths=[path], ext_dir=ext, baba_turf="良", baba_dirt="稍重")
    res = run_ingest(req, tmp_path / "out", logs.append)["past"]
    assert isinstance(res, common.Outputs), logs
    for name, key in PP.KEYS.items():
        kind, n, how = PP.INDICATORS[name]
        want = entries_oracle(base, sp if kind == "SP" else st, ent, kind, n, how, "良", "稍・重")
        f = tmp_path / "out" / "past_sp_st" / key / f"20260530_{key}.csv"
        got = pd.read_csv(f, encoding="cp932", dtype=str, keep_default_na=False)
        assert list(got.columns) == [RID, "ワーク1"] and got[RID].tolist() == ent[RID].tolist() and got["ワーク1"].tolist() == want
        assert (tmp_path / "out" / "past_sp_st" / key / f"shisu_20260530_1_{key}_merged.csv").exists()
        assert (ext / "past_sp_st" / key / f"20260530_{key}.csv").read_bytes() == f.read_bytes()      # ext_indices にも
    assert len(res.ext_files) == 8 and any("想定の馬場状態 芝 良・ダ 稍・重・24 頭" in m for m in logs)
    assert PP.load_settings() == {"baba_turf": "良", "baba_dirt": "稍・重"} and any("past_perf_settings.json に保存" in n for n in res.notes)
    # 置いたファイルは ext_indices の形式A として、その指数に振り分けられる
    sources, skipped = ext_indices.discover(ext, log=lambda m: None)
    assert set(sources) == set(PP.INDICATORS) and not skipped
    look = ext_indices.load_source(sources["過去SP_3走平均"], log=lambda m: None)
    assert look.values[ent[RID].iloc[0]] == entries_oracle(base, sp, ent, "SP", 3, "avg", "良", "稍・重")[0]
    (root / "past_perf_settings.json").unlink()
    # 馬名の無いベースCSV・結果指数の無い場所は、この取得元だけ失敗（理由つき）
    thin = tmp_path / "thin.csv"
    ent[[RID, "枠番", "馬番"]].to_csv(thin, index=False, encoding="cp932")
    out = run_ingest(IngestRequest(sources=["past"], base_paths=[thin]), tmp_path / "out2", logs.append)["past"]
    assert isinstance(out, str) and "馬名" in out
    assert not (root / "past_perf_settings.json").exists()                          # 失敗した実行は想定の馬場状態を保存しない
    assert any("想定馬場状態（ダ）" in e for e in validate(IngestRequest(sources=["past"], base_paths=[path], baba_dirt="晴")))
    import ingest as cli
    assert cli.main(["--sources", "past", "--base", str(path), "--baba-turf", "不", "--out", str(tmp_path / "cli"),
                     "--run-id", "r1", "--no-ext-copy"]) == 0
    assert PP.load_settings() == {"baba_turf": "不", "baba_dirt": "auto"}
    assert cli.main(["--sources", "past", "--base", str(path), "--out", str(tmp_path / "cli"), "--run-id", "r2",
                     "--no-ext-copy"]) == 0
    assert PP.load_settings() == {"baba_turf": "不", "baba_dirt": "auto"}             # 省いたら前に保存した値
    a = (tmp_path / "cli" / "r1" / "past_sp_st" / "past_sp_avg3" / "20260530_past_sp_avg3.csv").read_bytes()
    assert a == (tmp_path / "cli" / "r2" / "past_sp_st" / "past_sp_avg3" / "20260530_past_sp_avg3.csv").read_bytes()
    assert cli.main(["--sources", "past", "--base", str(path), "--baba-turf", "晴", "--out", str(tmp_path / "cli")]) == 2
    (root / "past_perf_settings.json").unlink()


def read_target(path):
    return pd.read_csv(path, encoding="cp932", dtype=str, keep_default_na=False)


def test_ingest_source_with_mixed_bases_reruns_and_failures(site, tmp_path, monkeypatch):
    root, base, sp, st = site
    from shisu.ingest import IngestRequest, common, run_ingest
    settings = root / "past_perf_settings.json"
    assert not settings.exists()
    ent = make_entries(base)
    ent.loc[ent["Ｒ"] == "2", "クラス"] = "1勝"            # 3歳限定の1勝クラス（年限定 の列でしか見分けられない）

    def run_past(paths, out, **kw):
        logs = []
        res = run_ingest(IngestRequest(sources=["past"], base_paths=list(paths), **kw), tmp_path / out, logs.append)["past"]
        return res, logs

    def target(out, key="past_sp_avg3"):
        return read_target(tmp_path / out / "past_sp_st" / key / f"20260530_{key}.csv")

    def csv(df, name):
        df.to_csv(tmp_path / name, index=False, encoding="cp932")
        return tmp_path / name
    want = entries_oracle(base, sp, ent, "SP", 3, "avg")
    want_st = entries_oracle(base, st, ent, "ST", 5, "max")
    # (1) 列の名前が違う2本のベースCSV（馬名／馬名S・年齢限定／年限定・芝・ダ／芝ダ）: どちらの行も自分の列から読む
    a = csv(ent[ent["Ｒ"] == "1"].rename(columns={"馬名S": "馬名", "年限定": "年齢限定", "芝ダ": "芝・ダ"}), "a.csv")
    b = csv(ent[ent["Ｒ"] == "2"], "b.csv")
    res, logs = run_past([a, b], "o1")
    assert isinstance(res, common.Outputs), logs
    got = target("o1")
    assert got[RID].tolist() == ent[RID].tolist() and got["ワーク1"].tolist() == want
    assert target("o1", "past_st_max5")["ワーク1"].tolist() == want_st
    assert sum(v != "" for v in want[12:]) >= 6                                      # 2本目の行にも値が付いている
    assert not any("年齢限定（年限定）の列が無い" in m for m in logs)
    # (2) レース単位（16桁）の レースID(新): 馬番を付けた18桁で書く（1レース1頭に潰さない）
    e16 = ent.copy()
    e16[RID] = e16[RID].str[:16]
    res, logs = run_past([csv(e16, "e16.csv")], "o2")
    assert isinstance(res, common.Outputs), logs
    got = target("o2")
    assert got[RID].tolist() == ent[RID].tolist() and got["ワーク1"].tolist() == want
    settings.unlink()
    # (3) ext_indices へのコピー: 今回のファイルにある馬は空欄でも今回の値（前の値を残さない）。今回に無い馬は残す
    new_rid, other = "202605300501010113", "202605300501010399"
    plus = pd.concat([ent, pd.DataFrame([{**ent.iloc[0].to_dict(), RID: new_rid, "馬番": "13", "馬名S": "ハジメテノウマ"}])],
                     ignore_index=True)
    ext = tmp_path / "ext"
    old = ext / "past_sp_st" / "past_sp_avg3" / "20260530_past_sp_avg3.csv"
    old.parent.mkdir(parents=True)
    old.write_text(f"{RID},ワーク1\n" + "".join(f"{r},999\n" for r in [*ent[RID], new_rid, other]), encoding="cp932")
    res, logs = run_past([csv(plus, "plus.csv")], "o3", ext_dir=ext)
    assert isinstance(res, common.Outputs), logs
    got = read_target(old)
    vals = dict(zip(got[RID], got["ワーク1"]))
    assert [vals[r] for r in ent[RID]] == want and vals[new_rid] == "" and vals[other] == "999"
    cleared = 1 + sum(v == "" for v in want)
    assert any(f"今回は値の無い {cleared} 頭の前の値を消しました" in m for m in logs)
    assert not any("前の値を残しました" in m for m in logs)
    settings.unlink()
    # (4) 芝ダ・距離 の無いベースCSV: 警告して、古馬戦でも600日以内の全走（段階⑤）。列の注意書きはファイルの名前つき
    res, logs = run_past([csv(ent[[RID, "枠番", "馬番", "馬名S"]], "names.csv")], "o4")
    assert isinstance(res, common.Outputs), logs
    assert any("[WARN]" in m and "芝ダ・距離 の列が無い" in m for m in logs)
    assert any("ベースCSV names.csv: 年齢限定（年限定）の列が無い" in m for m in logs)
    everything = entries_oracle(base, sp, ent.assign(年限定="3歳"), "SP", 3, "avg")        # 限定戦の数え方 = 窓の中の全走
    assert target("o4")["ワーク1"].tolist() == everything and everything != want
    settings.unlink()
    # (5) 設定を保存できなくても（読み取り専用の場所など）取得元は成功のまま・警告だけ
    with monkeypatch.context() as m:
        m.setattr(PP, "save_settings", lambda *a, **k: (_ for _ in ()).throw(PermissionError("読み取り専用")))
        res, logs = run_past([b], "o5")
    assert isinstance(res, common.Outputs) and len(res.files) == 16
    assert any("[WARN]" in x and "保存できませんでした" in x for x in logs) and not any("保存しました" in n for n in res.notes)
    assert not settings.exists()
    # (6) 結果指数も履歴も無い場所: この取得元は失敗（理由つき）・CLI は終了コード1・設定は保存しない
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("SHISU_BASE_DIR", str(empty))
    res, logs = run_past([b], "o6")
    assert isinstance(res, str) and "結果指数" in res and not (tmp_path / "o6" / "past_sp_st" / "past_sp_avg3").exists()
    import ingest as cli
    assert cli.main(["--sources", "past", "--base", str(b), "--out", str(tmp_path / "cli6"), "--no-ext-copy"]) == 1
    assert not (empty / "past_perf_settings.json").exists()
    monkeypatch.setenv("SHISU_BASE_DIR", str(root))
    assert not settings.exists()


def test_ingest_form_has_the_going_selects_and_runs_the_source(site, monkeypatch):
    root, base, sp, st = site
    import re
    import app as am
    from shisu.ingest import web
    monkeypatch.setattr(web, "JOBS", {})
    monkeypatch.setattr(web, "_RESERVED", set())
    c = am.app.test_client()
    page = c.get("/ingest").get_data(as_text=True)
    assert "想定馬場状態（過去SP・ST指数用）" in page and "過去SP・ST指数（結果指数から過去3走/5走の平均・最高を計算）" in page
    for field, label in (("baba_turf", "芝"), ("baba_dirt", "ダ")):
        sel = re.search(r'<label>%s</label><select name="%s"[^>]*>(.*?)</select>' % (label, field), page, re.S).group(1)
        opts = re.findall(r'<option value="([^"]*)"\s*(selected)?\s*>([^<]*)</option>', sel)
        assert [(v, t) for v, _s, t in opts] == [("auto", "自動（出馬表の列／無ければ指定なし）"), ("良", "良"),
                                                 ("稍・重", "稍・重"), ("不", "不")]
        assert [v for v, s, _t in opts if s] == ["auto"]
    ent_bytes = (root / "entries_in" / "shisu_20260530_1.csv").read_bytes()

    def post(**kw):
        data = {"src": ["past"], "base_files": [(io.BytesIO(ent_bytes), "出馬表.csv")], **kw}
        return c.post("/ingest/run", data=data, content_type="multipart/form-data")
    r = post(baba_turf="晴")
    assert r.status_code == 400 and "想定馬場状態（芝）は 自動 / 良 / 稍・重 / 不 のどれか" in r.get_data(as_text=True)
    assert not (root / "past_perf_settings.json").exists()                          # 受け付けなかった POST は保存しない
    # 想定馬場状態は正しくても、ほかの理由で受け付けなかった POST（ベースCSVなし）・失敗したジョブ（馬名の無いベースCSV）は保存しない
    r = c.post("/ingest/run", data={"src": ["past"], "baba_turf": "良", "baba_dirt": "不"}, content_type="multipart/form-data")
    assert r.status_code == 400 and not (root / "past_perf_settings.json").exists()
    thin = make_entries(base)[[RID, "枠番", "馬番"]].to_csv(index=False).encode("cp932")
    r = c.post("/ingest/run", data={"src": ["past"], "baba_turf": "良", "baba_dirt": "不",
                                    "base_files": [(io.BytesIO(thin), "thin.csv")]}, content_type="multipart/form-data")
    assert r.status_code == 302
    job = web.JOBS[r.headers["Location"].rstrip("/").split("/")[-1]]
    job.thread.join(timeout=60)
    assert job.status == "error" and any("馬名" in line for line in job.lines), (job.status, list(job.lines))
    assert not (root / "past_perf_settings.json").exists()
    monkeypatch.setattr(web, "JOBS", {})
    r = post(baba_turf="稍・重", baba_dirt="不")
    assert r.status_code == 302
    run_id = r.headers["Location"].rstrip("/").split("/")[-1]
    job = web.JOBS[run_id]
    job.thread.join(timeout=60)
    assert job.status == "done", list(job.lines)
    assert any("想定馬場状態（過去SP・ST指数用）: 芝 稍・重・ダ 不" in line for line in job.lines)
    page = c.get(f"/ingest/job/{run_id}").get_data(as_text=True)
    assert "past_sp_st/past_sp_avg3/20260530_past_sp_avg3.csv" in page and "past_perf_settings.json に保存" in page
    ent = make_entries(base)
    f = root / "ext_indices" / "past_sp_st" / "past_st_max5" / "20260530_past_st_max5.csv"
    got = pd.read_csv(f, encoding="cp932", dtype=str, keep_default_na=False)
    assert got["ワーク1"].tolist() == entries_oracle(base, st, ent, "ST", 5, "max", "稍・重", "不")
    # 選んだ値は保存され、次に開いたフォームで選ばれている。calc_entry_hensachi の既定にもなる
    page = c.get("/ingest").get_data(as_text=True)
    assert re.search(r'<select name="baba_turf".*?<option value="稍・重" selected', page, re.S)
    assert re.search(r'<select name="baba_dirt".*?<option value="不" selected', page, re.S)
    import calc_entry_hensachi as C
    assert C.resolve_baba(None, None) == ("稍・重", "不", "設定ファイル") and C.resolve_baba("良", None) == ("良", "不", "引数・設定ファイル")
    # フォームを経ない POST（想定馬場状態なし）は、保存してある値
    monkeypatch.setattr(web, "JOBS", {})
    r = c.post("/ingest/run", data={"src": ["past"], "base_files": [(io.BytesIO(ent_bytes), "b.csv")]},
               content_type="multipart/form-data")
    job = web.JOBS[r.headers["Location"].rstrip("/").split("/")[-1]]
    job.thread.join(timeout=60)
    assert job.status == "done" and job.req.baba_turf == "稍・重" and job.req.baba_dirt == "不"
    for p in (root / "past_perf_settings.json",):
        p.unlink()
    import shutil
    shutil.rmtree(root / "ext_indices")
    shutil.rmtree(root / "ingest_out")
    shutil.rmtree(root / "ingest_in", ignore_errors=True)
