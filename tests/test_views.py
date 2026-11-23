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


def test_hit_category_reads_the_win_rate_and_wedge_of_the_row():
    """画面の区分は、ヒット行の 勝率_val（絶対軸の条件②）と WEDGE（2〜10倍未満専用の消し）も classify に渡す。"""
    two = {"odds_mode": "normal", "件数": 20, "key": KEY, "勝率_val": 40.0, "連対率_val": 55.0, "複勝率_val": 80.0,
           "掲示板率_val": 85.0, "複勝回収値": 90.0, "単勝適正回収値": 100.0}
    assert app.hit_category(two) == "abs"                                             # ② 件数 20・勝率 40・複勝率 80・回収値 100
    assert app.hit_category({**two, "勝率_val": 39.9}) is None and app.hit_category({**two, "勝率_val": None}) is None
    assert app.hit_category({k: v for k, v in two.items() if k != "勝率_val"}) is None
    assert app.hit_category({**two, "件数": 19}) is None and app.hit_category({**two, "複勝率_val": 79.9}) is None
    assert app.hit_category({**two, "単勝適正回収値": 99.9}) is None
    assert app.hit_category({**two, "掲示板率_val": 90.0, "勝率_val": 39.9}) == "axis"   # ② でなければ今までの区分
    low_key = "川田将雅×前走1~3着×6位~"
    assert app.hit_category({**two, "key": low_key}) is None and app.low_tail_category({**two, "key": low_key}) == "abs"
    assert app.low_tail_category({**two, "key": low_key, "勝率_val": None}) is None
    # 2〜10倍未満専用の消し: 複勝率 < 20 かつ（掲示板率 < 35 または WEDGE < 0.350）。WEDGE が無ければ「< 0.350」は満たさない
    ks = {"odds_mode": "odds10u", "件数": 25, "key": KEY, "勝率_val": 3.0, "連対率_val": 8.0, "複勝率_val": 15.0,
          "掲示板率_val": 50.0, "複勝回収値": 60.0, "単勝適正回収値": 60.0}
    assert app.hit_category(ks) is None and app.keshi_rule_of(ks) is None            # 掲示板率 50・WEDGE なし → 消しではない
    assert app.hit_category({**ks, "WEDGE": None}) is None and app.hit_category({**ks, "WEDGE": 0.35}) is None
    for more in ({"WEDGE": 0.3499}, {"掲示板率_val": 34.9}, {"掲示板率_val": 34.9, "WEDGE": 1.2}, {"掲示板率_val": None, "WEDGE": 0.1}):
        assert app.hit_category({**ks, **more}) == "keshi" and app.keshi_rule_of({**ks, **more}) == "fav", more
        for mode in ("normal", "odds10_50", "odds50p"):
            assert app.hit_category({**ks, **more, "odds_mode": mode}) is None and app.keshi_rule_of({**ks, **more, "odds_mode": mode}) is None
    common = {**ks, "複勝率_val": 3.0, "掲示板率_val": 8.0, "WEDGE": 0.1}
    assert app.hit_category(common) == "keshi" and app.keshi_rule_of(common) == "any"
    # 消しでなくなった行は、ほかの区分の判定に進む（複勝率は低いが連対率・回収値は絶対軸の基準、という行）
    assert app.hit_category({**ks, "件数": 12, "連対率_val": 85.0, "複勝回収値": 120.0, "WEDGE": 0.9}) == "abs"


def test_stats_presets_give_the_intended_category():
    for cat, cnt in (("abs", 12), ("axis", 22), ("opp", 40), ("keshi", 25)):
        for mode in ("normal", "odds10u", "odds10_50", "odds50p"):
            hit(1, "タイムM", mode, cnt, cat)


