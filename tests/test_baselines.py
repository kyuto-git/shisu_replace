"""SP指数・ST指数の3着内基準値（shisu/baselines.py）と、基準比・過去SP/ST 3走順の条件。"""
import csv
import hashlib
import io
import json
import random
import re
import shutil
import sqlite3
import statistics
from fractions import Fraction
from pathlib import Path

import pandas as pd
import pytest

from shisu import baselines as B
from shisu import conditions as C
from shisu import families as F
from shisu import past_perf_indices as PP
from tests import synth2

RID = "レースID(新)"
REPO_DATA = Path(__file__).resolve().parent.parent / "shisu" / "data"


# ---------------------------------------------------------------------------
# 別に書いた計算（oracle）
# ---------------------------------------------------------------------------
def o_cells(path) -> dict:
    with open(path, encoding="utf-8", newline="") as f:
        return {r["key"]: int(r["index_mean"]) for r in csv.DictReader(f)}


def o_lookup(cells: dict, sd, cls, track, pg, band):
    if cls is None:
        return None
    for key in (f"{sd} × {cls} × {track} × {pg} × {band}", f"{sd} × {cls} × {track} × {band}", f"{sd} × {cls} × {track}"):
        if key in cells:
            return cells[key]
    return None


O_TRACK = {"芝": {"良": "良", "稍": "良以外", "重": "良以外", "不": "良以外", "": "良"},
           "ダ": {"良": "良稍", "稍": "良稍", "重": "重不良", "不": "重不良", "": "良稍"}}
O_BAND = {("芝", 1600): "芝マイル", ("芝", 2000): "芝中距離", ("芝", 1800): "芝マイル", ("ダ", 1400): "ダマイル",
          ("ダ", 1600): "ダマイル", ("ダ", 1200): "ダ短距離"}
O_PG = {"東京": "主場", "中山": "主場"}
# 合成データのレースに割り当てる (クラス名, 年齢限定) → (集計のクラス, 引くときのクラス)
RACE_KINDS = [(("未勝利", "3歳"), "3歳未勝利", "3歳未勝利"), (("1勝", "3上"), "古馬1勝", "古馬1勝"), (("2勝", "4上"), "2勝", "2勝"),
              (("オープン", "3上"), "古馬OP", "古馬OP"), (("Ｇ３", "3上"), "古馬重賞", "古馬重賞"), (("G1", "3上"), None, "古馬重賞"),
              (("新馬", "2歳"), None, None), (("未勝利", "2歳"), "2歳未勝利", "2歳未勝利"), (("500万", "3歳"), "3歳1勝", "3歳1勝"),
              (("OP(L)", "3歳"), "3歳OP", "3歳OP"), (("1600万", "3上"), "3勝", "3勝")]
O_CLASS = {k: (build, look) for k, build, look in RACE_KINDS}


def o_prev_label(kind, d):
    hi, lo = {"SP": (3, -6), "ST": (2, -4)}[kind]
    word = "突破" if d >= hi else ("クリア" if d >= 0 else ("未達" if d > lo else "大幅未達"))
    return f"前走{kind}基準{word}"


def o_now_label(kind, a3, a5, m3, m5, b):
    vals = [a3, a5, m3, m5]
    if b is None or all(v is None for v in vals):
        return ""
    if any(v is not None and v >= b for v in (a3, a5)):
        return f"{kind}平均基準超"
    if m3 is not None and m3 >= b:
        return f"{kind}3走最高基準超"
    return f"{kind}5走最高基準超" if m5 is not None and m5 >= b else f"{kind}今走基準未達"


def o_build(samples, decay):
    """(芝ダ, クラス, 馬場区分, 場所区分, 距離帯, 年, 値) → {key: 行}。依頼の手順をそのまま書いたもの。"""
    ymax = max(s[5] for s in samples)
    d = Fraction(str(float(decay)))
    items = [(s[:5], s[6], d ** (ymax - s[5])) for s in samples]
    wmean = lambda xs: sum(Fraction(str(v)) * w for v, w in xs) / sum(w for _v, w in xs)   # 分数で（誤差なし）
    root = round(wmean([(v, w) for _k, v, w in items]))
    levels = {"L3": lambda k: (k[0], k[1], k[2]), "L2": lambda k: (k[0], k[1], k[2], k[4]), "L1": lambda k: k}
    parents = {"L3": lambda key: None, "L2": lambda key: key[:3], "L1": lambda key: key[:3] + key[4:]}
    out, final = {}, {}
    for level in ("L3", "L2", "L1"):
        groups = {}
        for k, v, w in items:
            groups.setdefault(levels[level](k), []).append((v, w))
        for key, xs in groups.items():
            xs = sorted(xs, key=lambda t: (t[0], -t[1]))
            n = len(xs)
            cut = n // 3
            kept = xs[: n - cut]
            m = round(wmean(kept))
            pkey = parents[level](key)
            base = root if pkey is None else final[pkey]
            mean = round(Fraction(n * m + 30 * base, n + 30))
            final[key] = mean
            out[" × ".join(key)] = {
                "level": level, "n": n, "index_mean": mean, "reliability": "高" if n >= 100 else ("中" if n >= 30 else "低"),
                "index_raw": m, "index_mean_全体": round(wmean(xs)), "n_kept": n - cut, "n_cut": cut,
                "index_std": round(statistics.pstdev([v for v, _w in kept]), 2), "p25": float(xs[min(n - 1, int(n * 0.25))][0]),
                "median": float(xs[min(n - 1, int(n * 0.5))][0]), "p75": float(xs[min(n - 1, int(n * 0.75))][0]),
                "parent": "（全体平均）" if pkey is None else " × ".join(pkey)}
    return out, root


def rows_by_key(rows):
    return {r["key"]: {k: v for k, v in r.items() if k != "key"} for r in rows}


# ---------------------------------------------------------------------------
# 受け取った基準値マスタ（shisu/data/）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind,root", [("SP", 62), ("ST", 48)])
def test_shipped_masters_follow_the_shrinkage_formula(kind, root, tmp_path):
    path = REPO_DATA / B.FILES[kind]
    data = path.read_bytes()
    assert data.startswith(b"level,key,n,index_mean,reliability,index_raw,") and data.count(b"\r\n") == 468
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    assert list(rows[0]) == B.COLUMNS and len(rows) == 467
    by = {r["key"]: r for r in rows}
    for r in rows:                                   # 縮約の式・丸め（round = 偶数丸め）・件数の切り方・信頼度が全セルで合う
        n, m = int(r["n"]), int(r["index_raw"])
        base = root if r["level"] == "L3" else int(by[r["parent"]]["index_mean"])
        assert B._shrink(n, m, base) == int(r["index_mean"]), r["key"]
        assert int(r["n_cut"]) == n // 3 and int(r["n_kept"]) == n - n // 3 and B._reliability(n) == r["reliability"]
        assert (r["parent"] == B.ROOT_LABEL) == (r["level"] == "L3")
    table = B.load(kind, REPO_DATA)
    assert len(table) == 467 and table.cells == {r["key"]: int(r["index_mean"]) for r in rows}
    resaved = tmp_path / B.FILES[kind]                     # Excel などで cp932・BOM つき UTF-8 に保存し直したものも読める
    for enc in ("cp932", "utf-8-sig"):
        resaved.write_bytes(data.decode("utf-8").encode(enc))
        assert B.load(kind, tmp_path).cells == table.cells, enc
    # 引き方: L1 → L2 → L3
    assert table.lookup("芝", "古馬1勝", "良", "主場", "芝マイル") == int(by["芝 × 古馬1勝 × 良 × 主場 × 芝マイル"]["index_mean"])
    assert table.lookup("芝", "古馬1勝", "良", "", "芝マイル") == int(by["芝 × 古馬1勝 × 良 × 芝マイル"]["index_mean"])
    assert table.lookup("芝", "古馬1勝", "良") == int(by["芝 × 古馬1勝 × 良"]["index_mean"])
    assert table.lookup("芝", "古馬G1", "良", "主場", "芝マイル") == table.lookup("芝", "古馬重賞", "良", "主場", "芝マイル")   # G1 → 重賞
    assert table.lookup("芝", "新馬", "良", "主場", "芝マイル") is None and table.lookup("芝", "", "良") is None
    assert table.lookup("障", "古馬1勝", "良") is None and table.lookup("芝", "古馬1勝", "重不良") is None


def test_lookup_falls_back_level_by_level():
    t = B.Baseline("SP", {"芝 × 2勝 × 良": 60, "芝 × 2勝 × 良 × 芝マイル": 62, "芝 × 2勝 × 良 × 主場 × 芝マイル": 65,
                          "ダ × 2勝 × 良稍": 50})
    assert t.lookup("芝", "2勝", "良", "主場", "芝マイル") == 65
    assert t.lookup("芝", "2勝", "良", "ローカル", "芝マイル") == 62            # L1 が無ければ L2
    assert t.lookup("芝", "2勝", "良", "主場", "芝長距離") == 60                # L2 も無ければ L3
    assert t.lookup("ダ", "2勝", "良稍", "主場", "ダマイル") == 50
    assert t.lookup("ダ", "2勝", "重不良", "主場", "ダマイル") is None          # L3 も無ければ None
    assert t.lookup("芝", "3勝", "良", "主場", "芝マイル") is None
    text = "level,key,index_mean\nL3,芝×2勝×良,60\nL3, ダ × 2勝 × 良稍 ,51.0\nL3,bad,x\nL3,芝×3勝×良,1e999\nL3,芝×3勝×良以外,nan\n"
    got = B.parse_csv(text)                                                       # × の前後の空白はそろえる・読めない行は飛ばす
    assert got.cells == {"芝 × 2勝 × 良": 60, "ダ × 2勝 × 良稍": 51}
    for bad in ("", "a,b\n1,2\n", "level,key,index_mean\n", "level,key,index_mean\nL3,芝×2勝×良," + "9" * 140000 + "\n"):
        with pytest.raises(ValueError):
            B.parse_csv(bad)


# ---------------------------------------------------------------------------
# セルの部品
# ---------------------------------------------------------------------------
def test_generation_and_race_class():
    g = B.generation
    assert [g(a) for a in ("2歳", "３歳", "3上", "3歳以上", "4上", "4歳以上", "2", "3", "")] == \
        ["2歳", "3歳", "古馬", "古馬", "古馬", "古馬", "2歳", "3歳", ""]
    assert g("", "未勝利", "3歳未勝利") == "3歳" and g("", "1勝", "3歳以上1勝クラス") == "古馬" and g("", "1勝", "") == ""
    assert g("xyz", "2歳未勝利", "") == "2歳"                                    # 年齢として読めない値は クラス名・レース名 から
    rc = B.race_class
    assert [rc(c, a) for c, a in (("未勝利", "2歳"), ("未勝利", "3歳"), ("1勝", "2歳"), ("500万", "3歳"), ("1勝クラス", "3上"),
                                  ("1000万", "4上"), ("2勝", ""), ("1600万", "3上"), ("3勝", ""), ("オープン", "2歳"),
                                  ("OP(L)", "3歳"), ("ｵｰﾌﾟﾝ", "3上"), ("L", "4上"), ("Ｇ３", "2歳"), ("G2", "3歳"), ("重賞", "3上"),
                                  ("GIII", "4上"))] == \
        ["2歳未勝利", "3歳未勝利", "2歳1勝", "3歳1勝", "古馬1勝", "2勝", "2勝", "3勝", "3勝", "2歳OP", "3歳OP", "古馬OP", "古馬OP",
         "2歳重賞", "3歳重賞", "古馬重賞", "古馬重賞"]
    assert [rc(c, "3上") for c in ("(L)", "リステッド", "OP", "ＯＰ（Ｌ）", "オープン")] == ["古馬OP"] * 5
    assert set(rc(c, a) for c in ("未勝利", "1勝", "2勝", "3勝", "オープン", "G3") for a in ("2歳", "3歳", "3上")) - {""} \
        == set(B.CLASSES) | {"2勝", "3勝"} and len(B.CLASSES) == 13
    assert rc("新馬", "2歳") == "新馬" and rc("Ｇ１", "3上") == "古馬G1" and rc("GI", "3歳") == "3歳G1" and rc("G1", "2歳") == "2歳G1"
    assert rc("未勝利", "3上") == "" and rc("1勝", "") == "" and rc("オープン", "") == "" and rc("", "3上") == ""
    assert rc("JG1", "3上") == "" and rc("J･G3", "3上") == "" and rc("障害未勝利", "3上") == ""       # 障害は対象外
    assert rc("11勝", "3上") == "" and rc("21勝", "") == ""                       # 数字の続きを 1勝 と読まない
    assert rc("未勝利", "", "2歳未勝利") == "2歳未勝利" and rc("1勝", "", "3歳以上1勝クラス") == "古馬1勝"
    lc = B.lookup_class
    assert lc("古馬G1") == "古馬重賞" and lc("2歳G1") == "2歳重賞" and lc("3歳G1") == "3歳重賞"
    assert lc("新馬") is None and lc("") is None and lc("古馬1勝") == "古馬1勝" and lc("2勝") == "2勝"


