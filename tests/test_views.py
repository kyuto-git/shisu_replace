"""画面の表示の規則（条件ヒット馬の「通常」の重複排除・並び・表示上限・穴の軸の強調、指数クロス）。DB を使わない関数単位。
ページとしての確認（独立スクロール・見出しの固定・折りたたみ）は tests/test_pipeline.py。"""
import pytest

import app

KEY = "東京×芝×1600×定量×1・2_勝×1"
STATS = {   # 区分が決まる成績（shisu/classify.py の規則。件数は別に渡す）
    "abs": {"連対率_val": 85.0, "複勝率_val": 90.0, "掲示板率_val": 95.0, "複勝回収値": 120.0, "単勝適正回収値": 90.0},
    "axis": {"連対率_val": 55.0, "複勝率_val": 82.0, "掲示板率_val": 92.0, "複勝回収値": 80.0, "単勝適正回収値": 80.0},
    "opp": {"連対率_val": 40.0, "複勝率_val": 62.0, "掲示板率_val": 72.0, "複勝回収値": 80.0, "単勝適正回収値": 80.0},
    "keshi": {"連対率_val": 1.0, "複勝率_val": 3.0, "掲示板率_val": 8.0, "複勝回収値": 10.0, "単勝適正回収値": 10.0},
}


def hit(uma, base, mode, cnt, cat, family="normal", key=KEY, scheme="", region="主5場", **more):
    h = {"馬番": str(uma), "枠番": "1", "馬名": f"馬{uma}", "騎手": "騎手", "base": base, "family": family,
         "rank_scheme": scheme, "odds_mode": mode, "region": region, "key": key, "件数": cnt,
         "テーブル名": f"foragg_{mode}_{family}_{scheme}_{region}_{base}", **STATS[cat], **more}
    assert app.hit_category(h) == cat, (cat, cnt, mode)
    return h


def test_stats_presets_give_the_intended_category():
    for cat, cnt in (("abs", 12), ("axis", 22), ("opp", 40), ("keshi", 25)):
        for mode in ("normal", "odds10u", "odds10_50", "odds50p"):
            hit(1, "タイムM", mode, cnt, cat)


def test_hit_category_passes_the_key_for_rare_conditions():
    """画面の区分（条件ヒット馬・指数クロス・CSV の「区分」列）は、結合データを見て希少条件の最低件数を使う。"""
    g1 = "東京×芝×23-24×定量×G1×1"
    for cat, cnt in (("abs", 6), ("axis", 6), ("opp", 15), ("keshi", 12)):
        rare = {"odds_mode": "normal", "件数": cnt, "key": g1, **STATS[cat]}
        assert app.hit_category(rare) == cat
        assert app.hit_category({**rare, "key": KEY}) is None                 # ふつうの条件は今までの最低件数
        assert app.hit_category({**rare, "件数": cnt - 1}) is None
    hr = {"件数": 15, "key": g1, "連対率_val": 10.0, "複勝率_val": 36.0, "掲示板率_val": 55.0, "複勝回収値": 0.0,
          "単勝適正回収値": 210.0}
    assert [app.hit_category({**hr, "odds_mode": m}) for m in ("odds10u", "odds10_50", "odds50p", "normal")] == ["axis", "axis", None, None]
    assert app.hit_category({**hr, "odds_mode": "odds10_50", "key": KEY}) is None
    assert app.hit_category({**hr, "odds_mode": "odds10_50", "key": KEY, "件数": 30}) == "axis"


@pytest.mark.parametrize("normal_cnt, sub_cnt, dropped", [(20, 18, 1), (20, 17, 1), (17, 20, 1), (20, 16, 0), (20, 20, 1)])
def test_redundant_normal_row_is_dropped_within_three(normal_cnt, sub_cnt, dropped):
    """同じ馬・指数・パターン・結合データで、通常とオッズ帯別が同じ区分・件数の差3以内なら、通常の行だけを除く。"""
    rows = [hit(1, "タイムM", "normal", normal_cnt, "abs"), hit(1, "タイムM", "odds10_50", sub_cnt, "abs")]
    info = {}
    b = app.classify_hits(rows, info)
    assert info["dropped"] == dropped
    assert [h["odds_mode"] for h in b["abs"]] == (["odds10_50"] if dropped else ["odds10_50", "normal"])