def test_hit_category_passes_the_key_for_rare_conditions():
    """画面の区分（条件ヒット馬・指数クロス・CSV の「区分」列）は、結合データを見て希少条件の最低件数を使う。"""
    g1 = "東京×芝×23-24×定量×G1×1"
    for cat, cnt in (("axis", 6), ("opp", 15), ("keshi", 12)):
        rare = {"odds_mode": "normal", "件数": cnt, "key": g1, **STATS[cat]}
        assert app.hit_category(rare) == cat
        assert app.hit_category({**rare, "key": KEY}) is None                 # ふつうの条件は今までの最低件数
        assert app.hit_category({**rare, "件数": cnt - 1}) is None
    # 絶対軸は希少条件でも件数を下げない（2026-11-23〜）: 6〜9件は絶対軸にならず、軸候補の条件（希少 6件〜）を満たせば軸候補
    rare_abs = {"odds_mode": "normal", "key": g1, **STATS["abs"]}
    assert [app.hit_category({**rare_abs, "件数": n}) for n in (5, 6, 9, 10)] == [None, "axis", "axis", "abs"]
    assert [app.hit_category({**rare_abs, "件数": n, "掲示板率_val": 70.0}) for n in (6, 9, 10)] == [None, None, "abs"]
    assert [app.hit_category({**rare_abs, "件数": n, "key": KEY}) for n in (9, 10)] == [None, "abs"]
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
        assert got["axis"] == [far] and info == {"dropped": 2, "cut": {"axis": 3, "opp": 0}, "tradeoff": 0}
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


# ---------------------------------------------------------------- 負の相関にある指数の消し候補（2026-11-16）
# 2〜10倍未満専用の規則（複勝率 < 20 かつ（掲示板率 < 35 または WEDGE < 0.350））だけで消し候補になる成績
FAV_ONLY = {"複勝率_val": 15.0, "掲示板率_val": 30.0}
FAV_WEDGE = {"複勝率_val": 15.0, "掲示板率_val": 50.0, "WEDGE": 0.3}     # 掲示板率は 35 以上で、WEDGE が低いほうで消しになる成績
GOOD = {"複勝回収値": 130.0}                                # 過剰人気カットの対象にならない軸


def test_tradeoff_keshi_rows_are_excluded_when_the_opposite_indicator_has_an_axis_hit():
    """片方の指数で絶対軸・軸候補に当たった馬の、対になる指数の「2〜10倍未満だけの消し候補」を除く（双方向・3つの組）。"""
    for axis_base, keshi_base in (("先行", "瞬発"), ("瞬発", "先行"), ("想定前半", "持続"), ("追走", "ST指数"), ("持久", "想定前半"),
                                  ("想定後半", "ST指数"), ("吉馬_先行力", "吉馬_末脚力"), ("吉馬_末脚力", "吉馬_先行力"),
                                  ("安井_スピード", "安井_スタミナ"), ("安井_パワー", "安井_スピード"), ("安井_根性", "安井_スピード")):
        for cat in ("abs", "axis"):
            ax = hit(1, axis_base, "odds10_50", 22, cat)
            ks = hit(1, keshi_base, "odds10u", 25, "keshi", **FAV_ONLY)
            b = app.classify_hits([ax, ks], info := {})
            assert b["keshi"] == [] and b[cat] == [ax] and info["tradeoff"] == 1, (axis_base, keshi_base, cat)
            assert app.keshi_rule_of(ks) == "fav"
            kw = hit(1, keshi_base, "odds10u", 25, "keshi", **FAV_WEDGE)              # WEDGE のほうで消しになる行も同じ
            b = app.classify_hits([ax, kw], info := {})
            assert b["keshi"] == [] and info["tradeoff"] == 1 and app.keshi_rule_of(kw) == "fav"
    # 除かないもの
    ax = hit(1, "先行", "odds10_50", 22, "axis")
    keep = [
        hit(1, "瞬発", "odds10u", 25, "keshi"),                                  # 全モード共通の規則（複勝率 < 5・掲示板率 < 10）も満たす
        hit(1, "瞬発", "normal", 60, "keshi", family="k2"),                      # ほかのオッズ帯の消し
        hit(1, "瞬発", "odds10_50", 25, "keshi", family="k3"),
        hit(1, "Gold", "odds10u", 25, "keshi", **FAV_ONLY),                      # 負の相関の組に無い指数
        hit(1, "想定前半", "odds10u", 25, "keshi", **FAV_ONLY),                  # 同じ側の指数（先行と想定前半は対ではない）
        hit(1, "安井_スタミナ", "odds10u", 25, "keshi", **FAV_ONLY),             # 別の取得元の組
        hit(2, "瞬発", "odds10u", 25, "keshi", **FAV_ONLY),                      # 別の馬（馬2 に先行の軸は無い）
        hit(1, "先行", "odds10u", 25, "keshi", family="k4", **FAV_ONLY),         # 軸と同じ指数の消し（指数の中で評価が割れている → 残す）
    ]
    b = app.classify_hits([ax] + keep, info := {})
    assert len(b["keshi"]) == len(keep) and info["tradeoff"] == 0
    st = app.horse_stats([{"馬番": "1", "馬名": "馬1", "騎手": "J"}, {"馬番": "2", "馬名": "馬2", "騎手": "J"}], b)["1"]
    assert st["keshi_fav_by"]["先行"] == 1                                     # 同じ指数の (消n) は今までどおり数える
    # 相手候補・消し候補しか無い指数は「軸ヒット」ではない
    opp = hit(1, "先行", "normal", 40, "opp")
    ks = hit(1, "瞬発", "odds10u", 25, "keshi", **FAV_ONLY)
    assert app.classify_hits([opp, ks], info := {})["keshi"] == [ks] and info["tradeoff"] == 0
    # 複数行: 対の指数のぶんだけ除く（行数を数える）
    rows = [ax, hit(1, "瞬発", "odds10u", 25, "keshi", family="a", **FAV_ONLY), hit(1, "持続", "odds10u", 25, "keshi", **FAV_ONLY),
            hit(1, "追走", "odds10u", 30, "keshi", family="b", **FAV_ONLY), hit(1, "Gold", "odds10u", 25, "keshi", **FAV_ONLY)]
    b = app.classify_hits(rows, info := {})
    assert [h["base"] for h in b["keshi"]] == ["Gold"] and info["tradeoff"] == 3