def test_track_place_group_and_distance_band():
    assert [B.baba4(v) for v in ("良", "稍", "稍重", "重", "不", "不良", "稍・重", "", "x")] == ["良", "稍", "稍", "重", "不", "不", "稍・重", "", ""]
    t = B.track_of
    assert [t("芝", b) for b in ("良", "稍", "稍重", "重", "不良", "稍・重")] == ["良", "良以外", "良以外", "良以外", "良以外", "良以外"]
    assert [t("ダ", b) for b in ("良", "稍", "稍重", "重", "不良")] == ["良稍", "良稍", "良稍", "重不良", "重不良"]
    assert t("ダ", "稍・重") == "良稍"                              # 想定の「稍・重」は、ダでは 稍重 の側
    assert t("芝", "") == "良" and t("ダ", "") == "良稍" and t("芝", "?") == "良"      # 分からなければ 良 として引く
    assert t("芝", "", strict=True) == "" and t("ダ", "稍・重", strict=True) == "" and t("ダ", "重", strict=True) == "重不良"
    assert t("障", "良") == "" and t("", "良") == ""
    assert [B.norm_baba_choice(v) for v in ("", "auto", "自動", "良", "稍重", "重", "不良", "稍・重")] == \
        ["auto", "auto", "auto", "良", "稍", "重", "不", "稍・重"]
    with pytest.raises(ValueError):
        B.norm_baba_choice("晴")
    assert [B.place_group(p) for p in ("東京", "中山", "京都", "阪神", "中京")] == ["主場"] * 5
    assert [B.place_group(p) for p in ("函館", "札幌", "福島", "新潟", "小倉")] == ["ローカル"] * 5
    assert B.place_group("大井") == "" and B.place_group("") == "" and B.place_group(" 東 京 ") == "主場"
    d = B.dist_band
    assert [d("芝", x) for x in (1000, 1400, 1401, 1800, 1801, 2400, 2401, 3600)] == \
        ["芝短距離", "芝短距離", "芝マイル", "芝マイル", "芝中距離", "芝中距離", "芝長距離", "芝長距離"]
    assert [d("ダ", x) for x in (1000, 1300, 1301, 1600, 1601, 2000, 2001, 2500)] == \
        ["ダ短距離", "ダ短距離", "ダマイル", "ダマイル", "ダ中距離", "ダ中距離", "ダ長距離", "ダ長距離"]
    assert d("芝", "1600") == "芝マイル" and d("芝", "") == "" and d("芝", float("nan")) == "" and d("障", 3000) == "" and d("芝", 0) == ""
    assert B.cell_keys("芝", "2勝", "良", "主場", "芝マイル") == {
        "L3": "芝 × 2勝 × 良", "L2": "芝 × 2勝 × 良 × 芝マイル", "L1": "芝 × 2勝 × 良 × 主場 × 芝マイル"}
    assert list(B.cell_keys("芝", "2勝", "良", "", "芝マイル")) == ["L3", "L2"] and list(B.cell_keys("芝", "2勝", "良", "主場", "")) == ["L3"]
    assert B.cell_keys("芝", "", "良") == {} and B.cell_keys("芝", "2勝", "") == {}


# ---------------------------------------------------------------------------
# 生成（上位1/3カット・年度の重み・縮約）
# ---------------------------------------------------------------------------
def random_samples(seed, n=900, years=(2022, 2023, 2024, 2025, 2026), integer=True):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        sd = rng.choice(["芝", "ダ"])
        cls = rng.choice(B.CLASSES[:6] if rng.random() < 0.8 else B.CLASSES)
        band = rng.choice([b for _hi, b in B.BANDS[sd]])
        v = rng.randint(20, 100) if integer else round(rng.uniform(20, 100), 1)
        out.append((sd, cls, rng.choice(B.TRACKS[sd]), rng.choice(B.PGROUPS), band, rng.choice(years), v))
    return out


def test_hand_worked_example_of_the_cut_mean_and_shrinkage():
    # 芝×2勝×良（9件・同じ年）: 小さい順 40 50 52 55 58 60 | 70 80 90 → 上位 9//3 = 3件を除いた6件の平均 52.5 → round → 52（偶数丸め）
    vals = [58, 40, 90, 55, 70, 50, 80, 52, 60]
    samples = [("芝", "2勝", "良", "主場", "芝マイル", 2026, v) for v in vals]
    rows, info = B.build_rows(samples, decay=1.0)
    by = rows_by_key(rows)
    root = round(sum(vals) / 9)                                        # 全体平均はカットなし: 555 / 9 = 61.67 → 62
    assert info == {"root": 62, "n": 9, "year_max": 2026, "decay": 1.0} and root == 62
    l3 = by["芝 × 2勝 × 良"]
    assert (l3["n"], l3["n_cut"], l3["n_kept"], l3["index_raw"], l3["index_mean_全体"]) == (9, 3, 6, 52, 62)
    assert l3["index_mean"] == round((9 * 52 + 30 * 62) / 39) == 60 and l3["parent"] == "（全体平均）" and l3["reliability"] == "低"
    assert (l3["p25"], l3["median"], l3["p75"]) == (52.0, 58.0, 70.0)   # 並べた値の int(n × 0.25 / 0.5 / 0.75) 番目
    assert l3["index_std"] == round(statistics.pstdev([40, 50, 52, 55, 58, 60]), 2)
    l2, l1 = by["芝 × 2勝 × 良 × 芝マイル"], by["芝 × 2勝 × 良 × 主場 × 芝マイル"]
    assert l2["parent"] == "芝 × 2勝 × 良" and l2["index_mean"] == round((9 * 52 + 30 * 60) / 39) == 58
    assert l1["parent"] == "芝 × 2勝 × 良 × 芝マイル" and l1["index_mean"] == round((9 * 52 + 30 * 58) / 39) == 57
    assert [r["level"] for r in rows] == ["L3", "L2", "L1"]
    # 端数は残す側: 5件 → 5 // 3 = 1件を除く。2件 → 除かない。1件だけのセル
    r5, _ = B.build_rows([("ダ", "3勝", "良稍", "ローカル", "ダ短距離", 2026, v) for v in (10, 20, 30, 40, 100)], 1.0)
    assert (r5[0]["n_cut"], r5[0]["n_kept"], r5[0]["index_raw"]) == (1, 4, 25)
    r2, _ = B.build_rows([("ダ", "3勝", "良稍", "ローカル", "ダ短距離", 2026, v) for v in (91, 95)], 1.0)
    assert (r2[0]["n_cut"], r2[0]["index_raw"], r2[0]["index_std"], r2[0]["p25"], r2[0]["median"], r2[0]["p75"]) == (0, 93, 2.0, 91.0, 95.0, 95.0)
    r1, _ = B.build_rows([("ダ", "3勝", "良稍", "ローカル", "ダ短距離", 2026, 70)], 1.0)
    assert (r1[0]["n"], r1[0]["index_raw"], r1[0]["index_std"], r1[0]["index_mean"]) == (1, 70, 0.0, 70)


def test_year_decay_weights_recent_seasons():
    # 残した値の重みつき平均: 2026年の 60・60 と 2024年の 30・30（上位カットは件数で決まる）。6件 → 2件を除く
    mk = lambda year, v: ("芝", "2勝", "良", "主場", "芝マイル", year, v)
    samples = [mk(2026, 60), mk(2026, 60), mk(2024, 30), mk(2024, 30), mk(2026, 99), mk(2024, 98)]
    equal, _ = B.build_rows(samples, 1.0)
    assert equal[0]["index_raw"] == 45                                   # (60 + 60 + 30 + 30) / 4
    rows, info = B.build_rows(samples, 0.5)                              # 2024年の重み = 0.5 ** 2 = 0.25
    assert rows[0]["index_raw"] == round((60 + 60 + 0.25 * 30 + 0.25 * 30) / 2.5) == 54 and info["year_max"] == 2026
    assert rows[0]["index_mean_全体"] == round((60 + 60 + 99 + 0.25 * (30 + 30 + 98)) / 3.75)
    assert (rows[0]["n"], rows[0]["n_cut"]) == (6, 2)                    # 件数と切る数は重みに依らない
    # 切れ目に同じ値がまたがるときは、古い（重みの小さい）ほうから除く。並びには依らない
    tie = [mk(2026, 50), mk(2020, 50), mk(2026, 40), mk(2026, 41), mk(2026, 42), mk(2026, 43)]       # 6件 → 50 の2つを除く
    a, _ = B.build_rows(tie, 0.65)
    tie2 = [mk(2026, 50), mk(2020, 50), mk(2026, 50), mk(2026, 10), mk(2026, 10), mk(2026, 10)]      # 50 が3つ・除くのは2つ
    b1, _ = B.build_rows(tie2, 0.65)
    b2, _ = B.build_rows(list(reversed(tie2)), 0.65)
    assert a[0]["index_raw"] == round((40 + 41 + 42 + 43) / 4) and b1 == b2 and b1[0]["index_raw"] == 20   # 残るのは 2026年の 50
    for bad in (0, -0.1, 1.01):
        with pytest.raises(ValueError):
            B.build_rows(samples, bad)
    with pytest.raises(ValueError):
        B.build_rows([], 0.65)


def test_exact_half_ties_and_the_weighted_root():
    mk = lambda year, v, cls="2勝": ("芝", cls, "良", "主場", "芝マイル", year, v)
    other = [("ダ", "3勝", "良稍", "ローカル", "ダ短距離", 2026, 70)]
    # 残した値の平均がちょうど .5 のセルは、重みが全部同じ（同じ年）なら重みに依らず偶数丸め。
    # （float で重みを掛けると 43.49999999999999 のようにずれて、別のセルに新しい年の標本があるだけで丸めが変わる）
    for year in (2026, 2025, 2022, 2020, 2017):
        for a in range(20, 60):
            for decay in (1.0, 0.65, 0.9):
                rows, _ = B.build_rows([mk(year, a), mk(year, a + 1), *other], decay)
                assert rows_by_key(rows)["芝 × 2勝 × 良"]["index_raw"] == (a if a % 2 == 0 else a + 1), (year, a, decay)
    # 2つの年に同じ組（a・a+1）があっても、平均は a + 0.5 ちょうど
    for a in range(20, 60):
        rows, _ = B.build_rows([mk(2026, a), mk(2026, a + 1), mk(2023, a), mk(2023, a + 1), *other], 0.65)
        assert rows_by_key(rows)["芝 × 2勝 × 良"]["index_mean_全体"] == (a if a % 2 == 0 else a + 1), a
    # 全体平均（L3 の縮約の親）: カットなし・年度の重みつき・偶数丸め
    assert B.build_rows([mk(2026, 52), mk(2026, 53)], 0.65)[1]["root"] == 52            # 52.5 → 52
    assert B.build_rows([mk(2026, 53), mk(2026, 54)], 0.65)[1]["root"] == 54            # 53.5 → 54
    old_high = [mk(2026, 40)] * 3 + [("ダ", "3勝", "良稍", "ローカル", "ダ短距離", 2020, 90)] * 3
    rows, info = B.build_rows(old_high, 0.5)                                            # 2020年の重み = 0.5 ** 6 = 1/64
    assert info["root"] == round(Fraction(120 * 64 + 270, 3 * 64 + 3)) == 41            # 重みなしなら 65
    assert rows_by_key(rows)["芝 × 2勝 × 良"]["index_mean"] == round(Fraction(3 * 40 + 30 * 41, 33)) == 41   # 重みなしの親なら 63
    assert B.build_rows(old_high, 1.0)[1]["root"] == 65
    # 縮約もちょうど .5 は偶数丸め: (30 × 51 + 30 × 50) / 60 = 50.5 → 50
    assert B._shrink(30, 51, 50) == 50 and B._shrink(30, 52, 51) == 52 and B._shrink(10, 60, 62) == 62
    # 信頼度の境目
    assert [B._reliability(n) for n in (1, 29, 30, 99, 100, 5000)] == ["低", "低", "中", "中", "高", "高"]
    for n, want in ((29, "低"), (30, "中"), (99, "中"), (100, "高")):
        assert B.build_rows([mk(2026, 50)] * n, 1.0)[0][0]["reliability"] == want


@pytest.mark.parametrize("seed,decay,integer", [(1, 1.0, True), (2, 0.65, True), (3, 0.9, False), (4, 0.65, True), (5, 1.0, False)])
def test_build_rows_equals_an_independent_calculation(seed, decay, integer):
    samples = random_samples(seed, integer=integer)
    rows, info = B.build_rows(samples, decay)
    want, root = o_build(samples, decay)
    got = rows_by_key(rows)
    assert info["root"] == root and info["n"] == len(samples) and set(got) == set(want)
    for key in want:
        assert got[key] == want[key], key
    # 並び: L3 → L2 → L1、その中は 芝 → ダ・クラス（CLASSES の順）・馬場区分・場所区分・距離帯。入力の並びを変えても同じ表
    assert [r["level"] for r in rows] == sorted((r["level"] for r in rows), key=["L3", "L2", "L1"].index)
    def order(r):
        parts = r["key"].split(" × ")
        sd = parts[0]
        pg = parts[3] if r["level"] == "L1" else ""
        band = parts[-1] if r["level"] != "L3" else ""
        bands = ["", *(b for _hi, b in B.BANDS[sd])]
        return (["L3", "L2", "L1"].index(r["level"]), ["芝", "ダ"].index(sd), B.CLASSES.index(parts[1]), B.TRACKS[sd].index(parts[2]),
                ["", "主場", "ローカル"].index(pg), bands.index(band))
    assert rows == sorted(rows, key=order)
    shuffled = samples[:]
    random.Random(seed + 100).shuffle(shuffled)
    assert B.build_rows(shuffled, decay)[0] == rows
    text = B.to_csv_text(rows)
    assert text.startswith(",".join(B.COLUMNS) + "\r\n") and text.count("\r\n") == len(rows) + 1
    assert B.parse_csv(text).cells == {r["key"]: r["index_mean"] for r in rows}