def test_redundant_normal_needs_same_horse_indicator_family_key_and_category():
    sub = hit(1, "タイムM", "odds10u", 20, "abs")
    same = hit(1, "タイムM", "normal", 21, "abs")
    assert app.classify_hits([same, sub], info := {})["abs"] == [sub] and info["dropped"] == 1
    others = [
        hit(2, "タイムM", "normal", 21, "abs"),                              # 別の馬
        hit(1, "対戦M", "normal", 21, "abs"),                                # 別の指数
        hit(1, "タイムM", "normal", 21, "abs", family="rankB"),               # 別のパターン
        hit(1, "タイムM", "normal", 21, "abs", key=KEY[:-1] + "2"),          # 別の結合データ
        hit(1, "タイムM", "normal", 21, "abs", scheme="s2"),                 # 別のスキーム
        hit(1, "タイムM", "normal", 21, "abs", region="全場"),                # 別の地域
        hit(1, "タイムM", "normal", 21, "axis"),                             # 同じキーでも別の区分
    ]
    for other in others:
        info = {}
        b = app.classify_hits([other, sub], info)
        assert info["dropped"] == 0 and sum(len(v) for v in b.values()) == 2, other
    # オッズ帯別の行が2つあり、片方だけが件数3以内でも除く。通常の行だけなら残す
    far = hit(1, "タイムM", "odds50p", 10, "abs")
    assert app.classify_hits([same, far, sub], info := {})["abs"] == [sub, far] and info["dropped"] == 1
    assert app.classify_hits([same], info := {})["abs"] == [same] and info["dropped"] == 0
    # オッズ帯別どうしは件数が近くても除かない
    near = hit(1, "タイムM", "odds10_50", 20, "abs")
    assert len(app.classify_hits([sub, near], info := {})["abs"]) == 2 and info["dropped"] == 0


def test_dropped_normal_does_not_hide_an_unclassified_or_differently_classified_sub_row():
    """オッズ帯別の行がどの区分にも入らないときは、通常の行を残す（比べる相手が区分に入っていない）。"""
    normal = hit(1, "タイムM", "normal", 12, "abs")
    plain = {**normal, "odds_mode": "odds10u", "テーブル名": "t2", "連対率_val": 30.0, "複勝率_val": 40.0, "掲示板率_val": 50.0}
    assert app.hit_category(plain) is None
    assert app.classify_hits([normal, plain], info := {})["abs"] == [normal] and info["dropped"] == 0


def test_hits_are_ordered_with_normal_last():
    """並び: オッズ帯（2〜10倍未満 → 10〜50倍 → 50倍以上 → 通常）→ 馬番 → 複勝率（消しは昇順）。"""
    rows = [hit(u, "タイムM", m, 40 + 10 * i, "axis", family=f)
            for i, (m, f) in enumerate((("normal", "normal"), ("odds50p", "rankB"), ("odds10_50", "rankJ"), ("odds10u", "rankBJ")))
            for u in (2, 1)]
    got = [(h["odds_mode"], h["馬番"]) for h in app.classify_hits(rows)["axis"]]
    assert got == [(m, u) for m in ("odds10u", "odds10_50", "odds50p", "normal") for u in ("1", "2")]
    a = hit(1, "タイムM", "odds10u", 40, "axis", family="a")
    b = {**hit(1, "タイムM", "odds10u", 40, "axis", family="b"), "複勝率_val": 95.0}
    assert app.classify_hits([a, b])["axis"] == [b, a]                       # 複勝率の高い順
    k1 = hit(1, "タイムM", "odds10u", 40, "keshi", family="a")
    k2 = {**hit(1, "タイムM", "odds10u", 40, "keshi", family="b"), "複勝率_val": 1.0}
    assert app.classify_hits([k1, k2])["keshi"] == [k2, k1]                  # 消しは低い順