def test_tradeoff_uses_the_axis_rows_left_after_the_cut_and_feeds_every_count():
    """軸ヒットは過剰人気カット・冗長な通常を除いたあとの行で見る。除いた消しは、消し(2〜10倍未満)の行数・指数数・判定にも入らない。"""
    race = [{"馬番": str(u), "馬名": f"馬{u}", "騎手": "J"} for u in (1, 2)]
    low_axis = hit(1, "先行", "odds10u", 22, "axis")                              # 回収値 80 → カット ON で除かれる軸
    k1 = hit(1, "瞬発", "odds10u", 25, "keshi", family="a", **FAV_ONLY)
    k2 = hit(1, "持続", "odds10u", 25, "keshi", family="b", **FAV_ONLY)
    k3 = hit(1, "持久", "odds10u", 25, "keshi", family="c", **FAV_ONLY)
    rows = [low_axis, k1, k2, k3]
    off = app.classify_hits(rows, info := {})                                     # カット OFF: 先行の軸がある → 3行とも除く
    assert off["keshi"] == [] and info["tradeoff"] == 3
    st = app.horse_stats(race, off)["1"]
    assert (st["keshi"], st["keshi_fav"], st["keshi_fav_inds"], st["danger"]) == (0, 0, 0, "")
    on = app.classify_hits(rows, info := {}, cut_low=True)                        # カット ON: 軸が無くなる → 除かない → 消優勢
    assert len(on["keshi"]) == 3 and on["axis"] == [] and info["tradeoff"] == 0 and info["cut"]["axis"] == 1
    st = app.horse_stats(race, on)["1"]
    assert (st["keshi_fav"], st["keshi_fav_inds"], st["danger"]) == (3, 3, "dominant")
    # 回収値の伴う軸なら、カット ON でも除く
    good_axis = hit(1, "先行", "odds10u", 22, "axis", **GOOD)
    on = app.classify_hits([good_axis, k1, k2, k3], info := {}, cut_low=True)
    assert on["keshi"] == [] and info["tradeoff"] == 3
    xs = {x["uma"]: x for x in app.axis_cross(race, on, xmin=1)}
    assert (xs["1"]["keshi_fav"], xs["1"]["keshi_fav_inds"], xs["1"]["danger"], xs["1"]["n_clash"]) == (0, 0, "", 0)
    # カット ON で負の相関の除外が起きても、過剰人気カットは効いたまま（除外のあとの決め直しでカットを落とさない）
    other = hit(2, "Gold", "odds10u", 22, "axis")                                 # 別の馬の、回収値 80 の軸 → カットされる
    hot = hit(1, "先行", "odds10_50", 22, "axis", family="z")
    on = app.classify_hits([hot, k1, other], info := {}, cut_low=True)
    assert on["axis"] == [hot] and on["keshi"] == [] and info == {"dropped": 0, "cut": {"axis": 1, "opp": 0}, "tradeoff": 1}
    # 画面に出ない軸は軸ヒットではない: 回収値 80 の 2〜10倍未満の軸（カット）と、それと重なる「通常」の軸（冗長として省く）しか
    # 無い指数は、カット ON では軸が1行も出ない → 対の指数の消しは除かない
    cut_axis = hit(1, "先行", "odds10u", 22, "axis", family="y")
    twin_axis = hit(1, "先行", "normal", 23, "axis", family="y", **GOOD)
    on = app.classify_hits([twin_axis, cut_axis, k1], info := {}, cut_low=True)
    assert on["axis"] == [] and on["keshi"] == [k1] and info == {"dropped": 1, "cut": {"axis": 1, "opp": 0}, "tradeoff": 0}
    off = app.classify_hits([twin_axis, cut_axis, k1], info := {})                # カット OFF なら 2〜10倍未満の軸が出る → 除く
    assert off["axis"] == [cut_axis] and off["keshi"] == [] and info["tradeoff"] == 1
    # 軸が「冗長な通常」として省かれる行だけでも、同じ中身のオッズ帯別の軸が残っているので軸ヒット
    twin = hit(1, "先行", "normal", 23, "axis")
    sub = hit(1, "先行", "odds10_50", 22, "axis")
    b = app.classify_hits([twin, sub, k1], info := {})
    assert b["axis"] == [sub] and b["keshi"] == [] and (info["dropped"], info["tradeoff"]) == (1, 1)