def history_frame(rows):
    """(着順, SP, ST, 芝ダ, 距離, 場所, 日付, クラス, 馬場4) → 履歴の表。"""
    recs = []
    for i, (fin, sp, st, sd, dist, place, date, cls, baba) in enumerate(rows):
        recs.append({"馬ID": f"20200{i:05d}", "馬名": f"ウマ{i}", "日付": date, RID: f"{date}05010101{i % 18 + 1:02d}", "場所": place,
                     "芝・ダ": sd, "距離": str(dist), "馬場": "", "着順": str(fin), "SP": sp, "ST": st, "クラス": cls, "馬場4": baba})
    return pd.DataFrame(recs, columns=PP.HISTORY_COLUMNS)


def test_samples_are_third_place_finishers_of_the_13_classes():
    ok = ("3", "70", "55", "芝", 1600, "東京", "20260105", "古馬1勝", "良")
    rows = [ok,
            ("2", "70", "55", "芝", 1600, "東京", "20260105", "古馬1勝", "良"),       # 3着でない
            ("3", "0", "", "芝", 1600, "東京", "20260105", "古馬1勝", "良"),          # 指数 0・値なし
            ("3", "70", "55", "芝", 1600, "東京", "20260105", "新馬", "良"),          # 新馬
            ("3", "70", "55", "芝", 1600, "東京", "20260105", "古馬G1", "良"),        # G1
            ("3", "70", "55", "芝", 1600, "東京", "20260105", "", "良"),              # クラス不明
            ("3", "70", "55", "芝", 1600, "東京", "20260105", "古馬1勝", ""),         # 馬場不明
            ("3", "70", "55", "芝", 1600, "大井", "20260105", "古馬1勝", "良"),       # JRA の10場でない
            ("3", "70", "55", "芝", "", "東京", "20260105", "古馬1勝", "良"),         # 距離不明
            ("3", "inf", "nan", "芝", 1600, "東京", "20260105", "古馬1勝", "良"),     # 数として使えない値
            ("3", "70", "55", "芝", 1600, "東京", "", "古馬1勝", "良"),               # 年が分からない
            ("3", "-5", "0.5", "ダ", 1200, "小倉", "20250105", "2勝", "不")]         # 負の値・小数も値
    f = history_frame(rows)
    assert B.samples_from_history(f, "SP") == [("芝", "古馬1勝", "良", "主場", "芝マイル", 2026, 70.0),
                                               ("ダ", "2勝", "重不良", "ローカル", "ダ短距離", 2025, -5.0)]
    assert B.samples_from_history(f, "ST") == [("芝", "古馬1勝", "良", "主場", "芝マイル", 2026, 55.0),
                                               ("ダ", "2勝", "重不良", "ローカル", "ダ短距離", 2025, 0.5)]
    assert B.samples_from_history(f.drop(columns=["クラス"]), "SP") == []          # 前の版の履歴（列が無い）


def test_update_writes_the_masters_only_with_enough_samples(tmp_path):
    rng = random.Random(7)
    rows = []
    for i in range(400):
        sd = rng.choice(["芝", "ダ"])
        rows.append((rng.choice(["3", "3", "1", "5"]), str(rng.randint(30, 90)), str(rng.randint(30, 70)) if i % 7 else "", sd,
                     rng.choice([1200, 1600, 2000]), rng.choice(["東京", "小倉", "阪神"]), f"{rng.choice([2024, 2025, 2026])}0105",
                     rng.choice(["古馬1勝", "2勝", "3歳未勝利", "新馬"]), rng.choice(["良", "稍", "重"])))
    frame = history_frame(rows)
    out = tmp_path / "data"
    logs = []
    none = B.update_from_history(frame.iloc[0:0], out, min_samples=0)             # 標本が1つも無ければ、最低件数 0 でも書かない
    assert {k: v for k, v in none.items()} == {"SP": {"status": "skipped", "n": 0}, "ST": {"status": "skipped", "n": 0}}
    res = B.update_from_history(frame, out, log=logs.append)                      # 既定は 2000 件に満たなければ書かない
    assert {k: v["status"] for k, v in res.items()} == {"SP": "skipped", "ST": "skipped"} and not out.exists()
    assert sum("満たないので更新しません" in m for m in logs) == 2
    res = B.update_from_history(frame, out, decay=0.65, min_samples=50, log=logs.append)
    assert {k: v["status"] for k, v in res.items()} == {"SP": "updated", "ST": "updated"}
    for kind in B.KINDS:
        samples = B.samples_from_history(frame, kind)
        want, root = o_build(samples, 0.65)
        assert res[kind]["n"] == len(samples) and res[kind]["root"] == root and res[kind]["cells"] == len(want)
        data = (out / B.FILES[kind]).read_bytes()
        assert rows_by_key([{k: (v if k in ("level", "key", "reliability", "parent") else float(v)) for k, v in r.items()}
                            for r in csv.DictReader(io.StringIO(data.decode("utf-8")))]) == \
            {k: {c: (v if c in ("level", "reliability", "parent") else float(v)) for c, v in r.items()} for k, r in want.items()}
        assert B.load(kind, out).cells == {k: r["index_mean"] for k, r in want.items()}
    assert not list(out.glob("*.part"))
    # 最低件数はちょうどなら更新する（1件でも足りなければしない）
    n_sp = len(B.samples_from_history(frame, "SP"))
    edge = tmp_path / "edge"
    assert B.update_from_history(frame, edge, min_samples=n_sp + 1, kinds=["SP"])["SP"]["status"] == "skipped" and not edge.exists()
    assert B.update_from_history(frame, edge, min_samples=n_sp, kinds=["SP"])["SP"]["status"] == "updated"
    # 書き込みは一時ファイルからの置き換え: 置き換えに失敗しても、前のファイルがそのまま読める
    held = (out / B.FILES["SP"]).read_bytes()
    real = B.os.replace
    def failing(src, dst):
        raise OSError("disk full (test)")
    B.os.replace = failing
    try:
        with pytest.raises(OSError):
            B.update_from_history(frame, out, decay=1.0, min_samples=50, kinds=["SP"])
    finally:
        B.os.replace = real
    assert (out / B.FILES["SP"]).read_bytes() == held and B.load("SP", out) is not None
    for part in out.glob("*.part"):
        part.unlink()
    before = {k: (out / B.FILES[k]).stat().st_mtime_ns for k in B.KINDS}
    res = B.update_from_history(frame, out, decay=0.65, min_samples=50)           # 同じ中身なら書き直さない
    assert {k: v["status"] for k, v in res.items()} == {"SP": "same", "ST": "same"}
    assert {k: (out / B.FILES[k]).stat().st_mtime_ns for k in B.KINDS} == before
    res = B.update_from_history(frame, out, decay=1.0, min_samples=50, kinds=["ST"])
    assert list(res) == ["ST"] and res["ST"]["status"] == "updated"
    assert B.load("ST", out).cells == {k: r["index_mean"] for k, r in o_build(B.samples_from_history(frame, "ST"), 1.0)[0].items()}
    (out / B.FILES["SP"]).write_text("こわれた", encoding="utf-8")
    logs.clear()
    assert B.load("SP", out, logs.append) is None and any("[WARN]" in m for m in logs) and list(B.load_tables(out)) == ["ST"]


# ---------------------------------------------------------------------------
# 条件（基準比・3走順）
# ---------------------------------------------------------------------------
def test_prev_and_now_labels_at_the_boundaries():
    p = B.prev_label
    assert [p("SP", d) for d in (10, 3, 2.9, 2, 0, -0.1, -1, -5, -5.9, -6, -20)] == \
        ["前走SP基準突破"] * 2 + ["前走SP基準クリア"] * 3 + ["前走SP基準未達"] * 4 + ["前走SP基準大幅未達"] * 2
    assert [p("ST", d) for d in (5, 2, 1.5, 1, 0, -0.5, -1, -3, -3.5, -4, -9)] == \
        ["前走ST基準突破"] * 2 + ["前走ST基準クリア"] * 3 + ["前走ST基準未達"] * 4 + ["前走ST基準大幅未達"] * 2
    n = B.now_label
    assert n("SP", 60, 55, 70, 80, 60) == "SP平均基準超" and n("SP", 55, 60, 70, 80, 60) == "SP平均基準超"      # どちらかの平均
    assert n("SP", 59.9, 59, 60, 80, 60) == "SP3走最高基準超" and n("SP", 50, 50, 59, 60, 60) == "SP5走最高基準超"
    assert n("SP", 50, 50, 59, 59.9, 60) == "SP今走基準未達" and n("ST", 50, 50, 59, 59.9, 60) == "ST今走基準未達"
    assert n("ST", 60, None, None, None, 60) == "ST平均基準超" and n("ST", None, None, None, 61, 60) == "ST5走最高基準超"
    assert n("SP", None, None, None, None, 60) == "" and n("SP", 60, 60, 60, 60, None) == ""                 # 値なし・基準値なし
    rng = random.Random(3)
    for _ in range(3000):
        kind = rng.choice(B.KINDS)
        d = rng.choice([rng.randint(-12, 12), round(rng.uniform(-8, 8), 1)])
        assert p(kind, d) == o_prev_label(kind, d)
        vals = [rng.choice([None, rng.randint(40, 80)]) for _ in range(4)]
        b = rng.choice([None, 60])
        assert n(kind, *vals, b) == o_now_label(kind, *vals, b)
    assert C.BASELINE_CONDITIONS == B.CONDITION_NAMES and set(C.BASELINE_CONDITIONS) <= set(C.CONDITION_COLUMNS)
    assert len(C.CONDITION_COLUMNS) == 36 and C.CONDITION_COLUMNS[-6:] == C.BASELINE_CONDITIONS