def test_hits_order_does_not_depend_on_input_order():
    """同点（同じオッズ帯・馬番・複勝率）の行の並びは、渡した行の順（xjoin の行の順）に依らない。指数は台帳の順。"""
    import random
    rows = [hit(u, b, "odds10u", 40, "axis", family=f) for u in (1, 2) for b in ("対戦M", "タイムM", "Gold", "オメガ指数")
            for f in ("rankB", "normal")]
    want = app.classify_hits(rows)["axis"]
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert app.classify_hits(shuffled)["axis"] == want
    order = [h["base"] for h in want if h["馬番"] == "1" and h["family"] == "normal"]
    assert order == app.order_bases(order) and len(order) == 4
    assert [h["family"] for h in want[:2]] == ["normal", "rankB"]


def test_spread_cap_gives_normal_rows_the_lowest_priority():
    """表示の枠はオッズ帯別の組に先に配り、通常の組は残り（どの組も最低1行は出す。並びは元のまま）。"""
    rows = ([{"馬番": "1", "odds_mode": "odds10u", "i": i} for i in range(40)]
            + [{"馬番": "2", "odds_mode": "odds50p", "i": i} for i in range(10)]
            + [{"馬番": str(u), "odds_mode": "normal", "i": i} for u in (1, 2, 3) for i in range(100)])
    out = app.spread_cap(rows, 80)
    n = {}
    for h in out:
        n[(h["馬番"], h["odds_mode"])] = n.get((h["馬番"], h["odds_mode"]), 0) + 1
    assert n == {("1", "odds10u"): 40, ("2", "odds50p"): 10, ("1", "normal"): 10, ("2", "normal"): 10, ("3", "normal"): 10}
    assert out == [h for h in rows if h in out]
    # オッズ帯別だけで上限を超えるときも、通常の組は1行ずつ出す（馬が丸ごと消えない）
    out = app.spread_cap(rows, 20)
    n = {}
    for h in out:
        n[(h["馬番"], h["odds_mode"])] = n.get((h["馬番"], h["odds_mode"]), 0) + 1
    assert all(n[(u, "normal")] == 1 for u in ("1", "2", "3")) and n[("1", "odds10u")] == n[("2", "odds50p")] == 8
    # 通常の行だけ・オッズ帯別の行だけでも今までどおり均等に切る
    only_normal = [h for h in rows if h["odds_mode"] == "normal"]
    assert len(app.spread_cap(only_normal, 60)) == 60
    assert app.spread_cap(rows, 10 ** 6) is rows


def test_hot_rows_are_axis_hits_in_long_odds_bands():
    for cat, cnt in (("abs", 12), ("axis", 22), ("opp", 40), ("keshi", 25)):
        for mode in ("normal", "odds10u", "odds10_50", "odds50p"):
            want = cat in ("abs", "axis") and mode in ("odds10_50", "odds50p")
            assert app.is_hot(hit(1, "タイムM", mode, cnt, cat), cat) is want
    assert app.is_hot({"odds_mode": None}, "abs") is False and app.is_hot({"odds_mode": "odds50p"}, None) is False


def test_axis_cross_counts_indicators_with_axis_hits():
    race = [{"馬番": str(u), "馬名": f"馬{u}", "騎手": "J"} for u in (1, 2, 3, 4)]
    rows = [hit(1, "タイムM", "odds10u", 12, "abs"), hit(1, "タイムM", "odds10u", 22, "axis", family="rankB"),
            hit(1, "対戦M", "odds50p", 22, "axis"), hit(1, "オメガ指数", "normal", 40, "opp"),
            hit(2, "タイムM", "normal", 22, "axis"), hit(2, "オメガ指数", "normal", 25, "keshi"),
            hit(3, "オメガ指数", "odds10_50", 12, "abs"), hit(3, "対戦M", "normal", 22, "axis")]
    b = app.classify_hits(rows)
    xs = {x["uma"]: x for x in app.axis_cross(race, b)}
    assert [(t["base"], t["abs"], t["axis"], t["hot"]) for t in xs["1"]["inds"]] == [("タイムM", 1, 1, False), ("対戦M", 0, 1, True)]
    assert (xs["1"]["n"], xs["1"]["n_hot"], xs["1"]["hit"]) == (2, 1, True)       # 相手候補・消しは数えない
    assert (xs["2"]["n"], xs["2"]["hit"]) == (1, False) and (xs["4"]["n"], xs["4"]["hit"]) == (0, False)
    assert (xs["3"]["n"], xs["3"]["n_hot"], xs["3"]["hit"]) == (2, 1, True)
    assert {x["uma"] for x in app.axis_cross(race, b, xmin=3) if x["hit"]} == set()
    assert {x["uma"] for x in app.axis_cross(race, b, xmin=1) if x["hit"]} == {"1", "2", "3"}
    assert {x["uma"] for x in app.axis_cross(race, b, a="タイムM", b="対戦M") if x["hit"]} == {"1"}
    assert {x["uma"] for x in app.axis_cross(race, b, a="対戦M", xmin=1) if x["hit"]} == {"1", "3"}