def test_tradeoff_exclusion_does_not_hide_the_common_rule_keshi_of_the_same_condition():
    """除いた「2〜10倍未満だけの消し」と件数の近い「通常」の消し（全モード共通の規則）は、重なる行として省かずに残す。"""
    ax = hit(1, "先行", "odds10_50", 22, "axis")
    fav = hit(1, "瞬発", "odds10u", 25, "keshi", **FAV_ONLY)
    normal = hit(1, "瞬発", "normal", 26, "keshi")                                # 同じ条件の通常（複勝率 3・掲示板率 8）
    # 軸が無ければ、通常は 2〜10倍未満の行と重なるので省く（今までどおり）
    b = app.classify_hits([fav, normal], info := {})
    assert b["keshi"] == [fav] and (info["dropped"], info["tradeoff"]) == (1, 0)
    # 先行の軸があると 2〜10倍未満の行を除く → 通常の行は別の消し（共通の規則）なので出す
    for order in ([ax, fav, normal], [normal, fav, ax], [fav, ax, normal]):
        b = app.classify_hits(order, info := {})
        assert b["keshi"] == [normal] and (info["dropped"], info["tradeoff"]) == (0, 1)
    st = app.horse_stats([{"馬番": "1", "馬名": "馬1", "騎手": "J"}], b)["1"]
    assert (st["keshi"], st["keshi_fav"]) == (1, 0)


def test_low_index_hits_are_not_axis_on_the_hits_page():
    """画面の区分も結合データの末尾を見る: 指数が下位の条件は軸・相手にならず、消しはそのまま。"""
    low, top = "川田将雅×前走1~3着×6位~", "川田将雅×前走1~3着×1~3位"
    for cat, cnt in (("abs", 22), ("axis", 22), ("opp", 40)):
        row = {"odds_mode": "normal", "件数": cnt, **STATS[cat]}
        assert app.hit_category({**row, "key": top}) == cat and app.hit_category({**row, "key": low}) is None
        assert app.hit_category({**row, "key": "東京×芝×1600×定量×1・2_勝×9"}) is None
    assert app.hit_category({"odds_mode": "normal", "件数": 25, **STATS["keshi"], "key": low}) == "keshi"
    rows = [hit(1, "タイムM", "normal", 25, "keshi", key=low), {**hit(1, "タイムM", "normal", 22, "axis", family="a"), "key": low}]
    b = app.classify_hits(rows)
    assert len(b["keshi"]) == 1 and b["axis"] == []


