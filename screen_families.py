# -*- coding: utf-8 -*-
"""family（集計キーの型）の候補を、DB を作らずにメモリ上で一括採点し、実戦で使える条件を選び出す。

  python screen_families.py                         # 候補（初期セット＋単一条件・2条件ペア・因果トリオ・各骨格×1条件）を採点
  python screen_families.py --space all             # ＋芝・ダ×(ペア・トリオ)・骨格×2条件（約2,400テンプレート。時間がかかる）
  python screen_families.py --export-selected       # 選抜した family を selected_families.json に書く（以後の本番で使われる）
  python screen_families.py --indicators タイムM,対戦M --workers 4

入力は master_csv/master_*.csv.gz（replace_csv_ver3.py の出力）。候補ごとに、master 1本（地域×指数）ずつ
本番と同じ関数（shisu.families.build_keys → aggregate_stats_ver2.aggregate_frame → build_db.prepare_agg_frame →
shisu.classify.classify）でオッズ帯4区分を集計し、master を通して合計する。

評価（screening_report.csv。1行 = 候補の family × スキーム）:
  基準A（サンプル数と識別力）
    groups / groups_ge10      成績表のグループ数（件数5以上）/ 件数10以上のグループ数（通常のオッズ帯）
    coverage                  件数10以上のグループに入る馬の割合（指数のある馬のうち）
    adj_groups                adjusted_WEDGE が付いたグループ数
    wedge_top / wedge_bottom / wedge_spread  指数上位帯と下位帯の平均 WEDGE（件数で重み付け）とその差
    adj_wedge_top / adj_wedge_bottom / adj_wedge_spread  同じく adjusted_WEDGE（付いたグループだけ）
    fuku_top / fuku_bottom / fuku_spread     同じく複勝率（頻度。%）
      上位帯・下位帯: 指数順位帯 s1 = 1位 / 10位~、s2 = 1~3位 / 6位~、偏差値帯 = 71以上 / 40以下、指数順位 = 1 / 10以上
    monotonic                 帯の順に平均 WEDGE が下がっていく割合（隣り合う帯の組のうち、上の帯の方が高い割合。1が理想）
  基準B（本番の判定で絶対軸・軸候補などが生まれるか）
    abs_*, axis_*, opp_*, keshi_*   オッズ帯ごと（normal/odds10u/odds10_50/odds50p）の該当キー数と合計（*_total）
選抜（--export-selected）: 次のどちらかを満たす候補（family 単位。指数順位帯はスキーム s1/s2 ごとに）
  ① 絶対軸＋軸候補のキーが --min-axis 個以上
  ② 基準A: groups_ge10 >= --min-groups（10）かつ coverage >= --min-coverage（0.2）かつ
     fuku_spread >= --min-fuku-spread（20.0 ＝ 複勝率の差20%ポイント）かつ wedge_spread >= --min-spread（0.10）、
     そのうち wedge_spread の大きい順に上位 --top-a 個
候補の空間（--space）: 骨格（舞台）× 条件。条件は 重量種別・クラス名・枠番グループ・騎手 と 22条件（28種）
  骨格0（コース不問）: 単一条件・2条件ペア・因果トリオ（騎手×ローテ×前走、物理属性×前走R印3/脚質×前走着順/上り、
                       前走人気×前走着順×今走条件、枠番グループ×初角帯/F係数帯×前走脚質）
  骨格1 芝・ダ / 骨格2 場所×芝・ダ / 骨格3 芝・ダ×距離 / 骨格4 芝・ダ×距離帯 / 骨格5 場所×芝・ダ×距離帯 /
  骨格6 COURSE、COURSE×重量種別×クラス名（normal）
  default = 骨格0 の単一・ペア・トリオ ＋ 骨格2〜6 × 単一条件、all = ＋ 骨格1 ×(ペア・トリオ) ＋ 骨格2・3・4・6 × ペア
            ＋ 骨格5 × 主要ペア
  同じグループにしかならない組み合わせは除く（redundant(): COURSE＋コースの物理属性、場所＋左右・坂、距離＋距離帯、
  左右遷移＋左右 など）。部品の集合が同じ候補は1つにまとめる
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from shisu import config
from shisu import families as F
from shisu.classify import classify
from shisu.conditions import CONDITION_COLUMNS
from shisu.csvio import read_csv_any, write_csv
from shisu.hensachi import all_band_labels
from shisu.naming import file_stem
from shisu.odds import ODDS_MODES

HENSACHI_ORDER = ("71以上", "66~70", "61~65", "51~60", "41~50", "40以下")
# 候補を作るときに組み合わせる条件（28種 = 重量種別・クラス名・枠番グループ・騎手 ＋ 22条件の24列）
POOL = ("重量種別", "クラス名", "枠番グループ", "騎手") + tuple(CONDITION_COLUMNS)
PART_CODES = {"COURSE": "crs", "重量種別": "wt", "クラス名": "cls", "枠番グループ": "frm", "騎手": "jk", "芝・ダ": "sd",
              "場所": "plc", "距離": "dist", "芝(内・外)": "io",
              "時期": "season", "輸送距離": "trans", "間隔帯": "intv", "叩き": "tataki", "R値帯": "rv", "直線帯": "strt",
              "初角帯": "corner", "CFS帯": "cfs", "F係数帯": "fco", "今走クワドラント": "quad", "左右": "lr",
              "左右遷移": "lrchg", "距離帯": "distb", "坂": "slope", "前走クワドラント": "pquad", "斤量帯": "kin",
              "斤量増減": "kinchg", "牝馬限定": "mare", "距離増減": "distchg", "前走着順": "pfin", "前走人気": "ppop",
              "前走上り3F順": "pagari", "前走脚質": "pstyle", "前走R印3": "pr3"}
CLASSES = ("abs", "axis", "opp", "keshi")

# コースだけで決まる物理属性（COURSE と掛け合わせても同じグループになる）
COURSE_ATTRS = ("R値帯", "直線帯", "初角帯", "CFS帯", "F係数帯", "今走クワドラント", "左右", "坂", "距離帯")
PLACE_ATTRS = ("左右", "坂")                       # 場所だけで決まる
# 因果関係の強い3条件（骨格0・骨格1 のトリオ）
ROTATION = ("間隔帯", "叩き", "斤量帯", "斤量増減", "距離増減", "輸送距離")          # ローテ・馬の状態
PREV = ("前走着順", "前走人気", "前走上り3F順", "前走脚質", "前走R印3", "前走クワドラント")  # 前走の条件
PHYSICAL = COURSE_ATTRS                                                          # コースの物理属性
NOW = ("時期", "輸送距離", "間隔帯", "叩き", "斤量帯", "斤量増減", "牝馬限定", "距離増減", "枠番グループ", "騎手",
       "重量種別", "クラス名", "左右遷移")                                        # 今走の条件
# 骨格5（場所×芝・ダ×距離帯）の「主要2条件ペア」に使う条件
MAJOR = ("騎手", "枠番グループ", "重量種別", "クラス名", "間隔帯", "叩き", "斤量帯", "距離増減", "前走着順",
         "前走人気", "前走上り3F順", "前走脚質", "前走R印3")
SKELETONS = {                                       # 骨格（舞台）→ 部品
    "s0": (), "s1": ("芝・ダ",), "s2": ("場所", "芝・ダ"), "s3": ("芝・ダ", "距離"), "s4": ("芝・ダ", "距離帯"),
    "s5": ("場所", "芝・ダ", "距離帯"), "s6": (F.COURSE,), "s6n": (F.COURSE, "重量種別", "クラス名"),
}


def redundant(parts: tuple[str, ...]) -> bool:
    """同じグループにしかならない（または部品が重なる）無意味な組み合わせ。
    - COURSE（または 場所・芝・ダ・距離 がすべて）＋ コースだけで決まる物理属性（R値帯・直線帯・初角帯・CFS帯・F係数帯・
      今走クワドラント・左右・坂・距離帯）
    - 場所 ＋ 左右・坂、距離 ＋ 距離帯、左右遷移 ＋ 左右
    - COURSE ＋ 場所・芝・ダ・距離・芝(内・外)（部品の重なり）"""
    ps = set(parts)
    if len(ps) != len(parts):
        return True
    full_course = F.COURSE in ps or {"場所", "芝・ダ", "距離"} <= ps
    if full_course and ps & set(COURSE_ATTRS):
        return True
    if F.COURSE in ps and ps & {"場所", "芝・ダ", "距離", "芝(内・外)"}:
        return True
    if "場所" in ps and ps & set(PLACE_ATTRS):
        return True
    if "距離" in ps and "距離帯" in ps:
        return True
    return "左右遷移" in ps and "左右" in ps


def causal_trios(pool: tuple[str, ...] = POOL) -> list[tuple[str, ...]]:
    """因果関係の強い3条件:
    ① 騎手 × ローテ・馬の状態 × 前走の条件  ② コースの物理属性 × [前走R印3/前走脚質] × [前走着順/前走上り3F順]
    ③ 前走人気 × 前走着順 × 今走の条件  ④ 枠番グループ × [初角帯/F係数帯] × 前走脚質"""
    out = []
    out += [("騎手", r, p) for r in ROTATION for p in PREV]
    out += [(c, a, b) for c in PHYSICAL for a in ("前走R印3", "前走脚質") for b in ("前走着順", "前走上り3F順")]
    out += [("前走人気", "前走着順", n) for n in NOW]
    out += [("枠番グループ", c, "前走脚質") for c in ("初角帯", "F係数帯")]
    order = {p: i for i, p in enumerate(pool)}
    return [tuple(sorted(t, key=lambda p: order.get(p, 99))) for t in out if all(p in order for p in t)]


def templates(space: str = "default", pool: tuple[str, ...] = POOL) -> list[tuple[tuple[str, ...], str]]:
    """(部品, 種類) の候補。default は 単一条件・2条件ペア・因果トリオ・各骨格×単一条件、
    all はさらに 芝・ダ×(ペア・トリオ)、骨格2〜4×ペア、骨格5×主要ペア、COURSE×ペア。"""
    singles = [(p,) for p in pool]
    pairs = list(itertools.combinations(pool, 2))
    trios = causal_trios(pool)
    out: list[tuple[tuple[str, ...], str]] = []
    add = lambda skel, conds, kind: out.extend((SKELETONS[skel] + c, f"{skel}:{kind}") for c in conds)
    add("s0", singles, "single")
    add("s0", pairs, "pair")
    add("s0", trios, "trio")
    for skel in ("s2", "s3", "s4", "s5", "s6", "s6n"):
        add(skel, singles, "single")
    if space == "all":
        add("s1", pairs, "pair")
        add("s1", trios, "trio")
        for skel in ("s2", "s3", "s4", "s6"):
            add(skel, pairs, "pair")
        add("s5", list(itertools.combinations(MAJOR, 2)), "pair")
    return [(parts, kind) for parts, kind in out if not redundant(parts)]


def candidate_name(parts: tuple[str, ...], tail: str) -> str:
    return "X_" + "_".join(PART_CODES[p] for p in parts) + {"RBAND": "", "HBAND": "_hen", "RANK": "_rank"}[tail]


def make_candidates(space: str = "default", tails: tuple[str, ...] = ("RBAND", "HBAND"),
                    pool: tuple[str, ...] = POOL) -> list[F.Family]:
    """候補の family。初期セット（49種）は名前をそのまま使い、新しい組み合わせは X_… の名前にする。
    部品の集合が同じもの（並びだけ違う）は1つにまとめる（初期セットを優先）。"""
    out: dict[tuple, F.Family] = {}
    for f in F.default_families():
        out[(frozenset(f.parts), f.tail)] = f
    for parts, layer in templates(space, pool):
        for t in tails:
            key = (frozenset(parts), t)
            if key in out:
                continue
            f = F.Family(candidate_name(parts, t), parts, t, layer=f"X:{layer}")
            F.validate(f)
            out[key] = f
    return list(out.values())


# ---------------------------------------------------------------------------
# 採点（master 1本）
# ---------------------------------------------------------------------------
def _top_bottom(fam: F.Family, scheme: str) -> tuple[list[str], str, str]:
    """(帯の順番, 上位帯, 下位帯)。キーの末尾の部品と比べる。"""
    if fam.tail == "HBAND":
        return list(HENSACHI_ORDER), HENSACHI_ORDER[0], HENSACHI_ORDER[-1]
    if fam.tail == "RBAND":
        labs = all_band_labels(scheme, "rank")
        return labs, labs[0], labs[-1]
    return [], "1", "10+"


def _tail_of(keys: pd.Series) -> pd.Series:
    return keys.str.rsplit(F.SEP, n=1).str[-1]


def score_master(path: str, fam_dicts: list[dict], wedge_opts: dict | None, modes: tuple[str, ...]) -> list[dict]:
    """master 1本 × 全候補 → 候補×スキームごとの集計値（合計できる形）。"""
    import aggregate_stats_ver2 as agg
    import build_db
    fams = [F.Family.from_dict(d) for d in fam_dicts]
    df = agg.master_frame(read_csv_any(path))
    ref_year = agg.ref_year_of(df)
    n_horses = len(df)
    slim = agg.with_precomputed(df)                  # 着順・オッズ・配当の解析は master 1本につき1回
    rows = []
    for fam in fams:
        order, top, bottom = None, None, None
        for scheme in fam.schemes:
            rec = {"name": fam.name, "scheme": scheme, "master": file_stem(path), "horses": n_horses}
            for m in modes:
                for c in CLASSES:
                    rec[f"{c}_{m}"] = 0
            keys = F.build_keys(df, fam, scheme)
            if keys.empty:
                rows.append(rec)
                continue
            sub = slim.loc[keys.index].assign(**{agg.KEY: keys.to_numpy()})
            for mode in modes:
                out = agg.aggregate_frame(agg.odds_part(sub, mode), ref_year, wedge_opts)
                if out.empty:
                    continue
                db = build_db.prepare_agg_frame(out)
                cats = [classify({"cnt": r["件数"], "fv": r["複勝率_val"], "rv": r["連対率_val"], "top5": r["掲示板率_val"],
                                  "frc": r["複勝回収値"], "tra": r["単勝適正回収値"]}, mode)
                        for r in db[["件数", "複勝率_val", "連対率_val", "掲示板率_val", "複勝回収値", "単勝適正回収値"]]
                        .to_dict("records")]
                for c in CLASSES:
                    rec[f"{c}_{mode}"] = int(sum(1 for x in cats if x == c))
                if mode != "normal":
                    continue
                ge10 = db[db["件数"].astype(float) >= 10]
                rec["groups"] = int(len(db))
                rec["groups_ge10"] = int(len(ge10))
                rec["adj_groups"] = int(db["adjusted_WEDGE"].notna().sum())
                size = dict(zip(db["結合データ"], db["件数"].astype(int)))
                covered = keys.map(lambda k: size.get(k, 0) >= 10)
                rec["covered"] = int(covered.groupby(level=0).any().sum())
                freq = pd.to_numeric(out["複勝率_val"], errors="coerce")        # CSV の *_val は頻度
                ge10 = ge10.assign(_fuku=freq.reindex(ge10.index).to_numpy(), _tail=_tail_of(ge10["結合データ"]).to_numpy())
                order, top, bottom = _top_bottom(fam, scheme)
                if fam.tail == "RANK":
                    rk = pd.to_numeric(ge10["_tail"], errors="coerce")
                    t_rows, b_rows = ge10[rk == 1], ge10[rk >= 10]
                else:
                    t_rows, b_rows = ge10[ge10["_tail"] == top], ge10[ge10["_tail"] == bottom]
                for name, part_rows in (("top", t_rows), ("bottom", b_rows)):
                    cnt = part_rows["件数"].astype(float)
                    adj = pd.to_numeric(part_rows["adjusted_WEDGE"], errors="coerce")
                    rec[f"a_{name}_sum"] = float((adj.fillna(0) * cnt).sum())        # adjusted_WEDGE（年10件以上）
                    rec[f"na_{name}"] = float(cnt[adj.notna()].sum())
                    rec[f"w_{name}_sum"] = float((pd.to_numeric(part_rows["WEDGE"], errors="coerce").fillna(0)
                                                  * part_rows["件数"].astype(float)).sum())
                    rec[f"f_{name}_sum"] = float((part_rows["_fuku"].fillna(0) * part_rows["件数"].astype(float)).sum())
                    rec[f"n_{name}"] = float(part_rows.loc[pd.to_numeric(part_rows["WEDGE"], errors="coerce").notna(),
                                                           "件数"].astype(float).sum())
                    rec[f"nf_{name}"] = float(part_rows["件数"].astype(float).sum())
                if order:
                    for i, lab in enumerate(order):
                        g = ge10[ge10["_tail"] == lab]
                        w = pd.to_numeric(g["WEDGE"], errors="coerce")
                        rec[f"band{i}_wsum"] = float((w.fillna(0) * g["件数"].astype(float)).sum())
                        rec[f"band{i}_n"] = float(g.loc[w.notna(), "件数"].astype(float).sum())
            rows.append(rec)
    return rows


def _score_job(job) -> list[dict] | str:
    try:
        return score_master(*job)
    except Exception as e:  # 1本の失敗で全体を止めない
        return f"[ERROR] {Path(job[0]).name}: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# まとめ・選抜
# ---------------------------------------------------------------------------
def summarize(rows: list[dict], fams: list[F.Family], modes: tuple[str, ...]) -> pd.DataFrame:
    by = {f.name: f for f in fams}
    df = pd.DataFrame(rows)
    num_cols = [c for c in df.columns if c not in ("name", "scheme", "master")]
    df[num_cols] = df[num_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
    g = df.groupby(["name", "scheme"], sort=False)[num_cols].sum().reset_index()
    g["masters"] = df.groupby(["name", "scheme"], sort=False).size().to_numpy()
    out = pd.DataFrame({
        "name": g["name"], "scheme": g["scheme"],
        "layer": g["name"].map(lambda n: by[n].layer), "tail": g["name"].map(lambda n: by[n].tail),
        "parts": g["name"].map(lambda n: F.SEP.join(by[n].parts)), "label": g["name"].map(lambda n: by[n].display()),
        "masters": g["masters"],
    })
    for c in ("groups", "groups_ge10", "adj_groups", "covered"):
        out[c] = (g[c] if c in g.columns else pd.Series(0, index=g.index)).astype(int)
    out["coverage"] = (out["covered"] / g["horses"].where(g["horses"] > 0)).round(4)
    def ratio(num: str, den: str) -> pd.Series:
        if num not in g.columns or den not in g.columns:
            return pd.Series(np.nan, index=g.index)
        return (g[num] / g[den].replace(0, np.nan)).round(4)

    for name, k, n in (("wedge", "w", "n"), ("adj_wedge", "a", "na"), ("fuku", "f", "nf")):
        out[f"{name}_top"] = ratio(f"{k}_top_sum", f"{n}_top")
        out[f"{name}_bottom"] = ratio(f"{k}_bottom_sum", f"{n}_bottom")
        out[f"{name}_spread"] = (out[f"{name}_top"] - out[f"{name}_bottom"]).round(4)
    mono = []
    for _, r in g.iterrows():
        means = []
        i = 0
        while f"band{i}_wsum" in g.columns:
            n = r.get(f"band{i}_n", 0)
            means.append(r[f"band{i}_wsum"] / n if n else np.nan)
            i += 1
        pairs = [(a, b) for a, b in zip(means, means[1:]) if a == a and b == b]
        mono.append(round(sum(1 for a, b in pairs if a > b) / len(pairs), 3) if pairs else np.nan)
    out["monotonic"] = mono
    for c in CLASSES:
        for m in modes:
            out[f"{c}_{m}"] = (g[f"{c}_{m}"] if f"{c}_{m}" in g.columns else pd.Series(0, index=g.index)).astype(int)
        out[f"{c}_total"] = sum(out[f"{c}_{m}"] for m in modes)
    return out


def select(report: pd.DataFrame, min_axis: int, min_groups: int, min_coverage: float, min_spread: float,
           top_a: int, min_fuku_spread: float = 20.0) -> pd.DataFrame:
    r = report.copy()
    r["select_B"] = (r["abs_total"] + r["axis_total"]) >= min_axis
    ok_a = ((r["groups_ge10"] >= min_groups) & (r["coverage"] >= min_coverage)
            & (r["fuku_spread"] >= min_fuku_spread) & (r["wedge_spread"] >= min_spread))
    rank_a = r[ok_a].sort_values("wedge_spread", ascending=False).head(top_a).index
    r["select_A"] = r.index.isin(rank_a)
    r["selected"] = r["select_A"] | r["select_B"]
    r["reason"] = ["・".join(x for x, f in (("B:軸が出る", b), ("A:識別力", a)) if f) for a, b in zip(r["select_A"], r["select_B"])]
    return r


def selected_families(report: pd.DataFrame, fams: list[F.Family]) -> list[F.Family]:
    by = {f.name: f for f in fams}
    out = []
    for name, g in report[report["selected"]].groupby("name", sort=False):
        f = by[name]
        if f.uses_schemes:
            sch = tuple(s for s in ("s1", "s2") if s in set(g["scheme"]))
            f = F.Family(f.name, f.parts, f.tail, f.label, f.layer, () if sch == ("s1", "s2") else sch)
        out.append(f)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master-dir", default=str(config.master_dir()))
    ap.add_argument("--space", choices=["default", "all", "initial"], default="default",
                    help="候補の範囲: initial=初期セット49種だけ / default=＋単一条件・2条件ペア・コース/距離帯×1条件 / all=＋×2条件")
    ap.add_argument("--indicators", default="", help="使う指数（カンマ区切り。既定は全部）")
    ap.add_argument("--regions", default="", help="使う地域（カンマ区切りの部分一致。既定は全部）")
    ap.add_argument("--odds", default=",".join(ODDS_MODES), help="判定するオッズ帯（既定は4区分すべて）")
    ap.add_argument("--max-candidates", type=int, default=0, help="候補の数の上限（0=上限なし。試しに動かすとき用）")
    ap.add_argument("--report", default=str(config.base_dir() / "screening_report.csv"))
    ap.add_argument("--export-selected", nargs="?", const=str(config.selected_families_path()), default=None,
                    help="選抜した family を JSON に書く（既定の書き先 selected_families.json）")
    ap.add_argument("--min-axis", type=int, default=1, help="選抜①: 絶対軸＋軸候補のキー数の下限（既定1）")
    ap.add_argument("--min-groups", type=int, default=10, help="選抜②: 件数10以上のグループ数の下限（既定10）")
    ap.add_argument("--min-coverage", type=float, default=0.2, help="選抜②: カバー率の下限（既定0.2）")
    ap.add_argument("--min-spread", type=float, default=0.10, help="選抜②: wedge_spread の下限（既定0.10）")
    ap.add_argument("--min-fuku-spread", type=float, default=20.0,
                    help="選抜②: fuku_spread（上位帯と下位帯の複勝率の差。%ポイント）の下限（既定20.0）")
    ap.add_argument("--top-a", type=int, default=30, help="選抜②で採る数の上限（既定30）")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--wedge-min-year-count", type=int, default=None)
    args = ap.parse_args(argv)

    import aggregate_stats_ver2 as agg
    masters = [p for p in sorted(Path(args.master_dir).glob("master_*.csv.gz")) if file_stem(p) not in agg.SKIP_MASTERS]
    from shisu.indicators import canonical_indicator, parse_source_name
    # ファイル名の地域・指数名と同じ形にそろえる（全角・別名でも当たるように）
    inds = {canonical_indicator(s) for s in args.indicators.split(",") if s.strip()}
    regs = [unicodedata.normalize("NFKC", s.strip()) for s in args.regions.split(",") if s.strip()]
    picked = []
    for p in masters:
        region, ind = parse_source_name(file_stem(p)[len("master_"):])
        region_n = unicodedata.normalize("NFKC", region)
        if inds and canonical_indicator(ind) not in inds:
            continue
        if regs and not any(r in region_n for r in regs):
            continue
        picked.append(p)
    if not picked:
        print(f"[ERROR] master がありません（{args.master_dir}。指数・地域の指定も確認）", file=sys.stderr)
        return 2
    modes = tuple(m for m in args.odds.split(",") if m in ODDS_MODES) or ("normal",)
    if "normal" not in modes:
        modes = ("normal",) + modes
    fams = F.default_families() if args.space == "initial" else make_candidates(args.space)
    if args.max_candidates:
        fams = fams[: args.max_candidates]
    opts = agg.wedge_options(args.wedge_min_year_count)
    n_var = sum(len(f.schemes) for f in fams)
    print(f"[INFO] 候補 {len(fams)} family（{n_var} 通り）× master {len(picked)} 本 × オッズ帯 {len(modes)}")
    t0 = time.time()
    jobs = [(str(p), [f.to_dict() for f in fams], opts, modes) for p in picked]
    rows: list[dict] = []
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(_score_job, jobs))
    else:
        results = []
        for i, j in enumerate(jobs, 1):
            results.append(_score_job(j))
            print(f"[INFO] {i}/{len(jobs)} {Path(j[0]).name}（{time.time() - t0:.0f}秒）", flush=True)
    failed = []
    for r in results:
        if isinstance(r, str):
            print(r, file=sys.stderr)
            failed.append(r)
        else:
            rows += r
    if not rows:
        print("[ERROR] 採点できた master がありません", file=sys.stderr)
        return 2
    report = select(summarize(rows, fams, modes), args.min_axis, args.min_groups, args.min_coverage,
                    args.min_spread, args.top_a, args.min_fuku_spread)
    report = report.sort_values(["selected", "abs_total", "axis_total", "wedge_spread"], ascending=False)
    write_csv(report, Path(args.report))
    n_sel = int(report["selected"].sum())
    print(f"[INFO] 採点 {len(report)} 通り（{time.time() - t0:.0f}秒）→ {args.report}")
    print(f"[INFO] 選抜: {n_sel} 通り（①軸が出る {int(report['select_B'].sum())} / ②識別力 {int(report['select_A'].sum())}）")
    if failed:
        print(f"[ERROR] 採点できなかった master が {len(failed)} 本あります。レポートは書きましたが、"
              "選抜（selected_families.json）は書きません（原因を直してやり直してください）", file=sys.stderr)
        return 1
    if args.export_selected:
        sel = selected_families(report, fams)
        if not sel:
            print("[WARN] 選抜された family がありません。selected_families.json は書きません", file=sys.stderr)
            return 1
        F.save_families(args.export_selected, sel, {"source": "screen_families.py", "space": args.space,
                                                    "criteria": {"min_axis": args.min_axis, "min_groups": args.min_groups,
                                                                 "min_coverage": args.min_coverage,
                                                                 "min_spread": args.min_spread,
                                                                 "min_fuku_spread": args.min_fuku_spread,
                                                                 "top_a": args.top_a}})
        print(f"[INFO] {len(sel)} family を {args.export_selected} に書きました。"
              "以後の aggregate_stats_ver2.py・build_db.py・照合はこの family を使います")
    return 0


if __name__ == "__main__":
    sys.exit(main())