def test_cross_rows_counts_top_indicators_by_rank_and_hensachi():
    bases = ["タイムM", "対戦M", "Gold"]

    def row(uma, vals):
        r = {"馬番": str(uma), "枠番": "1", "馬名": f"馬{uma}", "騎手": "J"}
        for b, (rank, hen) in zip(bases, vals):
            r[f"{b}_順位"], r[f"{b}_偏差値"] = rank, hen
        return r

    rows = [row(1, [("1", "70"), ("3", "55"), ("9", "40")]),       # 2指数（順位）
            row(2, [("4", "61"), ("5", "62"), ("6", "63")]),       # 3指数（偏差値だけ）
            row(3, [("3", "61"), ("", ""), ("0", "60.9")]),        # 1指数（順位 0・空は対象外。60.9 は未満）
            row(4, [("13.0", "50"), (None, None), ("2", None)])]   # 1指数（順位 2）
    n = lambda crit, **k: {x["uma"]: (x["n"], x["hit"]) for x in app.cross_rows(rows, bases, crit, **k)}
    assert n("either") == {"1": (2, True), "2": (3, True), "3": (1, False), "4": (1, False)}
    assert n("rank") == {"1": (2, True), "2": (0, False), "3": (1, False), "4": (1, False)}
    assert n("hen") == {"1": (1, False), "2": (3, True), "3": (1, False), "4": (0, False)}
    assert n("both") == {"1": (1, False), "2": (0, False), "3": (1, False), "4": (0, False)}
    assert n("either", xmin=1)["3"] == (1, True) and n("either", xmin=3)["1"] == (2, False)
    assert n("either", a="タイムM", b="対戦M") == {"1": (2, True), "2": (3, True), "3": (1, False), "4": (1, False)}
    assert n("either", a="Gold", xmin=1) == {"1": (2, False), "2": (3, True), "3": (1, False), "4": (1, True)}
    tops = app.cross_rows(rows, bases)[0]["tops"]
    assert [(t["base"], t["rank"], t["hen"]) for t in tops] == [("タイムM", 1, 70.0), ("対戦M", 3, 55.0)]
    assert app.cell_class(rows[0], "タイムM") == "top3 hen61" and app.cell_class(rows[1], "Gold") == "hen61"
    assert app.cell_class(rows[0], "対戦M") == "top3" and app.cell_class(rows[0], "Gold") == ""


# ---------------------------------------------------------------- 人気馬（2〜10倍未満）の見分け（2026-11-13）
def test_low_return_cut_only_touches_favorite_axis_and_opp_rows():
    """過剰人気カット: 2〜10倍未満（odds10u）の軸候補・相手候補で、複勝回収値も単勝適正回収値も100未満の行だけを除く。"""
    for cat, cnt in (("axis", 22), ("opp", 40)):
        low = hit(1, "タイムM", "odds10u", cnt, cat)                       # 80 / 80
        assert app.is_low_return_favorite(low, cat)
        for more in ({"複勝回収値": 100.0}, {"単勝適正回収値": 100.0}, {"複勝回収値": 100.0, "単勝適正回収値": 250.0}):
            assert not app.is_low_return_favorite({**low, **more}, cat), more      # どちらかが100以上なら残す（境界は残す側）
        for more in ({"複勝回収値": 99.99, "単勝適正回収値": 99.99}, {"複勝回収値": None, "単勝適正回収値": None},
                     {"複勝回収値": None}, {"複勝回収値": "", "単勝適正回収値": "x"}, {"複勝回収値": 0, "単勝適正回収値": 0}):
            assert app.is_low_return_favorite({**low, **more}, cat), more          # 値が無いものは「100以上」ではない
        for mode in ("normal", "odds10_50", "odds50p", None):                       # ほかのオッズ帯は対象外
            assert not app.is_low_return_favorite({**low, "odds_mode": mode}, cat)
    # 絶対軸（もともと回収値100以上が条件）・消し候補・区分なしは対象外
    assert not app.is_low_return_favorite(hit(1, "タイムM", "odds10u", 12, "abs"), "abs")
    assert not app.is_low_return_favorite(hit(1, "タイムM", "odds10u", 25, "keshi"), "keshi")
    assert not app.is_low_return_favorite(hit(1, "タイムM", "odds10u", 22, "axis"), None)