def test_note_cell_and_condition_of_escape_and_link():
    """「危険条件メモ / 調査」のセル: メモは赤の太字・title に全文・別タブで逆引きへ。HTML はエスケープする。"""
    h = hit(3, "SAV", "odds10u", 30, "axis", family="rankB", scheme="s2", region="全場")
    assert app.condition_of(h) == {"region": "全場", "indicator": "SAV", "family": "rankB", "rank_scheme": "s2",
                                   "odds_mode": "odds10u", "key": KEY}
    assert app.condition_of({"indicator": "ZI", "base": "x", "odds_mode": None, "key": None}) == {
        "region": "", "indicator": "ZI", "family": "", "rank_scheme": "", "odds_mode": "normal", "key": ""}
    cell = str(app.note_cell('⚠️<b>危険</b> "478kg"', "/entries/drill?key=a&b=1"))
    assert cell.startswith('<td class="note" title="⚠️&lt;b&gt;危険&lt;/b&gt; &#34;478kg&#34;">')
    assert '<a class="notetext" style="color:#b91c1c;font-weight:700" href="/entries/drill?key=a&amp;b=1" target="_blank">⚠️&lt;b&gt;危険&lt;/b&gt;' in cell
    assert cell.count('target="_blank"') == 2 and ">調査</a>" in cell and "<b>" not in cell
    plain = str(app.note_cell("", "/d"))
    assert plain == '<td class="note"><a href="/d" target="_blank">調査</a></td>'
    assert str(app.note_cell("", "/d", "/k")) == ('<td class="note"><a href="/d" target="_blank">調査</a> ／ '
                                                  '<a href="/k" target="_blank">成績表</a></td>')
    long = str(app.note_cell("あ" * 500, "/d"))
    assert "あ" * app.NOTE_SHOW + "…</a>" in long and 'title="' + "あ" * 500 + '"' in long


# ---------------------------------------------------------------- 条件特化ヒット（指数が下位でも軸の基準を満たす行）
JK = "騎手A×前走1着"


def low(uma, base, mode, cnt, cat, tail="6~9位", head=JK, family="B_jk_prevfin", scheme="s1", region="主5場", **more):
    """末尾が下位の結合データのヒット行。4区分ではどれにもならず、条件特化ヒットの元区分が cat。"""
    h = {"馬番": str(uma), "枠番": "1", "馬名": f"馬{uma}", "騎手": "騎手", "base": base, "family": family,
         "rank_scheme": scheme, "odds_mode": mode, "region": region, "key": f"{head}×{tail}", "件数": cnt,
         "テーブル名": f"t_{mode}_{family}_{scheme}_{region}_{base}_{tail}", **STATS[cat], **more}
    assert app.hit_category(h) is None and app.low_tail_category(h) == cat, (cat, cnt, mode, tail)
    return h