def test_conditions_from_history_values_and_ranks():
    table = B.Baseline("SP", {"芝 × 古馬1勝 × 良": 60, "ダ × 古馬1勝 × 良稍": 50, "ダ × 古馬1勝 × 重不良": 40, "芝 × 古馬重賞 × 良以外": 80})
    st = B.Baseline("ST", {"芝 × 古馬1勝 × 良": 45})
    hist = PP.History(pd.DataFrame([
        {"馬ID": "2020100001", "馬名": "アルファ", "日付": "20260501", RID: "202605010501010103", "場所": "東京", "芝・ダ": "芝", "距離": "1600",
         "馬場": "良", "着順": "5", "SP": "63", "ST": "46.5", "クラス": "古馬1勝", "馬場4": "良"},
        {"馬ID": "2020100001", "馬名": "アルファ", "日付": "20260301", RID: "202603010601010105", "場所": "中山", "芝・ダ": "ダ", "距離": "1200",
         "馬場": "稍・重", "着順": "1", "SP": "33", "ST": "", "クラス": "古馬1勝", "馬場4": "重"},
        {"馬ID": "", "馬名": "ベータ", "日付": "20260501", RID: "202605010501010107", "場所": "東京", "芝・ダ": "芝", "距離": "2400",
         "馬場": "不", "着順": "9", "SP": "75", "ST": "44", "クラス": "古馬G1", "馬場4": "不"},
        {"馬ID": "2020100003", "馬名": "ガンマ", "日付": "20260501", RID: "202605010501010109", "場所": "東京", "芝・ダ": "芝", "距離": "1600",
         "馬場": "良", "着順": "2", "SP": "0", "ST": "45", "クラス": "新馬", "馬場4": "良"},
    ], columns=PP.HISTORY_COLUMNS))
    raw = pd.DataFrame({
        "血統登録番号": ["2020100001", "2020100001", "", "2020100003", "2020100009"],
        "馬名": ["アルファ", "アルファ", "(外)ベータ", "ガンマ", "デルタ"], "場所": ["東京", "中山", "東京", "東京", "東京"],
        "芝ダ": ["芝", "ダ", "芝", "芝", "芝"], "距離": ["1600", "1200", "1600", "1600", "1600"],
        "クラス": ["1勝", "1勝", "1勝", "新馬", "1勝"], "年限定": ["3上", "3上", "3上", "2歳", "3上"],
        "馬場状態": ["良", "重", "", "良", "良"]})
    parts = B.today_parts(raw)
    assert parts["クラス"].tolist() == ["古馬1勝", "古馬1勝", "古馬1勝", "新馬", "古馬1勝"] and parts["馬名"].tolist()[2] == "ベータ"
    assert parts["馬場"].tolist() == ["良", "重", "", "良", "良"] and parts["距離"].tolist()[1] == 1200
    prev = pd.Series(["2026050105010101", "2026030106010101", "2026050105010101", "2026050105010101", "2026050105010101"])
    values = {"過去SP_3走平均": pd.Series(["60", "39.9", "0", "70", ""]), "過去SP_5走平均": pd.Series(["55", "39", "0", "70", ""]),
              "過去SP_3走最高": pd.Series(["65", "40", "0", "70", ""]), "過去SP_5走最高": pd.Series(["65", "45", "0", "70", ""]),
              "過去ST_3走平均": pd.Series(["44", "", "", "", ""]), "過去ST_5走平均": pd.Series(["44.9", "", "", "", ""]),
              "過去ST_3走最高": pd.Series(["44", "", "", "", ""]), "過去ST_5走最高": pd.Series(["46", "", "", "", ""])}
    ranks = {"SP": pd.Series(["1", "2.0", "", "0", "x"]), "ST": pd.Series(["3", "", "", "", ""])}
    got = B.conditions(parts, prev, hist, {"SP": table, "ST": st}, values, ranks)
    assert list(got.columns) == list(C.BASELINE_CONDITIONS) and got.index.equals(raw.index)
    # 前走: 馬ごとに 前走RID16 の走を履歴から。63 − 60 = +3 → 突破／33 − 40（ダ・重 = 重不良）= −7 → 大幅未達／
    # ベータ（番号なし・馬名で引く）は G1 → 重賞・不良 = 良以外: 75 − 80 = −5 → 未達／ガンマは 新馬（基準値なし）・SP 0／デルタは履歴に無い
    assert got["前走SP基準比"].tolist() == ["前走SP基準突破", "前走SP基準大幅未達", "前走SP基準未達", "", ""]
    assert got["前走ST基準比"].tolist() == ["前走ST基準クリア", "", "", "", ""]             # 46.5 − 45 = +1.5。ST の表に無い条件は空
    # 今走: 3走平均 60 ≥ 60／ダ・重（重不良 40）: 平均 39.9・39 は未満、3走最高 40 ≥ 40／値 0 = 値なし／新馬は基準値なし／値なし
    assert got["過去SP今走基準比"].tolist() == ["SP平均基準超", "SP3走最高基準超", "", "", ""]
    assert got["過去ST今走基準比"].tolist() == ["ST5走最高基準超", "", "", "", ""]
    assert got["過去SP3走順"].tolist() == ["1", "2", "", "", ""] and got["過去ST3走順"].tolist() == ["3", "", "", "", ""]
    # 今走の馬場の想定（出馬表側）: ダを 良 と想定すると 良稍（50）で引く → 平均も最高も未満
    assumed = B.conditions(parts, prev, hist, {"SP": table}, values, ranks, baba_turf="auto", baba_dirt="良")
    assert assumed["過去SP今走基準比"].tolist() == ["SP平均基準超", "SP今走基準未達", "", "", ""]
    assert assumed["前走SP基準比"].tolist() == got["前走SP基準比"].tolist()               # 前走は実際の馬場（履歴）で引く
    assert assumed["過去ST今走基準比"].tolist() == [""] * 5                              # ST の表を渡していない
    wet = B.conditions(parts, prev, hist, {"SP": table}, values, ranks, baba_turf="不")
    assert wet["過去SP今走基準比"].tolist() == ["", "SP3走最高基準超", "", "", ""]        # 芝・良以外 の 古馬1勝 は表に無い
    # 馬場の列が空の馬（ベータ）は 良 として引く
    v2 = {k: pd.Series(["", "", "61", "", ""]) for k in B.NOW_INDICATORS["SP"]}
    assert B.conditions(parts, prev, hist, {"SP": table}, v2, {})["過去SP今走基準比"].tolist() == ["", "", "SP平均基準超", "", ""]
    # 履歴・基準値・値が無ければ、その列は空
    none = B.conditions(parts, prev, None, {"SP": table}, values, ranks)
    assert none["前走SP基準比"].tolist() == [""] * 5 and none["過去SP今走基準比"].tolist() == got["過去SP今走基準比"].tolist()
    empty = B.conditions(parts, None, hist, {}, None, None)
    assert (empty == "").all().all() and list(empty.columns) == list(C.BASELINE_CONDITIONS)
    only_rank = B.conditions(parts, prev, hist, {}, values, ranks)                       # 順位は基準値が無くても付く
    assert only_rank["過去SP3走順"].tolist() == ["1", "2", "", "", ""] and (only_rank["過去SP今走基準比"] == "").all()
    with pytest.raises(ValueError):
        B.conditions(parts, prev, hist, {"SP": table}, values, ranks, baba_turf="晴")
    # derive: perf で渡した列がそのまま入る（渡さなければ空）
    std = pd.DataFrame({c: [""] * 5 for c in C.STD_COLUMNS})
    assert C.derive(std, perf=got)[list(C.BASELINE_CONDITIONS)].equals(got.astype(object))
    assert (C.derive(std)[list(C.BASELINE_CONDITIONS)] == "").all().all()
    assert hist.run_at("2020100001", "アルファ", "2026030106010101")[PP._F_BABA4] == "重" and hist.run_at("", "ベータ", "") is None


def test_previous_run_is_this_horse_at_that_race_and_cells_use_each_races_own_place():
    # 主場とローカルで基準値の違う表（L1）: 芝・2勝・良・芝マイル は 主場 50／ローカル 70（L2・L3 は 60）
    table = B.Baseline("SP", {"芝 × 2勝 × 良": 60, "芝 × 2勝 × 良 × 芝マイル": 60, "芝 × 2勝 × 良 × 主場 × 芝マイル": 50,
                              "芝 × 2勝 × 良 × ローカル × 芝マイル": 70})
    run = lambda hid, name, date, rid, place, sp, baba="良": {
        "馬ID": hid, "馬名": name, "日付": date, RID: rid, "場所": place, "芝・ダ": "芝", "距離": "1600", "馬場": baba, "着順": "4",
        "SP": sp, "ST": "", "クラス": "2勝", "馬場4": baba}
    hist = PP.History(pd.DataFrame([
        run("2020200001", "ドウメイ", "20260501", "202605011001010103", "小倉", "65"),     # 同じレースに同じ馬名の2頭（番号が違う）
        run("2020200002", "ドウメイ", "20260501", "202605011001010107", "小倉", "75"),
        run("2020200003", "フルイ", "20260101", "202601010501010102", "東京", "80"),       # 履歴にあるのは古い走だけ
        run("2020200004", "フメイ", "20260501", "202605010501010105", "東京", "49", ""),   # 前走の馬場が分からない → 良 として引く
    ], columns=PP.HISTORY_COLUMNS))
    raw = pd.DataFrame({"血統登録番号": ["2020200001", "2020200002", "2020200003", "2020200004"],
                        "馬名": ["ドウメイ", "ドウメイ", "フルイ", "フメイ"], "場所": ["東京", "小倉", "東京", "東京"],
                        "芝ダ": ["芝"] * 4, "距離": ["1600"] * 4, "クラス": ["2勝"] * 4, "年限定": ["3上"] * 4, "馬場状態": ["良"] * 4})
    # フルイの前走は履歴に無いレース（地方・取消だった走など）。履歴の古い走で代用しない
    prev = pd.Series(["2026050110010101", "2026050110010101", "2026040144010101", "2026050105010101"])
    values = {"過去SP_3走平均": pd.Series(["60", "60", "-3", "49.5"]), "過去SP_5走平均": pd.Series(["55", "58", "-3", ""]),
              "過去SP_3走最高": pd.Series(["65", "65", "-1", ""]), "過去SP_5走最高": pd.Series(["65", "69.9", "-1", ""])}
    got = B.conditions(B.today_parts(raw), prev, hist, {"SP": table}, values, {})
    # 前走は小倉（ローカル 70）: 番号 …01 は 65 − 70 = −5 → 未達、…02 は 75 − 70 = +5 → 突破（今走の場所・馬名だけでは決めない）
    assert got["前走SP基準比"].tolist() == ["前走SP基準未達", "前走SP基準突破", "", "前走SP基準未達"]      # フメイ: 49 − 50（主場・良）
    # 今走: 東京（主場 50）に 60 → 平均基準超／小倉（ローカル 70）に 60・最高 69.9 → 未達／負の値も値（未達）／49.5 < 50 → 未達
    assert got["過去SP今走基準比"].tolist() == ["SP平均基準超", "SP今走基準未達", "SP今走基準未達", "SP今走基準未達"]
    assert hist.run_at("2020200003", "フルイ", "2026040144010101") is None
    assert hist.run_at("2020200003", "フルイ", "2026010105010101")[PP._F_SP] == 80
    assert hist.run_at("2020200002", "ドウメイ", "2026050110010101")[PP._F_SP] == 75


def test_results_of_a_previous_version_history_are_carried_over(tmp_path):
    """版3の履歴（結果指数つき）が残っていて結果指数のファイルが無い場所（csv_files と master_csv だけ持ってきた）では、
    履歴を捨てずに、結果指数の値を引き継いで版4にする。"""
    base, _marks, _ent, _names = synth2.site_data(days=20)
    csv_dir, res, master = tmp_path / "csv_files", tmp_path / "result_indices", tmp_path / "master_csv"
    csv_dir.mkdir()
    res.mkdir()
    base.to_csv(csv_dir / "全場_タイムM.csv", index=False, encoding="cp932")
    sp, _st = result_values(base)
    (res / "SP_all.csv").write_text("".join(f"{r},{v}\n" for r, v in sp.items()), encoding="utf-8")
    h4 = PP.get_history(csv_dir, res, master)
    assert h4.has_results
    frame, _sig = PP.load_history_cache(master)
    # 前の版の形にする（クラス・馬場4 の列が無い・版3）
    import gzip
    old_cols = [c for c in PP.HISTORY_COLUMNS if c not in ("クラス", "馬場4")]
    (master / PP.HISTORY_FILE).write_bytes(gzip.compress(frame[old_cols].to_csv(index=False).encode("utf-8")))
    meta = json.loads((master / PP.HISTORY_META).read_text(encoding="utf-8"))
    (master / PP.HISTORY_META).write_text(json.dumps({**meta, "version": 3}), encoding="utf-8")
    assert PP.load_history_cache(master) is None
    (res / "SP_all.csv").unlink()
    logs = []
    h = PP.get_history(csv_dir, res, master, logs.append)
    assert h is not None and h.has_results and any("前の版の履歴（結果指数つき）" in m for m in logs)
    again, _sig = PP.load_history_cache(master)                       # 版4で書き直されている（結果指数の値・クラス・馬場4 つき）
    assert again.equals(frame) and json.loads((master / PP.HISTORY_META).read_text(encoding="utf-8"))["version"] == 4
    logs.clear()
    assert PP.get_history(csv_dir, res, master, logs.append).has_results and not any("前の版" in m for m in logs)
    # replace（rebuild=True）は今ある元から作り直す（結果指数なし）。結果指数の無い版3の履歴からは引き継がない
    assert not PP.get_history(csv_dir, res, master, rebuild=True, save=False).has_results
    (master / PP.HISTORY_FILE).write_bytes(gzip.compress(frame[old_cols].assign(SP="", ST="").to_csv(index=False).encode("utf-8")))
    (master / PP.HISTORY_META).write_text(json.dumps({**meta, "version": 3}), encoding="utf-8")
    assert not PP.get_history(csv_dir, res, master).has_results
    # 基本CSVも無い場所（master_csv だけ持ってきた）: 前の版の履歴をそのまま使う（8指数・近5走には足りる。前走の基準比は付かない）
    (master / PP.HISTORY_FILE).write_bytes(gzip.compress(frame[old_cols].to_csv(index=False).encode("utf-8")))
    (master / PP.HISTORY_META).write_text(json.dumps({**meta, "version": 3}), encoding="utf-8")
    logs.clear()
    h = PP.get_history(tmp_path / "no_csv", res, master, logs.append)
    assert h is not None and h.has_results and h.rows == len(frame) and any("前の版で作った" in m for m in logs)
    row = frame[frame["SP"] != ""].iloc[0]
    run = h.run_at(row["馬ID"], row["馬名"], row[RID][:16])
    assert run[PP._F_SP] == float(row["SP"]) and run[PP._F_CLASS] == "" and run[PP._F_BABA4] == ""
    assert json.loads((master / PP.HISTORY_META).read_text(encoding="utf-8"))["version"] == 3      # 書き換えない
    (master / PP.HISTORY_META).write_text(json.dumps({**meta, "version": 2}), encoding="utf-8")    # もっと前の版は使えない
    logs.clear()
    assert PP.get_history(tmp_path / "no_csv", res, master, logs.append) is None and any("[WARN]" in m for m in logs)