def test_classify_hits_cut_low_counts_and_keeps_everything_else():
    rows = [hit(1, "タイムM", "odds10u", 22, "axis", family="a"),                             # 除く
            hit(1, "タイムM", "odds10u", 22, "axis", family="b", **{"複勝回収値": 130.0}),     # 残す
            hit(1, "タイムM", "odds10u", 40, "opp", family="c"),                              # 除く
            hit(1, "タイムM", "odds10u", 40, "opp", family="d", **{"単勝適正回収値": 100.0}),  # 残す
            hit(1, "タイムM", "odds10_50", 22, "axis", family="e"),                           # 10〜50倍は対象外
            hit(1, "タイムM", "odds10u", 12, "abs", family="f"),
            hit(1, "タイムM", "odds10u", 25, "keshi", family="g"),
            hit(2, "対戦M", "normal", 40, "opp", family="h")]
    off = app.classify_hits(rows, info := {})
    assert {c: len(v) for c, v in off.items()} == {"abs": 1, "axis": 3, "opp": 3, "keshi": 1}
    assert info["cut"] == {"axis": 0, "opp": 0}
    on = app.classify_hits(rows, info := {}, cut_low=True)
    assert info["cut"] == {"axis": 1, "opp": 1} and info["dropped"] == 0
    assert sorted(h["family"] for h in on["axis"]) == ["b", "e"] and sorted(h["family"] for h in on["opp"]) == ["d", "h"]
    assert on["abs"] == off["abs"] and on["keshi"] == off["keshi"]
    assert app.classify_hits(rows, cut_low=False) == off


def test_cut_row_does_not_come_back_as_its_redundant_normal_twin():
    """カットした2〜10倍未満の行と同じ区分・件数差3以内の「通常」の行（冗長な通常）は、カット ON でも出さない（同じ過剰人気の
    条件が「通常」として出直さない）。その「通常」の行自身の回収値が100以上でも同じ（件数差3以内の上振れは10倍以上の馬
    1〜3頭ぶん）。件数が離れた「通常」の行（ほかのオッズ帯の馬も含む別の情報）は、カットの対象外なので残る。行の順に依らない。"""
    import itertools
    fav = hit(1, "タイムM", "odds10u", 38, "axis")
    twin = hit(1, "タイムM", "normal", 40, "axis")
    far = hit(1, "タイムM", "normal", 90, "axis", family="rankB")
    fav2 = hit(1, "タイムM", "odds10u", 38, "axis", family="rankB")
    good_twin = hit(1, "タイムM", "normal", 40, "axis", family="rankJ", **{"複勝回収値": 103.0})    # 回収値100以上の「通常」
    fav3 = hit(1, "タイムM", "odds10u", 38, "axis", family="rankJ")
    rows = [twin, fav, far, fav2, good_twin, fav3]
    for order in itertools.permutations(rows):
        got = app.classify_hits(list(order), info := {}, cut_low=True)
        assert got["axis"] == [far] and info == {"dropped": 2, "cut": {"axis": 3, "opp": 0}}
    # カット OFF でも「通常」の2行は冗長として出ていない（カット ON の表 = OFF の表 − 依頼の条件に当たる行）
    off = app.classify_hits(rows, info := {})
    assert off["axis"] == [fav, fav2, fav3, far] and info["dropped"] == 2
    assert [h for h in off["axis"] if not app.is_low_return_favorite(h, "axis")] == [far]
    # 相手の2〜10倍未満の行が回収値100以上で残るなら、通常は今までどおりその行の冗長
    fav_ok = hit(1, "タイムM", "odds10u", 38, "axis", **{"複勝回収値": 120.0})
    assert app.classify_hits([twin, fav_ok], info := {}, cut_low=True)["axis"] == [fav_ok] and info["dropped"] == 1