def test_special_hits_group_low_index_axis_rows_into_one_row_per_condition():
    """条件特化ヒット: 同じ馬・パターン・スキーム・オッズ帯・地域・条件（末尾を除いた部分）の行は代表の1行
    （複勝率_val 最大 → 件数 最大）にまとめ、元区分と、当たった指数（台帳の順）を持つ。"""
    assert [app.condition_part(k) for k in ("A×B×6位~", "6位~", "", None, "A×10")] == ["A×B", "", "", "", "A"]
    a = low(1, "先行", "normal", 30, "axis")                                            # 複勝率 82
    b = low(1, "瞬発", "normal", 22, "axis", tail="10位~", **{"複勝率_val": 85.0})       # 複勝率が最大 → 代表
    c = low(1, "SAV", "normal", 12, "abs", **{"複勝率_val": 84.0})
    out = app.special_hits([a, b, c], False, info := {})
    assert len(out) == 1 and info == {"special_cut": 0}
    row = out[0]
    assert row["base"] == "瞬発" and row["key"] == f"{JK}×10位~" and row["_cat"] == "axis" and row["件数"] == 22
    assert (row["_n_inds"], row["_n_rows"]) == (3, 3)
    assert [(m["base"], m["cat"], m["tail"]) for m in row["_members"]] == [
        ("SAV", "abs", "6~9位"), ("先行", "axis", "6~9位"), ("瞬発", "axis", "10位~")]      # 台帳の順
    assert [m["h"] for m in row["_members"]] == [c, a, b]
    assert a.get("_cat") is None and "_members" not in b                                 # もとのヒット行は書き換えない
    # 複勝率が同じなら件数の多い行、それも同じなら指数（台帳の順）
    d = low(1, "ZI", "normal", 40, "axis", **{"複勝率_val": 85.0})
    assert app.special_hits([a, b, c, d])[0]["base"] == "ZI"
    e = low(1, "Gold", "normal", 22, "axis", **{"複勝率_val": 85.0})
    assert app.special_hits([b, e])[0]["base"] == "Gold" and app.special_hits([e, b])[0]["base"] == "Gold"
    # まとめないもの: 馬・パターン・スキーム・オッズ帯・地域・条件の部分のどれかが違う行
    others = [low(2, "先行", "normal", 30, "axis"), low(1, "先行", "normal", 30, "axis", family="B_jk_prevpop"),
              low(1, "先行", "normal", 30, "axis", scheme="s2", tail="6位~"), low(1, "先行", "odds10_50", 30, "axis"),
              low(1, "先行", "normal", 30, "axis", region="全場"), low(1, "先行", "normal", 30, "axis", head="騎手A×前走2~3着")]
    out = app.special_hits([a, b, c] + others)
    assert len(out) == 7 and sorted(r["_n_inds"] for r in out) == [1, 1, 1, 1, 1, 1, 3]
    # 入らないもの: 末尾が上位の軸・相手候補の基準まで・消し候補・どの区分でもない行
    plain = [hit(1, "先行", "normal", 30, "axis"), hit(1, "先行", "normal", 40, "opp"), hit(1, "先行", "normal", 25, "keshi"),
             {**low(1, "BC", "normal", 30, "axis"), **STATS["opp"]}, {**low(1, "BC", "normal", 30, "axis"), **STATS["keshi"]},
             {**low(1, "BC", "normal", 30, "axis"), "件数": 19}]
    assert app.special_hits(plain) == []
    # 4区分の表には出ない（ゲートはそのまま）。行の順に依らない
    assert all(v == [] for v in app.classify_hits([a, b, c] + others).values())
    import random as _r
    rows = [a, b, c, d, e] + others
    want = [(r["馬番"], r["base"], r["key"], r["_n_inds"]) for r in app.special_hits(rows)]
    for seed in range(5):
        sh = rows[:]
        _r.Random(seed).shuffle(sh)
        assert [(r["馬番"], r["base"], r["key"], r["_n_inds"]) for r in app.special_hits(sh)] == want
    # 同じ指数が同じ条件に2行（別の集計表）あっても、指数は1つに数える
    twin = {**a, "テーブル名": "t_other", "件数": 31}
    row = app.special_hits([a, twin])[0]
    assert (row["_n_inds"], row["_n_rows"], row["件数"]) == (1, 2, 31) and row["_members"][0]["h"] is twin