def test_rank_bands_of_past_sp_st_make_the_same_keys_on_both_sides():
    assert C.BANDED_CONDITIONS["過去SP3走順"] == "sp3" and C.BANDED_CONDITIONS["過去ST3走順"] == "st3"
    from shisu.hensachi import band_labels
    assert band_labels(1, "s1", "sp3") == ["SP3走1位"] and band_labels(3, "s1", "st3") == ["ST3走2~3位"]
    assert band_labels(2, "s2", "st3") == ["ST3走1~3位", "ST3走1~5位"] and band_labels(8, "s2", "sp3") == ["SP3走6位~"]
    assert band_labels(10, "s1", "sp3") == ["SP3走10位~"] and band_labels("", "s1", "sp3") == []
    f = F.Family("X_stb_st3", ("過去ST今走基準比", "過去ST3走順"), "RBAND")
    h = F.Family("X_spb_sp3_hen", ("過去SP今走基準比", "過去SP3走順"), "HBAND")
    nidx = F.Family("X_stb_st3_none", ("過去ST今走基準比", "過去ST3走順"), "NONE")
    rk = F.Family("X_sp3_rank", ("過去SP3走順",), "RANK")
    rk_st = F.Family("X_st3_rank", ("過去ST3走順",), "RANK")             # SP・ST のもう片方でも同じ
    nidx_sp = F.Family("X_sp3_none", ("過去SP3走順",), "NONE")
    for fam in (f, h, nidx, rk, rk_st, nidx_sp):
        F.validate(fam)
        assert fam.uses_schemes and fam.schemes == ("s1", "s2")          # 帯にする条件なので、どの末尾でも s1・s2
    df = pd.DataFrame({"過去ST今走基準比": ["ST平均基準超", "ST今走基準未達", "ST平均基準超", ""], "過去ST3走順": ["2", "7", "", "1"],
                       "過去SP今走基準比": ["SP3走最高基準超"] * 4, "過去SP3走順": ["1", "12", "5", ""],
                       "順位": ["4", "1", "2", "3"], "偏差値帯": ["61~65", "71以上", "51~60", "40以下"]})
    assert F.build_keys(df, f, "s1").tolist() == ["ST平均基準超×ST3走2~3位×4~5位", "ST今走基準未達×ST3走6~9位×1位"]
    assert sorted(F.build_keys(df, f, "s2")[lambda s: s.index == 0]) == ["ST平均基準超×ST3走1~3位×1~5位", "ST平均基準超×ST3走1~5位×1~5位"]
    assert F.build_keys(df, h, "s1").tolist() == ["SP3走最高基準超×SP3走1位×61~65", "SP3走最高基準超×SP3走10位~×71以上",
                                                  "SP3走最高基準超×SP3走4~5位×51~60"]
    assert F.build_keys(df, nidx, "s2")[lambda s: s.index == 1].tolist() == ["ST今走基準未達×ST3走6位~"]
    assert F.build_keys(df, rk, "s1").tolist() == ["SP3走1位×4", "SP3走10位~×1", "SP3走4~5位×2"]      # 末尾が指数順位でも帯はスキームどおり
    assert sorted(F.build_keys(df, rk, "s2")[lambda s: s.index == 0]) == ["SP3走1~3位×4", "SP3走1~5位×4"]
    from shisu import matching
    from shisu.indicators import CONDITION_ONLY
    for fam in (f, h, nidx, rk, rk_st, nidx_sp):
        for sc in ("s1", "s2"):
            past = F.build_keys(df, fam, sc)
            for i, row in df.iterrows():
                assert sorted(F.entry_keys(row.to_dict(), fam, sc)) == sorted(past[past.index == i].tolist()), (fam.name, sc, i)
    # 照合の窓口（matching.entry_fields）を通しても同じ: 条件のみ（順位・偏差値帯を見ない）でも 3走順の部品は残る
    for i, row in df.iterrows():
        entry = {**row.to_dict(), "オメガ指数_順位": row["順位"], "オメガ指数_偏差値帯": row["偏差値帯"]}
        for fam, base in ((nidx, CONDITION_ONLY), (nidx_sp, CONDITION_ONLY), (f, "オメガ指数"), (rk, "オメガ指数"), (rk_st, "オメガ指数")):
            got = {k for sc in ("s1", "s2") for k in F.entry_keys(matching.entry_fields(entry, base), fam, sc)}
            want = {k for sc in ("s1", "s2") for k in F.build_keys(df, fam, sc)[lambda s: s.index == i].tolist()}
            assert got == want, (fam.name, i)
    # その指数自身の順位を部品にした family は、その指数の master では作らない
    assert F.is_self_redundant(h, "過去SP_3走平均") and not F.is_self_redundant(h, "過去ST_3走平均")
    assert F.is_self_redundant(f, "過去ST_3走平均") and not F.is_self_redundant(f, "過去ST_5走平均") and not F.is_self_redundant(f, "オメガ指数")
    assert not F.is_self_redundant(nidx, "過去ST_3走平均")                # 条件のみは基準の master で1回だけ（対象外）
    assert F.is_self_redundant(rk, "過去SP_3走平均") and not F.is_self_redundant(rk, "過去SP_3走最高")     # 末尾が指数順位でも
    assert F.is_self_redundant(rk_st, "過去ST_3走平均") and not F.is_self_redundant(rk_st, "過去SP_3走平均")
    assert not F.is_self_redundant(nidx_sp, "過去SP_3走平均")
    assert F.build_keys(df, nidx_sp, "s1").tolist() == ["SP3走1位", "SP3走10位~", "SP3走4~5位"]
    assert not F.is_self_redundant(F.Family("X_spb", ("過去SP今走基準比",), "RBAND"), "過去SP_3走平均")


def test_screening_candidates_include_the_baseline_conditions():
    import screen_families as SF
    assert len(SF.POOL) == 40 and set(C.BASELINE_CONDITIONS) <= set(SF.POOL) and set(SF.POOL) <= set(SF.PART_CODES)
    assert len(set(SF.PART_CODES.values())) == len(SF.PART_CODES)
    trios = {frozenset(t) for t in SF.causal_trios()}
    for kind in ("SP", "ST"):
        for x in (f"前走{kind}基準比", "前走着順", "前走人気", "近5走同条件"):
            assert frozenset((f"過去{kind}今走基準比", f"過去{kind}3走順", x)) in trios
    names = {f.name: f for f in SF.make_candidates("default")}
    pair = names["X_stb_st3"]                                           # 過去ST今走基準比 × 過去ST3走順 × 指数順位帯
    assert set(pair.parts) == {"過去ST今走基準比", "過去ST3走順"} and pair.tail == "RBAND"
    assert names["X_spb_sp3_hen"].tail == "HBAND" and "X_pfin_stb_st3" in names and "X_pspb_spb_sp3" in names
    assert "X_plc_sd_stb" in names and "X_crs_sp3" in names             # 骨格 × 単一条件にも入る
    assert not SF.redundant(("過去SP今走基準比", "過去SP3走順"))
    for space, tails, n_t, n_f, n_v in (("default", ("RBAND", "HBAND"), 1127, 2277, 3709), ("all", ("RBAND", "HBAND"), 4727, 9299, 15195),
                                        ("default", ("RBAND", "HBAND", "NONE"), 1127, 3404, 5131)):
        cands = SF.make_candidates(space, tails)
        assert (len(SF.templates(space)), len(cands), sum(len(f.schemes) for f in cands)) == (n_t, n_f, n_v)


# ---------------------------------------------------------------------------
# 全段（replace → aggregate → calc → build_db）
# ---------------------------------------------------------------------------
FAMILIES = [
    {"name": "normal", "parts": ["COURSE", "重量種別", "クラス名"], "tail": "RANK", "layer": "A"},
    {"name": "X_stb_st3", "parts": ["過去ST今走基準比", "過去ST3走順"], "tail": "RBAND"},
    {"name": "X_spb_sp3_hen", "parts": ["過去SP今走基準比", "過去SP3走順"], "tail": "HBAND"},
    {"name": "X_pspb", "parts": ["前走SP基準比"], "tail": "RBAND"},
    {"name": "X_sp3", "parts": ["過去SP3走順"], "tail": "RBAND"},
    {"name": "N_stb_pstb", "parts": ["過去ST今走基準比", "前走ST基準比"], "tail": "NONE"},
    {"name": "N_stb_st3", "parts": ["過去ST今走基準比", "過去ST3走順"], "tail": "NONE"},
    {"name": "X_sp3_rank", "parts": ["過去SP3走順"], "tail": "RANK"},
]
AGG_ARGS = ["--wedge-min-year-count", "2", "--no-compact"]
MIN_ARGS = ["--baseline-min-samples", "30"]


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def result_values(base):
    """結果指数（合成）: レースID(新) → 値。SP は一部を値なし・0 に、ST は別の欠け方に。"""
    sp, st = {}, {}
    for i, r in enumerate(base[RID]):
        if i % 17 != 0:
            sp[r] = 0 if i % 23 == 0 else 38 + (i * 7) % 45
        if i % 13 != 0:
            st[r] = 30 + (i * 5) % 33
    return sp, st


@pytest.fixture(scope="module")
def site(tmp_path_factory, monkeypatch_module):
    root = tmp_path_factory.mktemp("round3")
    data = root / "baseline"
    data.mkdir()
    for kind in B.KINDS:                                       # 受け取ったマスタの写しから始める（replace が作り直す）
        shutil.copy(REPO_DATA / B.FILES[kind], data / B.FILES[kind])
    monkeypatch_module.setenv("SHISU_BASE_DIR", str(root))
    monkeypatch_module.setenv("SHISU_BASELINE_DIR", str(data))
    for k in ("RESULT_INDICES_DIR", "RACE_MARKS_DIR", "SHISU_EXT_DIR", "DB_PATH", "SHISU_FAMILIES_FILE", "NOTES_PATH", "PAST_PERF_SETTINGS"):
        monkeypatch_module.delenv(k, raising=False)
    base, _marks, ent, _names = synth2.write_site(root, days=80, marks_as="none")
    rid16 = base[RID].str[:16]
    kinds = {r: RACE_KINDS[i % len(RACE_KINDS)][0] for i, r in enumerate(sorted(set(rid16)))}
    base["クラス名"] = [kinds[r][0] for r in rid16]
    base["年齢限定"] = [kinds[r][1] for r in rid16]
    base.to_csv(root / "csv_files" / "全場_タイムM.csv", index=False, encoding="cp932")
    rng = random.Random(4)
    base.assign(指数=[str(rng.randint(40, 110)) for _ in range(len(base))]).drop(columns=["指数順位"]).to_csv(
        root / "csv_files" / "全場_対戦M.csv", index=False, encoding="cp932")
    sp, st = result_values(base)
    (root / "result_indices" / "sub").mkdir(parents=True)
    (root / "result_indices" / "SP_all.csv").write_text("".join(f"{r},{v}\n" for r, v in sp.items()), encoding="utf-8")
    (root / "result_indices" / "sub" / "st_se_all.csv").write_text("".join(f"{r},{v}\n" for r, v in st.items()), encoding="utf-8")
    # ext_indices の指数（形式A）: 「基準比 × ST3走順 × ほかの指数の順位帯」の道を通す
    (root / "ext_indices" / "OMEGA").mkdir(parents=True)
    rng = random.Random(9)
    (root / "ext_indices" / "OMEGA" / "all_omega.csv").write_text(
        "".join(f"{r},{rng.randint(30, 99)}\n" for r in [*base[RID], *ent[RID]]), encoding="utf-8")
    (root / "selected_families.json").write_text(json.dumps({"families": FAMILIES}, ensure_ascii=False), encoding="utf-8")
    import aggregate_stats_ver2, build_db, calc_entry_hensachi, replace_csv_ver3
    assert replace_csv_ver3.main(["--clean", *MIN_ARGS]) == 0
    assert aggregate_stats_ver2.main(["--clean", *AGG_ARGS]) == 0
    assert calc_entry_hensachi.main(["--merge-only", "--clean"]) == 0
    assert build_db.main(["--fresh"]) == 0
    yield root, base, ent, sp, st
    F.reset()