def _danger_spec(abs_cnt, axis_cnt, keshi_10u, keshi_10u_inds):
    """依頼の式そのもの（2026-11-14）。axis_total = abs_cnt + axis_cnt。"""
    axis_total = abs_cnt + axis_cnt
    if abs_cnt == 0 and keshi_10u >= 3 and keshi_10u_inds >= 2 and keshi_10u > axis_total:
        return "dominant"
    if axis_total >= 1 and keshi_10u >= 2 and keshi_10u >= axis_total:
        return "caution"
    return ""


@pytest.mark.parametrize("abs_cnt, axis_cnt, keshi, inds, want", [
    (0, 0, 0, 0, ""), (0, 3, 0, 0, ""),
    # 1〜2行の偶発的な消しでは「消優勢」にしない（前の基準では 消優勢 だった）
    (0, 0, 1, 1, ""), (0, 0, 2, 2, ""), (0, 1, 1, 1, ""), (0, 2, 2, 2, "caution"), (0, 1, 2, 2, "caution"),
    # 消優勢: 絶対軸なし・3行以上・2指数以上・軸の合計より多い
    (0, 0, 3, 2, "dominant"), (0, 2, 3, 2, "dominant"), (0, 2, 5, 3, "dominant"), (0, 9, 10, 2, "dominant"),
    (0, 0, 3, 1, ""),                  # 1指数だけの消し（軸なし）→ 何も付けない
    (0, 2, 3, 1, "caution"),           # 1指数だけ → 消優勢にはしないが、軸の合計以上なので要注意
    (0, 3, 3, 2, "caution"),           # 同数は「より多い」ではない → 要注意
    (0, 4, 3, 3, ""),                  # 軸のほうが多い
    # 絶対軸がある馬は、消しがどれだけ多くても 要注意 まで
    (1, 0, 3, 2, "caution"), (1, 0, 9, 4, "caution"), (1, 1, 2, 1, "caution"), (2, 0, 2, 2, "caution"),
    (1, 0, 1, 1, ""), (1, 2, 2, 2, ""), (1, 3, 3, 2, ""), (2, 2, 4, 2, "caution"),
    # 要注意の下限: 2行以上・軸の合計以上
    (0, 3, 2, 2, ""), (0, 10, 9, 5, ""), (0, 10, 10, 1, "caution"), (0, 10, 10, 5, "caution"),
    (0, 10, 11, 5, "dominant"), (0, 10, 11, 1, "caution")])
def test_danger_level_cases(abs_cnt, axis_cnt, keshi, inds, want):
    """消優勢 = 絶対軸が無く、消し(2〜10倍未満) が3行以上・2指数以上で、軸の合計より多い。
    要注意 = 消優勢ではなく、軸が1行以上あり、消し(2〜10倍未満) が2行以上で、軸の合計以上。"""
    assert app.danger_level(abs_cnt, abs_cnt + axis_cnt, keshi, inds) == want
    assert _danger_spec(abs_cnt, axis_cnt, keshi, inds) == want


def test_danger_level_equals_the_requested_formula_on_a_grid():
    seen = set()
    for abs_cnt in range(0, 4):
        for axis_cnt in range(0, 6):
            for keshi in range(0, 9):
                for inds in range(0, min(keshi, 4) + 1):
                    if keshi and not inds:
                        continue                      # 消しがあれば、出た指数は1つ以上
                    got = app.danger_level(abs_cnt, abs_cnt + axis_cnt, keshi, inds)
                    assert got == _danger_spec(abs_cnt, axis_cnt, keshi, inds), (abs_cnt, axis_cnt, keshi, inds)
                    seen.add(got)
    assert seen == {"", "caution", "dominant"}
    assert (app.KESHI_DOMINANT_MIN_ROWS, app.KESHI_DOMINANT_MIN_INDS, app.KESHI_CAUTION_MIN_ROWS) == (3, 2, 2)