def test_special_hits_use_each_rows_own_odds_band_and_count_indicators_not_rows():
    """元区分はその行のオッズ帯で決める（高回収率の基準は 2〜10倍未満・10〜50倍だけ。2〜10倍未満専用の消しは対象外）。
    指数数は指数の数（同じ指数の行が2つあっても1つ）。"""
    hr = {"複勝率_val": 36.0, "連対率_val": 10.0, "掲示板率_val": 55.0, "複勝回収値": 0.0, "単勝適正回収値": 210.0}
    rows = [{**low(1, "先行", "normal", 30, "axis"), **hr, "odds_mode": m, "テーブル名": f"t_{m}"}
            for m in ("normal", "odds10u", "odds10_50", "odds50p")]
    assert [app.low_tail_category(h) for h in rows] == [None, "axis", "axis", None]
    assert [r["odds_mode"] for r in app.special_hits(rows)] == ["odds10u", "odds10_50"]
    # 2〜10倍未満では「複勝率 < 20 かつ WEDGE < 0.350（または掲示板率 < 35）」は消し候補（末尾に関係なく先に決まる）→ 絶対軸の
    # 数字でも入らない。ほかのオッズ帯では絶対軸の基準
    ab = {"複勝率_val": 15.0, "連対率_val": 85.0, "掲示板率_val": 60.0, "複勝回収値": 150.0, "WEDGE": 0.3}
    fav, mid = ({**low(2, "SAV", "normal", 25, "abs"), **ab, "odds_mode": m, "テーブル名": f"u_{m}"} for m in ("odds10u", "odds10_50"))
    assert app.hit_category(fav) == "keshi" and app.low_tail_category(fav) is None and app.low_tail_category(mid) == "abs"
    assert [(r["馬番"], r["odds_mode"], r["_cat"]) for r in app.special_hits([fav, mid])] == [("2", "odds10_50", "abs")]
    # WEDGE が 0.350 以上（掲示板率も 35 以上）なら 2〜10倍未満でも消しではない → 条件特化ヒットに入る
    kept = {**fav, "WEDGE": 0.8}
    assert app.hit_category(kept) is None and app.low_tail_category(kept) == "abs"
    assert [(r["odds_mode"], r["_cat"]) for r in app.special_hits([kept, mid])] == [("odds10u", "abs"), ("odds10_50", "abs")]
    # 絶対軸の条件②（件数 20・勝率 40・複勝率 80）で条件特化ヒットになる行
    two = {**low(4, "SAV", "normal", 25, "axis"), "勝率_val": 45.0, "連対率_val": 55.0, "単勝適正回収値": 110.0, "掲示板率_val": 85.0}
    assert app.low_tail_category(two) == "abs" and app.low_tail_category({**two, "勝率_val": 39.0}) is None
    assert app.low_tail_category({**two, "件数": 19}) is None
    # 希少条件: 軸候補の最低件数（6）は下がるが、絶対軸は下げない（10件から）
    rare = low(3, "ZI", "normal", 12, "abs", head="東京×芝×23-24×定量×G1", tail="7")
    assert [app.low_tail_category({**rare, "件数": n}) for n in (5, 6, 9, 10)] == [None, "axis", "axis", "abs"]
    assert app.low_tail_category({**low(3, "ZI", "normal", 12, "abs"), "件数": 9}) is None
    # 指数数 = 指数の数。地域・オッズ帯の None と空・通常は同じ組（馬番は数でも文字でも）
    a = low(1, "先行", "normal", 30, "axis")
    twin = {**a, "テーブル名": "t_twin", "馬番": 1, "odds_mode": None}
    b = {**low(1, "瞬発", "normal", 30, "axis"), "region": "主5場"}
    row = app.special_hits([a, twin, b])
    assert len(row) == 1 and (row[0]["_n_inds"], row[0]["_n_rows"]) == (2, 3)
    assert len(app.special_hits([{**a, "region": None}, {**b, "region": ""}])) == 1


def test_special_hits_follow_the_overpopular_cut_and_the_table_order():
    """過剰人気カット ON なら、2〜10倍未満（odds10u）で複勝回収値も単勝適正回収値も 100 未満の行を先に除く（ほかのオッズ帯は対象外）。"""
    weak = low(1, "先行", "odds10u", 30, "axis")                                          # 回収値 80 / 80
    good = low(1, "瞬発", "odds10u", 30, "axis", **{"複勝回収値": 100.0})
    tra = low(2, "SAV", "odds10u", 30, "axis", **{"単勝適正回収値": 130.0})
    none = low(3, "ZI", "odds10u", 30, "axis", **{"複勝回収値": None, "単勝適正回収値": None})
    ab = low(4, "BC", "odds10u", 12, "abs")                                               # 絶対軸は回収値100以上が条件
    other = [low(1, "先行", m, 30, "axis") for m in ("normal", "odds10_50", "odds50p")]
    rows = [weak, good, tra, none, ab] + other
    off = app.special_hits(rows, False, info := {})
    assert info == {"special_cut": 0} and len(off) == 7
    assert next(r for r in off if r["馬番"] == "1" and r["odds_mode"] == "odds10u")["_n_inds"] == 2
    on = app.special_hits(rows, True, info := {})
    assert info == {"special_cut": 2} and len(on) == 6 and all(r["馬番"] != "3" for r in on)
    one = next(r for r in on if r["馬番"] == "1" and r["odds_mode"] == "odds10u")
    assert one["base"] == "瞬発" and one["_n_inds"] == 1                                 # カットした行は代表にも指数数にも入らない
    # 並び: オッズ帯（2〜10倍未満 → 10〜50倍 → 50倍以上 → 通常）→ 馬番 → 複勝率の大きい順
    assert [(r["odds_mode"], r["馬番"]) for r in on] == [("odds10u", "1"), ("odds10u", "2"), ("odds10u", "4"),
                                                         ("odds10_50", "1"), ("odds50p", "1"), ("normal", "1")]