def read_master(root, indicator="タイムM"):
    return pd.read_csv(root / "master_csv" / f"master_全場_{indicator}.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)


def base_samples(base, results, look=False):
    """基本CSVの行 → (芝ダ, クラス, 馬場区分, 場所区分, 距離帯, 年, 値)（3着・値が 0 でない・13区分のクラス）。"""
    out = []
    for _, r in base.iterrows():
        v = results.get(r[RID])
        cls = O_CLASS[(r["クラス名"], r["年齢限定"])][0]
        if r["着順"] != "3" or not v or cls is None:
            continue
        sd, dist = r["距離"][0], int(r["距離"][1:])
        out.append((sd, cls, O_TRACK[sd][r["馬場状態"]], O_PG[r["場所"]], O_BAND[(sd, dist)], int(r[RID][:4]), float(v)))
    return out


def o_base_now(cells, r, baba=None):
    sd, dist = r["距離"][0], int(r["距離"][1:])
    cls = O_CLASS[(r["クラス名"], r["年齢限定"])][1]
    return o_lookup(cells, sd, cls, O_TRACK[sd][r["馬場状態"] if baba is None else baba], O_PG[r["場所"]], O_BAND[(sd, dist)])


def o_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None


def test_replace_rebuilds_the_masters_from_third_place_finishers(site):
    root, base, _ent, sp, st = site
    hist = pd.read_csv(root / "master_csv" / PP.HISTORY_FILE, encoding="utf-8", dtype=str, keep_default_na=False)
    assert list(hist.columns) == PP.HISTORY_COLUMNS and PP.HISTORY_VERSION == 4
    assert json.loads((root / "master_csv" / PP.HISTORY_META).read_text(encoding="utf-8"))["version"] == 4
    by = base.set_index(RID)
    src = by.loc[hist[RID]]
    assert hist["クラス"].tolist() == [B.race_class(c, a) for c, a in zip(src["クラス名"], src["年齢限定"])]
    assert set(hist["クラス"]) == {"3歳未勝利", "古馬1勝", "2勝", "古馬OP", "古馬重賞", "古馬G1", "新馬", "2歳未勝利", "3歳1勝", "3歳OP", "3勝"}
    assert hist["馬場4"].tolist() == src["馬場状態"].tolist() and set(hist["馬場4"]) == {"良", "稍", "重"}
    for kind, results in (("SP", sp), ("ST", st)):
        samples = base_samples(base, results)
        assert len(samples) >= 100
        want, _root = o_build(samples, 0.65)                             # 既定の decay = 0.65
        path = root / "baseline" / B.FILES[kind]
        got = {r["key"]: r for r in csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8")))}
        assert set(got) == set(want) and path.read_bytes() != (REPO_DATA / B.FILES[kind]).read_bytes()
        for key, w in want.items():
            g = got[key]
            assert (g["level"], g["parent"], g["reliability"]) == (w["level"], w["parent"], w["reliability"]), key
            for c in ("n", "index_mean", "index_raw", "index_mean_全体", "n_kept", "n_cut", "index_std", "p25", "median", "p75"):
                assert float(g[c]) == float(w[c]), (kind, key, c)
        # master を作ったときの基準値の写し（出馬表側が読む）
        assert (root / "master_csv" / B.MASTER_FILES[kind]).read_bytes() == path.read_bytes()
        assert not any(k.split(" × ")[1] in ("新馬", "古馬G1") for k in got)
    assert json.loads((root / "master_csv" / B.MASTER_META).read_text(encoding="utf-8")) == {"version": 1, "past_perf": True}


@pytest.mark.parametrize("indicator", ["タイムM", "対戦M", "オメガ指数", "過去ST_5走最高"])     # file・ext・perf の3つの道
def test_masters_carry_the_six_columns(site, indicator):
    root, base, _ent, sp, st = site
    m = read_master(root, indicator)
    by = base.set_index(RID)
    assert len(m) == len(base) and m[RID].tolist() == base[RID].tolist()
    filled = {}
    for kind, results in (("SP", sp), ("ST", st)):
        cells = o_cells(root / "baseline" / B.FILES[kind])
        perf = {n: read_master(root, n).set_index(RID) for n in B.NOW_INDICATORS[kind]}
        want_prev, want_now, want_rank = [], [], []
        for _, r in base.iterrows():
            # 前走: 前走レースID(新) の走（同じ馬）の結果指数 − その走の条件の基準値
            p = r["前走レースID(新)"]
            v = results.get(p) if p else None
            b_prev = o_base_now(cells, by.loc[p]) if p else None
            want_prev.append(o_prev_label(kind, v - b_prev) if v and b_prev is not None else "")
            vals = [o_num(perf[n].loc[r[RID], "指数"]) for n in B.NOW_INDICATORS[kind]]
            want_now.append(o_now_label(kind, *vals, o_base_now(cells, r)))
            want_rank.append(perf[B.RANK_INDICATOR[kind]].loc[r[RID], "順位"])
        assert m[B.PREV_COLUMNS[kind]].tolist() == want_prev, (indicator, kind)
        assert m[B.NOW_COLUMNS[kind]].tolist() == want_now, (indicator, kind)
        assert m[B.RANK_COLUMNS[kind]].tolist() == want_rank, (indicator, kind)
        filled[kind] = (set(want_prev), set(want_now), set(want_rank))
    # 空の一致でないこと: どの区分も出ている。新馬（基準値なし）のレースは今走の基準比が空
    for kind in B.KINDS:
        prev, now, rank = filled[kind]
        assert prev == {"", *(f"前走{kind}基準{w}" for w in ("突破", "クリア", "未達", "大幅未達"))}
        assert now == {"", f"{kind}平均基準超", f"{kind}3走最高基準超", f"{kind}5走最高基準超", f"{kind}今走基準未達"}
        assert {"1", "2", "5", "12", ""} <= rank
    shinba = m[(base["クラス名"] == "新馬").to_numpy()]
    assert len(shinba) > 100 and (shinba["過去SP今走基準比"] == "").all() and (shinba["過去SP3走順"] != "").any()


def test_entries_side_gets_the_same_columns(site, capsys):
    root, base, ent, sp, st = site
    con = sqlite3.connect(root / "racing.db")
    e = pd.read_sql('SELECT * FROM entries_merged_hensachi ORDER BY "レースID(新)"', con).fillna("")
    con.close()
    ent = ent.sort_values(RID).reset_index(drop=True)
    assert e[RID].tolist() == ent[RID].tolist()
    ran = base[base["着順"] != "取消"]
    last = ran.groupby("血統登録番号").tail(1).set_index("血統登録番号")
    by = base.set_index(RID)
    for kind, results in (("SP", sp), ("ST", st)):
        cells = o_cells(root / "baseline" / B.FILES[kind])
        want_prev, want_now = [], []
        for i, r in ent.iterrows():
            prev = last.loc[r["血統登録番号"]] if r["血統登録番号"] in last.index else None
            v = results.get(prev[RID]) if prev is not None else None
            b_prev = o_base_now(cells, by.loc[prev[RID]]) if prev is not None else None
            want_prev.append(o_prev_label(kind, v - b_prev) if v and b_prev is not None else "")
            # 今走: 出馬表（1勝・3上。馬場状態の列は無い → 良 として引く）
            b_now = o_lookup(cells, r["芝ダ"], "古馬1勝", O_TRACK[r["芝ダ"]][""], O_PG[r["場所"]], O_BAND[(r["芝ダ"], int(r["距離"]))])
            want_now.append(o_now_label(kind, *[o_num(e.loc[i, n]) for n in B.NOW_INDICATORS[kind]], b_now))
        assert e[B.PREV_COLUMNS[kind]].tolist() == want_prev and len(set(want_prev)) >= 4
        assert e[B.NOW_COLUMNS[kind]].tolist() == want_now and len(set(want_now)) >= 3
        assert e[B.RANK_COLUMNS[kind]].tolist() == [str(int(float(v))) if str(v) not in ("", "nan") else ""
                                                    for v in e[f"{B.RANK_INDICATOR[kind]}_順位"]]
        assert (e[B.RANK_COLUMNS[kind]] != "").sum() >= 20


def test_tables_and_hits_use_the_new_parts_and_skip_self_synonyms(site):
    root, _base, _ent, _sp, _st = site
    con = sqlite3.connect(root / "racing.db")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute('SELECT family, indicator, rank_scheme, "結合データ" AS key FROM unified_all')]
    by = {}
    for r in rows:
        by.setdefault((r["family"], r["indicator"]), []).append(r)
    inds = {i for _f, i in by}
    assert {"タイムM", "対戦M", "オメガ指数", "過去SP_3走平均", "過去ST_3走平均", "条件のみ"} <= inds and len(inds) == 12
    key_re = re.compile(r"^ST(平均基準超|3走最高基準超|5走最高基準超|今走基準未達)×ST3走(\d+位|\d+~\d+位|\d+位~)×(\d+位|\d+~\d+位|\d+位~)$")
    for ind in ("タイムM", "オメガ指数", "過去SP_5走最高", "過去ST_5走平均"):
        got = by[("X_stb_st3", ind)]
        assert len(got) > 20 and all(key_re.match(r["key"]) for r in got) and {r["rank_scheme"] for r in got} == {"s1", "s2"}
    for ind in ("タイムM", "オメガ指数"):                                  # 依頼の例: ST平均基準超 × ST3走1~5位 × 1~5位（オメガ指数）
        assert any(r["key"] == "ST平均基準超×ST3走1~5位×1~5位" for r in by[("X_stb_st3", ind)])
    # その指数自身の順位を部品にした family は、その指数の master では作らない
    assert ("X_sp3", "過去SP_3走平均") not in by and ("X_spb_sp3_hen", "過去SP_3走平均") not in by
    assert ("X_stb_st3", "過去ST_3走平均") not in by
    assert ("X_sp3", "過去SP_5走平均") in by and ("X_sp3", "過去ST_3走平均") in by and ("X_spb_sp3_hen", "タイムM") in by
    assert ("X_pspb", "過去SP_3走平均") in by                             # 順位の部品が無い family は作る
    assert {i for f, i in by if f == "N_stb_pstb"} == {"条件のみ"}
    # 条件のみで帯の部品を持つ family・末尾が指数順位の family
    none_re = re.compile(r"^ST(平均基準超|3走最高基準超|5走最高基準超|今走基準未達)×ST3走(\d+位|\d+~\d+位|\d+位~)$")
    got = by[("N_stb_st3", "条件のみ")]
    assert {i for f, i in by if f == "N_stb_st3"} == {"条件のみ"} and len(got) > 10
    assert all(none_re.match(r["key"]) for r in got) and {r["rank_scheme"] for r in got} == {"s1", "s2"}
    assert ("X_sp3_rank", "過去SP_3走平均") not in by and ("X_sp3_rank", "過去SP_5走平均") in by
    rank_re = re.compile(r"^SP3走(\d+位|\d+~\d+位|\d+位~)×\d+$")
    assert all(rank_re.match(r["key"]) for r in by[("X_sp3_rank", "タイムM")]) and {r["rank_scheme"] for r in by[("X_sp3_rank", "タイムM")]} == {"s1", "s2"}
    assert any(r["key"].startswith("ST平均基準超×前走ST基準") for r in by[("N_stb_pstb", "条件のみ")])
    # 照合結果: 出馬表の馬の部品から作ったキーで当たっている
    hits = [dict(r) for r in con.execute("SELECT * FROM entries_xjoin_merged_hensachi WHERE family IN "
                                         "('X_stb_st3', 'X_spb_sp3_hen', 'X_pspb', 'N_stb_st3', 'X_sp3_rank') ORDER BY family, base")]
    assert len(hits) > 20 and {h["family"] for h in hits} == {"X_stb_st3", "X_spb_sp3_hen", "X_pspb", "N_stb_st3", "X_sp3_rank"}
    assert {h["base"] for h in hits if h["family"] == "N_stb_st3"} == {"条件のみ"}       # 条件のみは base = 条件のみ で1回だけ
    ents = {r[RID]: dict(r) for r in con.execute("SELECT * FROM entries_merged_hensachi")}
    from shisu import matching
    F.configure(root / "selected_families.json", log=lambda m: None)
    step = max(1, len(hits) // 400)
    for h in [*hits[::step], *(x for x in hits if x["family"] in ("N_stb_st3", "X_sp3_rank"))]:
        fields = matching.entry_fields(ents[h[RID]], h["base"])
        assert h["key"] in F.entry_keys(fields, F.get(h["family"]), h["rank_scheme"] or ""), h["key"]
    assert not any(h["family"] == "X_sp3" and h["base"] == "過去SP_3走平均" for h in
                   con.execute("SELECT family, base FROM entries_xjoin_merged_hensachi"))
    con.close()


def test_screening_skips_self_synonyms_too(site, monkeypatch, capsys):
    root, _base, _ent, _sp, _st = site
    import screen_families as SF
    monkeypatch.setattr(SF, "templates", lambda space="default", pool=SF.POOL: [
        (("過去SP3走順",), "s0:single"), (("過去ST今走基準比", "過去ST3走順"), "s0:pair"), (("前走SP基準比",), "s0:single")])
    report = root / "report.csv"
    assert SF.main(["--space", "default", "--tails", "RBAND", "--report", str(report), "--wedge-min-year-count", "2"]) == 0
    rep = pd.read_csv(report, dtype=str, keep_default_na=False, encoding="cp932").drop_duplicates("name").set_index("name")
    assert rep.loc["X_sp3", "masters"] == "10" and rep.loc["X_stb_st3", "masters"] == "10" and rep.loc["X_pspb", "masters"] == "11"
    assert int(rep.loc["X_stb_st3", "groups"]) > 50
    F.configure(root / "selected_families.json", log=lambda m: None)


def entries_table(root, *args):
    import build_db
    rc = build_db.main(["--entries-only", "--no-xjoin", *args])
    con = sqlite3.connect(root / "racing.db")
    try:
        return rc, pd.read_sql('SELECT * FROM entries_merged_hensachi ORDER BY "レースID(新)"', con).fillna("")
    finally:
        con.close()


def test_assumed_going_for_the_entries_and_the_snapshot_in_master_csv(site, capsys):
    root, _base, ent, _sp, _st = site
    ent = ent.sort_values(RID).reset_index(drop=True)
    _rc, plain = entries_table(root)
    cells = o_cells(root / "baseline" / B.FILES["SP"])

    def want(baba_turf, baba_dirt, e, cells=cells):
        out = []
        for i, r in ent.iterrows():
            baba = baba_turf if r["芝ダ"] == "芝" else baba_dirt
            b = o_lookup(cells, r["芝ダ"], "古馬1勝", O_TRACK[r["芝ダ"]][baba], O_PG[r["場所"]], O_BAND[(r["芝ダ"], int(r["距離"]))])
            out.append(o_now_label("SP", *[o_num(e.loc[i, n]) for n in B.NOW_INDICATORS["SP"]], b))
        return out

    assert plain["過去SP今走基準比"].tolist() == want("", "", plain)
    # 想定の馬場: 引数 → 省いたほうは calc が使った想定（entries_out/ の記録）→ 設定ファイル（/ingest で選んだ値）→ auto
    capsys.readouterr()
    _rc, wet = entries_table(root, "--baba-turf", "不", "--baba-dirt", "重")
    assert "今走の馬場の想定 芝 不・ダ 重" in capsys.readouterr().out
    assert wet["過去SP今走基準比"].tolist() == want("不", "重", wet)
    assert wet["前走SP基準比"].tolist() == plain["前走SP基準比"].tolist()          # 前走は実際の馬場で引く
    PP.save_settings("不", "auto")
    _rc, recorded = entries_table(root)            # calc の記録（auto）があるあいだは、設定ファイルより先
    assert recorded["過去SP今走基準比"].tolist() == plain["過去SP今走基準比"].tolist()
    record = root / "entries_out" / PP.USED_BABA_FILE
    held_record = record.read_bytes()
    record.unlink()                                # 記録が無ければ設定ファイル
    _rc, saved = entries_table(root)
    assert saved["過去SP今走基準比"].tolist() == want("不", "", saved) != plain["過去SP今走基準比"].tolist()
    _rc, over = entries_table(root, "--baba-turf", "auto")
    assert over["過去SP今走基準比"].tolist() == plain["過去SP今走基準比"].tolist()
    PP.config.past_perf_settings_path().unlink()
    record.write_bytes(held_record)
    import build_db
    assert build_db.main(["--entries-only", "--baba-turf", "晴"]) == 2
    # 基準値マスタだけをあとから変えても、出馬表側は master を作ったときの写しで引く（master と違う基準値を見ない）
    data = root / "baseline" / B.FILES["SP"]
    kept = data.read_bytes()
    rows = list(csv.DictReader(io.StringIO(kept.decode("utf-8"))))
    for r in rows:
        r["index_mean"] = str(int(r["index_mean"]) + 50)
    data.write_text(B.to_csv_text(rows), encoding="utf-8", newline="")
    try:
        _rc, same = entries_table(root)
        assert same["過去SP今走基準比"].tolist() == plain["過去SP今走基準比"].tolist()
        copy, meta = root / "master_csv" / B.MASTER_FILES["SP"], root / "master_csv" / B.MASTER_META
        held, held_meta = copy.read_bytes(), meta.read_bytes()
        copy.unlink()                              # replace の記録があって写しが無い（SP なしで作った master）→ SP は付けない
        _rc, no_sp = entries_table(root)
        assert (no_sp[["前走SP基準比", "過去SP今走基準比"]] == "").all().all()
        assert no_sp[["前走ST基準比", "過去ST今走基準比", "過去SP3走順"]].equals(plain[["前走ST基準比", "過去ST今走基準比", "過去SP3走順"]])
        copy.write_bytes(b"\xff\xfe\x00garbage")    # 記録があって写しが読めない → 基準値マスタで代用しない（WARN）
        capsys.readouterr()
        _rc, unreadable = entries_table(root)
        assert "[WARN] 基準値: __index_baseline_SP__.csv を読めません" in capsys.readouterr().out
        assert (unreadable[["前走SP基準比", "過去SP今走基準比"]] == "").all().all()
        assert unreadable["過去ST今走基準比"].tolist() == plain["過去ST今走基準比"].tolist()
        copy.unlink()
        meta.unlink()                              # 記録も無い（前の版の master）→ 基準値マスタ
        _rc, raised = entries_table(root)
        assert set(raised["過去SP今走基準比"]) == {"SP今走基準未達"} and set(raised["前走SP基準比"]) <= {"", "前走SP基準大幅未達"}
        copy.write_bytes(held)
        meta.write_bytes(held_meta)
    finally:
        data.write_bytes(kept)
    _rc, back = entries_table(root)
    assert back[list(C.BASELINE_CONDITIONS)].equals(plain[list(C.BASELINE_CONDITIONS)])


def past_entries(base, dates):
    """過去の開催日を出馬表の形に（馬場状態の列つき。取消・除外の馬は出馬表に出ないので除く）。"""
    day = base[base[RID].str[:8].isin(dates)]
    def kaisai(rid18):
        if not rid18:
            return ""
        place = {"05": "東京", "06": "中山"}[rid18[8:10]]
        return f"{rid18[2:8]} {int(rid18[10:12])}{synth2.PLACE_CHAR[place]}{int(rid18[12:14])} {int(rid18[14:16]):>2}R"
    ent = pd.DataFrame({
        RID: day[RID], "場 R": day["場所"].str[0] + day[RID].str[14:16].astype(int).astype(str), "Ｒ": day[RID].str[14:16],
        "場所": day["場所"], "芝ダ": day["距離"].str[0], "距離": day["距離"].str[1:], "外回り": "", "クラス": day["クラス名"],
        "レース名": "", "年限定": day["年齢限定"], "頭": "12", "枠番": day["枠番"], "馬番": day["馬番"], "馬名S": day["馬名"],
        "騎手": day["騎手"], "血統登録番号": day["血統登録番号"], "所属": "美浦", "間隔": day["間隔"], "休明戦目": "2", "斤量": "56",
        "性": "牡", "前場所": day["前走場所"], "前芝ダ": day["前芝・ダ"], "前距離": day["前距離"],
        "前走開催": [kaisai(v) for v in day["前走レースID(新)"]], "前着順": [f"{v}着" if v else "" for v in day["前走着順"]],
        "前人気": day["前走人気"], "前3F順": day["前走上り3F順"], "前脚質": day["前走脚質"].str[0], "前レース印3": day["前走レース印３"],
        "前斤量": "56", "マイニング値": day["指数"], "マイニング順位": day["指数順位"], "馬場状態": day["馬場状態"]})
    return ent[~day["着順"].isin(["取消", "除外"]).to_numpy()].reset_index(drop=True)


def test_a_past_race_day_as_entries_gets_the_same_six_columns_as_the_master(site, capsys):
    """同じ馬・同じレースなら、過去側（master）と出馬表側（DB）で6列が同じ（出馬表に実際の 馬場状態 の列があるとき）。"""
    root, base, _ent, _sp, _st = site
    import build_db, calc_entry_hensachi
    dates = sorted(set(base[RID].str[:8]))[-6:]
    ent = past_entries(base, dates)
    assert len(ent) > 150 and set(ent["馬場状態"]) == {"良", "稍", "重"}
    (root / "past_in").mkdir(exist_ok=True)
    ent.to_csv(root / "past_in" / "shisu_past_1.csv", index=False, encoding="cp932")
    db = root / "past.db"
    shutil.copy(root / "racing.db", db)
    assert calc_entry_hensachi.main(["--merge-only", "--clean", "--in-dir", str(root / "past_in"), "--out-dir", str(root / "past_out")]) == 0
    assert build_db.main(["--entries-only", "--no-xjoin", "--entries-dir", str(root / "past_out"), "--db-path", str(db)]) == 0
    con = sqlite3.connect(db)
    e = pd.read_sql("SELECT * FROM entries_merged_hensachi", con).fillna("").set_index(RID)
    con.close()
    m = read_master(root).set_index(RID).loc[e.index]
    for c in C.BASELINE_CONDITIONS:
        assert e[c].tolist() == m[c].tolist(), c
        assert (e[c] != "").mean() > 0.5, c
    assert len(set(e["過去SP今走基準比"])) == 5 and len(set(e["前走ST基準比"])) == 5      # どの区分も出ている（空を含めて5通り）


def xjoin_rows(db):
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT * FROM entries_xjoin_merged_hensachi").fetchall()
    finally:
        con.close()
    return sorted(map(repr, rows))


def test_assumed_going_from_calc_and_partial_rebuild(site, capsys):
    root, _base, ent, _sp, _st = site
    import build_db, calc_entry_hensachi
    ent = ent.sort_values(RID).reset_index(drop=True)
    cells = o_cells(root / "baseline" / B.FILES["SP"])

    def want(baba_turf, baba_dirt, e):
        out = []
        for i, r in ent.iterrows():
            baba = baba_turf if r["芝ダ"] == "芝" else baba_dirt
            b = o_lookup(cells, r["芝ダ"], "古馬1勝", O_TRACK[r["芝ダ"]][baba], O_PG[r["場所"]], O_BAND[(r["芝ダ"], int(r["距離"]))])
            out.append(o_now_label("SP", *[o_num(e.loc[i, n]) for n in B.NOW_INDICATORS["SP"]], b))
        return out

    # 不正な想定は、どのオプションと一緒でも DB に触らずに終了コード 2
    assert build_db.main(["--entries-only"]) == 0
    digest = lambda: hashlib.md5((root / "racing.db").read_bytes()).hexdigest()
    before = digest()
    for argv in (["--fresh", "--baba-turf", "晴"], ["--entries-only", "--mining-only", "--baba-dirt", "bad"],
                 ["--mode", "foragg", "--baba-turf", "x"]):
        assert build_db.main(argv) == 2 and digest() == before, argv
    # 想定の馬場だけを変えた --mining-only: 6列が変わるので全部の照合に切り替わり、全部作り直した DB と同じ照合結果になる
    a, b = root / "a.db", root / "b.db"
    shutil.copy(root / "racing.db", a)
    shutil.copy(root / "racing.db", b)
    capsys.readouterr()
    assert build_db.main(["--entries-only", "--mining-only", "--baba-turf", "不", "--baba-dirt", "重", "--db-path", str(a)]) == 0
    assert "全部の指数を照合し直します" in capsys.readouterr().out
    assert build_db.main(["--entries-only", "--baba-turf", "不", "--baba-dirt", "重", "--db-path", str(b)]) == 0
    assert xjoin_rows(a) == xjoin_rows(b) != xjoin_rows(root / "racing.db") and len(xjoin_rows(a)) > 1000
    # 同じ想定のままの --mining-only は一部の作り直しで済む
    capsys.readouterr()
    assert build_db.main(["--entries-only", "--mining-only", "--baba-turf", "不", "--baba-dirt", "重", "--db-path", str(a)]) == 0
    assert "の行だけ作り直しました" in capsys.readouterr().out and xjoin_rows(a) == xjoin_rows(b)
    # calc_entry_hensachi に付けた想定は出力のフォルダに残り、引数なしの build_db が同じ想定で今走の基準値を引く
    record = root / "entries_out" / PP.USED_BABA_FILE
    assert json.loads(record.read_text(encoding="utf-8")) == {"baba_turf": "auto", "baba_dirt": "auto"}
    # calc の記録が無いとき、ダの想定は設定ファイル（/ingest で選んだ値）から
    held_record = record.read_bytes()
    record.unlink()
    PP.save_settings("auto", "不")
    _rc, saved = entries_table(root)
    assert saved["過去SP今走基準比"].tolist() == want("", "不", saved) != want("", "", saved)
    PP.config.past_perf_settings_path().unlink()
    record.write_bytes(held_record)
    try:
        assert calc_entry_hensachi.main(["--merge-only", "--clean", "--baba-turf", "不", "--baba-dirt", "不"]) == 0
        assert json.loads(record.read_text(encoding="utf-8")) == {"baba_turf": "不", "baba_dirt": "不"} and PP.load_used_baba(record.parent)
        capsys.readouterr()
        _rc, used = entries_table(root)
        assert "今走の馬場の想定 芝 不・ダ 不（指定の無いほうは calc_entry_hensachi が使った想定）" in capsys.readouterr().out
        assert used["過去SP今走基準比"].tolist() == want("不", "不", used) != want("", "", used)
        _rc, mixed = entries_table(root, "--baba-dirt", "良")                 # 引数が先。省いた芝は calc の想定
        assert mixed["過去SP今走基準比"].tolist() == want("不", "良", mixed)
        PP.save_settings("良", "良")                                          # 設定ファイルより calc の記録が先
        _rc, still = entries_table(root)
        assert still["過去SP今走基準比"].tolist() == used["過去SP今走基準比"].tolist()
        record.write_text("こわれた", encoding="utf-8")                        # 記録が読めなければ設定ファイル
        _rc, fallback = entries_table(root)
        assert fallback["過去SP今走基準比"].tolist() == want("良", "良", fallback)
        # 一部の指数だけの差し替え（--indicators）: 省いた想定は前の計算の記録から（設定ファイル・auto に戻して想定を混ぜない）
        record.unlink()
        PP.config.past_perf_settings_path().unlink()
        assert calc_entry_hensachi.main(["--merge-only", "--clean", "--baba-turf", "稍・重", "--baba-dirt", "稍・重"]) == 0
        mid = {"baba_turf": "稍・重", "baba_dirt": "稍・重"}
        merged = root / "entries_out" / "entries_merged_hensachi.csv"
        before_bytes = merged.read_bytes()
        assert json.loads(record.read_text(encoding="utf-8")) == mid
        assert calc_entry_hensachi.main(["--indicators", "過去SP_5走最高"]) == 0
        assert json.loads(record.read_text(encoding="utf-8")) == mid and merged.read_bytes() == before_bytes   # 同じ想定で計算 → 同じ値
        capsys.readouterr()
        assert calc_entry_hensachi.main(["--indicators", "過去SP_5走最高", "--baba-turf", "不", "--baba-dirt", "不"]) == 0
        assert "だけを、前と違う想定の馬場状態" in capsys.readouterr().out               # 一部だけ違う想定: 記録は前のまま（WARN）
        assert json.loads(record.read_text(encoding="utf-8")) == mid and merged.read_bytes() != before_bytes
        assert calc_entry_hensachi.main(["--indicators", ",".join(PP.INDICATORS), "--baba-turf", "不", "--baba-dirt", "不"]) == 0
        assert json.loads(record.read_text(encoding="utf-8")) == {"baba_turf": "不", "baba_dirt": "不"}     # 8指数とも計算し直した → 記録も
        record.unlink()                                                             # 記録が無ければ、差し替えでも残す
        assert calc_entry_hensachi.main(["--indicators", "過去ST_3走平均", "--baba-dirt", "良"]) == 0
        assert json.loads(record.read_text(encoding="utf-8")) == {"baba_turf": "auto", "baba_dirt": "良"}
        assert calc_entry_hensachi.main(["--mining-only"]) == 0                     # 過去SP・ST を計算し直さない差し替えは記録に触らない
        assert json.loads(record.read_text(encoding="utf-8")) == {"baba_turf": "auto", "baba_dirt": "良"}
    finally:
        PP.config.past_perf_settings_path().unlink(missing_ok=True)
        assert calc_entry_hensachi.main(["--merge-only", "--clean"]) == 0
    assert json.loads(record.read_text(encoding="utf-8")) == {"baba_turf": "auto", "baba_dirt": "auto"}
    assert calc_entry_hensachi.main(["--merge-only", "--clean", "--no-past-perf"]) == 0 and not record.exists()   # 計算していない出力には残さない
    assert calc_entry_hensachi.main(["--merge-only", "--clean"]) == 0 and build_db.main(["--entries-only"]) == 0
    assert digest() != "" and xjoin_rows(root / "racing.db") != xjoin_rows(a)


def test_replace_options_for_the_baseline(site, capsys, monkeypatch):
    root, base, _ent, sp, _st = site
    import replace_csv_ver3
    full = read_master(root)
    data = root / "baseline" / B.FILES["SP"]
    built = data.read_bytes()
    # 既定の最低件数（2000）に満たないデータでは作り直さない（今のマスタのまま）
    shutil.copy(REPO_DATA / B.FILES["SP"], data)
    capsys.readouterr()
    assert replace_csv_ver3.main([]) == 0
    out = capsys.readouterr().out
    assert "満たないので更新しません" in out and data.read_bytes() == (REPO_DATA / B.FILES["SP"]).read_bytes()
    assert (root / "master_csv" / B.MASTER_FILES["SP"]).read_bytes() == data.read_bytes()
    shipped = read_master(root)
    assert (shipped["過去SP今走基準比"] != full["過去SP今走基準比"]).any()        # 受け取ったマスタで引いた値になる
    # --no-baseline-update: 件数が足りても作り直さない
    capsys.readouterr()
    assert replace_csv_ver3.main(["--no-baseline-update", *MIN_ARGS]) == 0
    assert "基準値（SP）" not in capsys.readouterr().out and data.read_bytes() == (REPO_DATA / B.FILES["SP"]).read_bytes()
    # --baseline-decay 1.0: 等重み
    assert replace_csv_ver3.main(["--baseline-decay", "1.0", *MIN_ARGS]) == 0
    want, _root = o_build(base_samples(base, sp), 1.0)
    assert o_cells(data) == {k: r["index_mean"] for k, r in want.items()} and data.read_bytes() != built
    capsys.readouterr()
    assert replace_csv_ver3.main(["--baseline-decay", "0", *MIN_ARGS]) == 1
    assert "[ERROR] 3着内基準値" in capsys.readouterr().out
    assert (read_master(root)[list(C.BASELINE_CONDITIONS)] == "").all().all()     # 基準値を用意できなければ空（master は作る）
    assert not (root / "master_csv" / B.MASTER_FILES["SP"]).exists()
    assert json.loads((root / "master_csv" / B.MASTER_META).read_text(encoding="utf-8")) == {"version": 1, "past_perf": False, "error": True}
    assert B.load_for_master(root / "master_csv") == {}                           # 出馬表側も付けない（master と同じ）
    # SP の基準値マスタが読めない（別の形で保存した など）・無い: WARN を出して SP の2列だけ空に。前の実行の写しは残さない
    assert replace_csv_ver3.main(MIN_ARGS) == 0 and (root / "master_csv" / B.MASTER_FILES["SP"]).exists()
    for breaker in (lambda: data.write_text("レベル,キー,値\nL3,芝 × 2勝 × 良,60\n", encoding="utf-8"), data.unlink):
        breaker()
        capsys.readouterr()
        assert replace_csv_ver3.main(["--no-baseline-update"]) == 0
        out = capsys.readouterr().out
        assert "[WARN] 基準値（SP）: index_baseline_SP.csv が" in out and "前走SP基準比・過去SP今走基準比 は空になります" in out
        assert not (root / "master_csv" / B.MASTER_FILES["SP"]).exists() and (root / "master_csv" / B.MASTER_FILES["ST"]).exists()
        m = read_master(root)
        assert (m[["前走SP基準比", "過去SP今走基準比"]] == "").all().all()
        assert m[["前走ST基準比", "過去ST今走基準比", "過去SP3走順"]].equals(full[["前走ST基準比", "過去ST今走基準比", "過去SP3走順"]])
    # 試しの実行（--result-dir などを別の場所に）は、いつもの基準値マスタを書き換えない。--baseline-dir を指定すればそこに作る
    data.write_bytes((REPO_DATA / B.FILES["SP"]).read_bytes())
    trial = root / "trial_results"
    shutil.copytree(root / "result_indices", trial)
    capsys.readouterr()
    assert replace_csv_ver3.main(["--result-dir", str(trial), *MIN_ARGS]) == 0
    out = capsys.readouterr().out
    assert "--result-dir が既定の場所と違うので、基準値マスタ" in out and "更新しません" in out
    assert data.read_bytes() == (REPO_DATA / B.FILES["SP"]).read_bytes()
    # 根ごと別の場所（SHISU_BASE_DIR）に向けて、基準値の置き場所（SHISU_BASELINE_DIR・--baseline-dir）を指定しない実行も同じ
    calls = []
    with monkeypatch.context() as mp:
        mp.delenv("SHISU_BASELINE_DIR")
        mp.setattr(B, "update_from_history", lambda *a, **k: calls.append(a) or {})
        capsys.readouterr()
        assert replace_csv_ver3.main(["--no-past-perf", *MIN_ARGS]) == 0
        assert "SHISU_BASE_DIR が既定の場所と違うので、基準値マスタ" in capsys.readouterr().out and calls == []
        assert (root / "master_csv" / B.MASTER_FILES["SP"]).read_bytes() == (REPO_DATA / B.FILES["SP"]).read_bytes()
    assert replace_csv_ver3.main(["--clean", *MIN_ARGS]) == 0 and read_master(root).equals(full)
    data.write_bytes((REPO_DATA / B.FILES["SP"]).read_bytes())
    elsewhere = root / "trial_baseline"
    assert replace_csv_ver3.main(["--result-dir", str(trial), "--baseline-dir", str(elsewhere), *MIN_ARGS]) == 0
    assert (elsewhere / B.FILES["SP"]).read_bytes() == built and data.read_bytes() == (REPO_DATA / B.FILES["SP"]).read_bytes()
    assert (root / "master_csv" / B.MASTER_FILES["SP"]).read_bytes() == built      # その実行の master は、指定した場所の基準値で引く
    assert read_master(root).equals(full)
    # --no-past-perf: 8指数を作らない実行では、今走の基準比・3走順は空。前走の基準比は付く
    assert replace_csv_ver3.main(["--clean", "--no-past-perf", *MIN_ARGS]) == 0
    m = read_master(root)
    assert (m[["過去SP今走基準比", "過去ST今走基準比", "過去SP3走順", "過去ST3走順"]] == "").all().all()
    assert m["前走SP基準比"].tolist() == full["前走SP基準比"].tolist() and data.read_bytes() == built
    assert json.loads((root / "master_csv" / B.MASTER_META).read_text(encoding="utf-8"))["past_perf"] is False
    capsys.readouterr()
    _rc, e = entries_table(root)                   # 出馬表側も同じ（calc が付けた 過去SP・ST の列があっても使わない）
    assert "出馬表にも 今走基準比・過去SP/ST 3走順 は付けません" in capsys.readouterr().out
    assert (e[["過去SP今走基準比", "過去ST今走基準比", "過去SP3走順", "過去ST3走順"]] == "").all().all()
    assert (e["前走SP基準比"] != "").sum() > 10 and (e["過去SP_3走平均_順位"].astype(str) != "").any()
    assert not (root / "master_csv" / "master_全場_過去SP_3走平均.csv.gz").exists()
    # 元に戻す（--workers 2 でも同じ master）
    assert replace_csv_ver3.main(["--clean", "--workers", "2", *MIN_ARGS]) == 0
    for ind in ("タイムM", "過去ST_3走平均"):
        again = read_master(root, ind)
        assert again[list(C.BASELINE_CONDITIONS)].equals(read_master(root, "対戦M")[list(C.BASELINE_CONDITIONS)])
    assert read_master(root).equals(full)


def test_update_command(site, capsys, monkeypatch):
    root, base, _ent, sp, st = site
    data = root / "baseline"
    assert B.main([]) == 0
    out = capsys.readouterr().out
    assert "SP:" in out and "ST:" in out and "--update" in out
    assert B.main(["--update", "--kinds", "XX"]) == 2 and B.main(["--update", "--decay", "1.5"]) == 2
    capsys.readouterr()
    assert B.main(["--update"]) == 1                                              # 2000 件に満たない → どれも更新しない
    assert "満たないので更新しません" in capsys.readouterr().out
    assert B.main(["--update", "--min-samples", "30", "--decay", "1.0", "--kinds", "st"]) == 0
    want, _root = o_build(base_samples(base, st), 1.0)
    assert o_cells(data / B.FILES["ST"]) == {k: r["index_mean"] for k, r in want.items()}
    assert "replace_csv_ver3.py → aggregate_stats_ver2.py → build_db.py" in capsys.readouterr().out
    out_dir = root / "elsewhere"
    assert B.main(["--update", "--min-samples", "30", "--out-dir", str(out_dir)]) == 0
    for kind, results in (("SP", sp), ("ST", st)):
        want, _root = o_build(base_samples(base, results), 0.65)
        assert o_cells(out_dir / B.FILES[kind]) == {k: r["index_mean"] for k, r in want.items()}
    assert B.main(["--update", "--csv-dir", str(root / "nope"), "--master-dir", str(root / "nope2")]) == 1
    # 片方だけ最低件数に届く: 届いたほうだけ更新して終了コード 0
    n_sp, n_st = len(base_samples(base, sp)), len(base_samples(base, st))
    assert n_sp < n_st
    part_dir = root / "partial"
    capsys.readouterr()
    assert B.main(["--update", "--min-samples", str(n_st), "--out-dir", str(part_dir)]) == 0
    assert "基準値（SP）: 3着馬のサンプルが" in capsys.readouterr().out
    assert (part_dir / B.FILES["ST"]).exists() and not (part_dir / B.FILES["SP"]).exists()
    # --result-dir: 別のフォルダの結果指数で作る（master_csv の履歴はこの場所のものを使わない）
    other = root / "other_results"
    other.mkdir()
    (other / "SP_all.csv").write_text("".join(f"{r},{v + 100}\n" for r, v in sp.items() if v), encoding="utf-8")
    assert B.main(["--update", "--min-samples", "30", "--kinds", "SP", "--result-dir", str(other), "--master-dir", str(root / "m2"),
                   "--out-dir", str(root / "out2")]) == 0
    shifted = {k: v + 100 for k, v in sp.items() if v}
    want, _root = o_build(base_samples(base, shifted), 0.65)
    assert o_cells(root / "out2" / B.FILES["SP"]) == {k: r["index_mean"] for k, r in want.items()}
    # 履歴を作れない（0バイトの基本CSV）: 終了コード 1
    broken = root / "broken_csv"
    broken.mkdir()
    (broken / "全場_タイムM.csv").write_bytes(b"")
    capsys.readouterr()
    assert B.main(["--update", "--csv-dir", str(broken), "--master-dir", str(root / "m3"), "--out-dir", str(root / "out3")]) == 1
    assert "[ERROR] 過去走の履歴を作れません" in capsys.readouterr().err and not (root / "out3").exists()
    assert B.main(["--update", "--min-samples", "30"]) == 0                       # 既定の decay に戻す