def test_horse_stats_and_axis_cross_count_favorite_keshi_per_indicator():
    """消し(2〜10倍未満) の行数と指数数は odds10u の消し候補だけを数える。指数クロスのタグには、同じ指数の 消し(2〜10倍未満) が付く。"""
    race = [{"馬番": str(u), "馬名": f"馬{u}", "騎手": "J"} for u in (1, 2, 3, 4, 5, 6, 7, 8)]
    rows = [hit(1, "タイムM", "odds10u", 12, "abs"), hit(1, "タイムM", "odds10u", 22, "axis", family="rankB"),
            hit(1, "対戦M", "odds50p", 22, "axis"),
            hit(1, "タイムM", "odds10u", 25, "keshi", family="k1"), hit(1, "タイムM", "odds10u", 25, "keshi", family="k2"),
            hit(1, "オメガ指数", "odds10u", 25, "keshi"), hit(1, "対戦M", "normal", 25, "keshi", family="k3"),
            hit(1, "対戦M", "odds50p", 25, "keshi", family="k4"),
            hit(2, "タイムM", "odds10u", 22, "axis"), hit(2, "対戦M", "odds10u", 22, "axis"),
            hit(2, "Gold", "odds10u", 25, "keshi"),
            hit(3, "Gold", "odds10u", 25, "keshi"), hit(3, "Gold", "normal", 40, "opp"),
            # 馬5: 絶対軸なし・軸2（過剰人気）・消し(2〜10倍未満) 3行が2指数 → 消優勢（カットで軸が0になっても同じ）
            hit(5, "タイムM", "odds10u", 22, "axis"), hit(5, "対戦M", "odds10u", 22, "axis"),
            hit(5, "Gold", "odds10u", 25, "keshi", family="k1"), hit(5, "Gold", "odds10u", 25, "keshi", family="k2"),
            hit(5, "ZI", "odds10u", 25, "keshi"),
            # 馬6: 軸なし・消し(2〜10倍未満) 3行が1指数だけ → 何も付けない
            hit(6, "Gold", "odds10u", 25, "keshi", family="k1"), hit(6, "Gold", "odds10u", 25, "keshi", family="k2"),
            hit(6, "Gold", "odds10u", 25, "keshi", family="k3"),
            # 馬7: 絶対軸なし・軸3（10〜50倍）・消し(2〜10倍未満) 3行が2指数 → 同数なので 要注意
            hit(7, "タイムM", "odds10_50", 22, "axis", family="a"), hit(7, "タイムM", "odds10_50", 22, "axis", family="b"),
            hit(7, "対戦M", "odds10_50", 22, "axis"),
            hit(7, "Gold", "odds10u", 25, "keshi", family="k1"), hit(7, "Gold", "odds10u", 25, "keshi", family="k2"),
            hit(7, "ZI", "odds10u", 25, "keshi"),
            # 馬8: 軸1・消しは 2〜10倍未満 が1行だけ（ほかは通常・50倍以上）→ 判定に使うのは 2〜10倍未満 の1行 → 何も付けない
            #      （全部のオッズ帯の消し3行で比べると 要注意 になってしまう）
            hit(8, "タイムM", "odds10_50", 22, "axis"), hit(8, "Gold", "odds10u", 25, "keshi"),
            hit(8, "ZI", "normal", 60, "keshi"), hit(8, "対戦M", "odds50p", 25, "keshi")]
    b = app.classify_hits(rows)
    st = app.horse_stats(race, b)
    assert (st["1"]["abs"], st["1"]["axis"], st["1"]["keshi"], st["1"]["opp"], st["1"]["hot"]) == (1, 2, 5, 0, 1)
    assert (st["1"]["axis_rows"], st["1"]["keshi_fav"], st["1"]["keshi_fav_inds"]) == (3, 3, 2)
    # 馬1: 絶対軸があるので、消し3行（2指数）が軸の合計3行以上でも 要注意 まで
    assert st["1"]["keshi_fav_by"] == {"タイムM": 2, "オメガ指数": 1} and st["1"]["danger"] == "caution"
    # 馬2・馬3: 消し(2〜10倍未満) が1行だけ → 何も付けない（前の基準では 要注意・消優勢）
    assert (st["2"]["axis_rows"], st["2"]["keshi_fav"], st["2"]["keshi_fav_inds"], st["2"]["danger"]) == (2, 1, 1, "")
    assert (st["3"]["axis_rows"], st["3"]["keshi_fav"], st["3"]["opp"], st["3"]["danger"]) == (0, 1, 1, "")
    assert (st["4"]["axis_rows"], st["4"]["keshi_fav"], st["4"]["danger"]) == (0, 0, "")
    assert (st["5"]["abs"], st["5"]["axis_rows"], st["5"]["keshi_fav"], st["5"]["keshi_fav_inds"], st["5"]["danger"]) == (0, 2, 3, 2, "dominant")
    assert (st["6"]["axis_rows"], st["6"]["keshi_fav"], st["6"]["keshi_fav_inds"], st["6"]["danger"]) == (0, 3, 1, "")
    assert (st["7"]["abs"], st["7"]["axis_rows"], st["7"]["keshi_fav"], st["7"]["keshi_fav_inds"], st["7"]["danger"]) == (0, 3, 3, 2, "caution")
    assert (st["8"]["axis_rows"], st["8"]["keshi"], st["8"]["keshi_fav"], st["8"]["keshi_fav_inds"], st["8"]["danger"]) == (1, 3, 1, 1, "")
    assert app.danger_level(0, 1, st["8"]["keshi"], 1) == "caution"       # 全部のオッズ帯の消しで比べたら付いてしまう
    xs = {x["uma"]: x for x in app.axis_cross(race, b)}
    assert [(t["base"], t["abs"], t["axis"], t["keshi_fav"], t["hot"]) for t in xs["1"]["inds"]] == [
        ("タイムM", 1, 1, 2, False), ("対戦M", 0, 1, 0, True)]            # 対戦M の消しは 通常・50倍以上 なので数えない
    assert (xs["1"]["n"], xs["1"]["n_clash"], xs["1"]["keshi_fav"], xs["1"]["keshi_fav_inds"], xs["1"]["danger"]) == (2, 1, 3, 2, "caution")
    assert [(t["base"], t["keshi_fav"]) for t in xs["2"]["inds"]] == [("タイムM", 0), ("対戦M", 0)] and xs["2"]["n_clash"] == 0
    assert (xs["2"]["keshi_fav"], xs["2"]["danger"], xs["2"]["hit"]) == (1, "", True)
    assert (xs["3"]["n"], xs["3"]["hit"], xs["3"]["danger"]) == (0, False, "")
    assert (xs["5"]["n"], xs["5"]["hit"], xs["5"]["danger"]) == (2, True, "dominant")
    assert (xs["7"]["n"], xs["7"]["n_hot"], xs["7"]["danger"]) == (2, 2, "caution")
    # 判定は過剰人気カットのあとの行数で付ける（馬2: 軸2 → 0・消し1 → 軸が無くなっても1行の消しでは付けない。
    # 馬5: 軸2 → 0・消し3（2指数）→ 消優勢のまま。馬7: 10〜50倍の軸はカットの対象外 → 要注意のまま）
    cut = app.classify_hits(rows, cut_low=True)
    st2 = app.horse_stats(race, cut)
    assert (st2["2"]["axis_rows"], st2["2"]["danger"]) == (0, "")
    assert (st2["1"]["axis_rows"], st2["1"]["keshi_fav"], st2["1"]["danger"]) == (2, 3, "caution")   # 馬1: 2〜10倍未満の軸候補1行が除かれ、絶対軸と50倍以上の軸が残る
    assert (st2["5"]["axis_rows"], st2["5"]["danger"]) == (0, "dominant")
    assert (st2["7"]["axis_rows"], st2["7"]["danger"]) == (3, "caution")
    assert [x["uma"] for x in app.axis_cross(race, cut) if x["hit"]] == ["1", "7"]

