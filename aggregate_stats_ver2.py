# -*- coding: utf-8 -*-
"""master（master_csv/master_*.csv.gz、1行=1頭）→ family ごと・オッズ帯ごとの成績表（agg_csv/）。

使う family は shisu/families.py の台帳（selected_families.json があればその定義、無ければ初期セット49種）。
master 1本（1地域×1指数）について、family（とスキーム s1/s2）ごとにキーを作り、オッズ帯4区分で集計する:
  通常（絞り込みなし）/ odds10u（10倍未満）/ odds10_50（10以上50未満）/ odds50p（50倍以上）
  出力名: agg_csv/foragg_{オッズ接頭辞}{rankband_スキーム_}{family}_{地域}_{指数名}.csv（命名は shisu/naming.py）
  agg_csv/families.json に使った family の定義を書く（build_db・画面が family の名前と部品を知るため）
集計に使うのは master の 指数有効=1 の馬（除外レース・新馬除外・欠損の馬を除く）。
family ごとの素材ファイルは作らない（49 family × 3 の素材は容量・時間ともに大きすぎるため。キーはメモリ上で作る）。

出力列:
  結合データ, 件数, 勝率_val, 勝率, 連対率_val, 連対率, 複勝率_val, 複勝率, 2着率_val, 2着率,
  3着率_val, 3着率, 掲示板率_val, 掲示板率, 平均着順, 単勝回収値, 複勝回収値, 単勝適正回収値,
  WIN, EDGE, WEDGE, adjusted_WEDGE, adjusted_WEDGE_年数, adjusted_WEDGE_件数, adjusted_WEDGE_最終年,
  提案勝率, 提案連対率, 提案複勝率, 提案2着率, 提案3着率, 場所, 芝・ダ, 距離, 芝(内・外)
  - 「勝率」などの文字列は '頻度%（推定%）'。*_val はこの CSV では頻度%。
    （build_db が DB に取り込むとき、5つの率の *_val は括弧内の推定値に置き換わる）
  - 推定値は shisu/estimates.py（既定: 事後分布の下側10%点、複勝率は頻度20%以下なら上側90%点）
    AGG_PROP_MODE=mean にすると勝率・連対率・2着率・3着率は事後平均になる（複勝率は常に上の分位点の切替）
  - WIN / EDGE / WEDGE / adjusted_WEDGE は shisu/wedge.py（定義・年度加重の規則はそちらの説明を参照）
    年度の窓の終わり（window=data）は、オッズ帯で絞る前の master 全体の最終年

環境変数: AGG_PROP_MODE(lower|mean), AGG_LOWER_Q(0.10), AGG_UPPER_Q(0.90), AGG_PRIOR_ALPHA/BETA(0.5),
          PLC_SWITCH_THRESHOLD_PCT(20), AGG_MIN_SAMPLE(5), AGG_RANKBAND_SCHEMES(例 "s1"),
          AGG_WEDGE_MIN_YEAR_COUNT(10), AGG_WEDGE_MAX_YEARS(10), AGG_WEDGE_DECAY(0.65), AGG_WEDGE_WINDOW(data|key)
          （WEDGE の4つは --wedge-min-year-count / --wedge-max-years / --wedge-decay / --wedge-window でも
           指定できる。引数が優先）
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from shisu import config
from shisu.csvio import read_csv_any, write_csv
from shisu.estimates import beta_quantile, has_scipy, posterior_mean
from shisu.finish import parse_finish
from shisu import families as fam_registry
from shisu.naming import agg_filename, file_stem
from shisu.normalize import race_date
from shisu.odds import ODDS_MODES, odds_mask, odds_mask_numeric
from shisu.wedge import (DEFAULT_DECAY, DEFAULT_MAX_YEARS, DEFAULT_MIN_YEAR_COUNT, WEDGE_COLUMNS, WINDOWS,
                         check_decay, valid_years, wedge_columns)

KEY = "結合データ"
RID = "レースID(新)"
BAND = "指数順位帯"

PROP_MODE = os.getenv("AGG_PROP_MODE", "lower").lower()
LOWER_Q = float(os.getenv("AGG_LOWER_Q", "0.10"))
UPPER_Q = float(os.getenv("AGG_UPPER_Q", "0.90"))
PRIOR_A = float(os.getenv("AGG_PRIOR_ALPHA", "0.5"))
PRIOR_B = float(os.getenv("AGG_PRIOR_BETA", "0.5"))
PLC_SWITCH_PCT = float(os.getenv("PLC_SWITCH_THRESHOLD_PCT", "20.0"))
MIN_SAMPLE = int(os.getenv("AGG_MIN_SAMPLE", "5"))
WEDGE_MIN_YEAR_COUNT = int(os.getenv("AGG_WEDGE_MIN_YEAR_COUNT", str(DEFAULT_MIN_YEAR_COUNT)))
WEDGE_MAX_YEARS = int(os.getenv("AGG_WEDGE_MAX_YEARS", str(DEFAULT_MAX_YEARS)))
WEDGE_DECAY = float(os.getenv("AGG_WEDGE_DECAY", str(DEFAULT_DECAY)))
WEDGE_WINDOW = os.getenv("AGG_WEDGE_WINDOW", "data").lower()


def wedge_options(min_year_count: int | None = None, window: str | None = None,
                  max_years: int | None = None, decay: float | None = None) -> dict:
    """adjusted_WEDGE の設定（引数 > 環境変数 > 既定）。wedge_columns() にそのまま渡せる dict。"""
    return {"min_year_count": WEDGE_MIN_YEAR_COUNT if min_year_count is None else int(min_year_count),
            "window": window or WEDGE_WINDOW,
            "max_years": WEDGE_MAX_YEARS if max_years is None else int(max_years),
            "decay": WEDGE_DECAY if decay is None else float(decay)}

OUT_COLUMNS = [KEY, "件数", "勝率_val", "勝率", "連対率_val", "連対率", "複勝率_val", "複勝率",
               "2着率_val", "2着率", "3着率_val", "3着率", "掲示板率_val", "掲示板率",
               "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値", *WEDGE_COLUMNS,
               "提案勝率", "提案連対率", "提案複勝率", "提案2着率", "提案3着率",
               "場所", "芝・ダ", "距離", "芝(内・外)"]
REF_COLUMNS = ["場所", "芝・ダ", "距離", "芝(内・外)"]


def _pct(v) -> str:
    return "" if v is None or pd.isna(v) else f"{v:.2f}%"


def _pair(freq, est) -> str:
    f, e = _pct(freq), _pct(est)
    if f and e:
        return f"{f}（{e}）"
    return f or e


def _estimate(k: pd.Series, n: pd.Series, upper: pd.Series | None = None) -> pd.Series:
    """推定値（%）。upper を渡したとき（複勝率）は、旧版どおり AGG_PROP_MODE に関係なく
    頻度≤閾値なら上側 q 点・それ以外は下側 q 点を使う（消し判定を保守的にするため）。"""
    if upper is None and PROP_MODE == "mean":
        return posterior_mean(k, n, PRIOR_A, PRIOR_B) * 100.0
    lo = beta_quantile(k, n, LOWER_Q, PRIOR_A, PRIOR_B) * 100.0
    if upper is None:
        return lo
    hi = beta_quantile(k, n, UPPER_Q, PRIOR_A, PRIOR_B) * 100.0
    return lo.where(~upper, hi)


# master から先に作っておく列（aggregate_frame が行ごとに求める値。family・オッズ帯ごとに作り直さない）
PRE_FINISH = {"finish_num": "_pre_finish_num", "denom": "_pre_denom", "win": "_pre_win", "second": "_pre_second",
              "third": "_pre_third", "place": "_pre_place", "board": "_pre_board"}
PRE_NUM = {"単勝オッズ": "_pre_odds", "単勝配当": "_pre_pay_win", "複勝配当": "_pre_pay_plc"}
PRE_YEAR = "_pre_year"
# aggregate_frame が読む列（これ以外の master の列はキーを作るときだけ使う）
AGG_INPUT_COLUMNS = (RID, "着順", "頭数", "馬番", "馬名", "単勝オッズ", "単勝配当", "複勝配当", "year", "日付",
                     "場所", "芝・ダ", "距離", "芝(内・外)")


def with_precomputed(df: pd.DataFrame) -> pd.DataFrame:
    """aggregate_frame の入力に、行ごとに決まる値（着順の解析・オッズ・配当・年）を先に足した細い DataFrame。
    family ごと・オッズ帯ごとに aggregate_frame を呼ぶとき、毎回の文字列処理を省く（結果は同じ）。"""
    out = df[[c for c in AGG_INPUT_COLUMNS if c in df.columns]].copy()
    fl = parse_finish(out["着順"], out["頭数"] if "頭数" in out.columns else None)
    for k, c in PRE_FINISH.items():
        out[c] = fl[k]
    for src, c in PRE_NUM.items():
        v = pd.to_numeric(out[src], errors="coerce") if src in out.columns else pd.Series(np.nan, index=out.index)
        out[c] = v if src == "単勝オッズ" else v.fillna(0)
    out[PRE_YEAR] = year_series(out)
    return out


def year_series(df: pd.DataFrame) -> pd.Series:
    """year 列（無い・空なら 日付 か レースID(新) の先頭4桁）。"""
    if PRE_YEAR in df.columns:
        return df[PRE_YEAR]
    y = df["year"].astype(str) if "year" in df.columns else pd.Series("", index=df.index)
    if (y == "").any():
        src = df["日付"] if "日付" in df.columns else race_date(df.get(RID, pd.Series("", index=df.index)))
        y = y.where(y != "", src.astype(str).str.slice(0, 4))
    return y


def ref_year_of(df: pd.DataFrame) -> int | None:
    """年度加重の窓の終わり（master 全体の最終年）。"""
    y = valid_years(year_series(df))
    return int(y.max()) if y.notna().any() else None


def ref_columns_for(fam) -> tuple[str, ...]:
    """成績表の 場所・芝・ダ・距離・芝(内・外) に値を入れる列。family がその部品で絞っているときだけ
    （コースを含む family は4つとも、芝・ダ だけを含む family は 芝・ダ だけ。ほかは空。画面の絞り込み用）。"""
    if fam_registry.COURSE in fam.parts:
        return tuple(REF_COLUMNS)
    return ("芝・ダ",) if "芝・ダ" in fam.parts else ()


def aggregate_frame(df: pd.DataFrame, ref_year: int | None = None, wedge_opts: dict | None = None,
                    ref_columns: tuple[str, ...] | None = None) -> pd.DataFrame:
    """キー付きの DataFrame（master の行。文字列）→ 成績表。ref_year は年度加重の窓の終わり（省略時は df の最終年）。
    wedge_opts は wedge_options() の dict（省略時は環境変数・既定値）。
    ref_columns は 場所・芝・ダ・距離・芝(内・外) のうち値を入れる列（省略時は4つとも。ref_columns_for）。"""
    for c in (RID, KEY, "着順"):
        if c not in df.columns:
            raise KeyError(c)
    df = df[df[KEY].astype(str) != ""]

    # 重複排除（1頭=1件）: レースID × 結合データ（× 順位帯）× 馬番（無ければ馬名）
    # 馬を識別する列が無いときは、レースID が馬単位（18桁）の場合だけ重複排除する。
    # レース単位のIDのまま重複排除すると、同じレース・同じキーの別の馬が1頭にまとめられて件数が減る。
    subset = [RID, KEY] + ([BAND] if BAND in df.columns else [])
    horse = next((c for c in ("馬番", "馬名") if c in df.columns and (df[c] != "").any()), None)
    if horse:
        subset.append(horse)
    if horse or df[RID].astype(str).str.fullmatch(r"\d{18}").all():
        df = df.drop_duplicates(subset=subset, keep="first")

    if PRE_FINISH["finish_num"] in df.columns:
        fl = df[list(PRE_FINISH.values())].rename(columns={v: k for k, v in PRE_FINISH.items()})
    else:
        fl = parse_finish(df["着順"], df["頭数"] if "頭数" in df.columns else None)
    use = fl["denom"]
    d, f = df[use], fl[use]
    # 件数 < MIN_SAMPLE のグループは出力しないので、先に除いてから計算する（推定値・文字列整形の無駄を省く）
    codes0, _ = pd.factorize(d[KEY].to_numpy())
    keep = np.bincount(codes0)[codes0] >= MIN_SAMPLE if len(codes0) else np.zeros(0, dtype=bool)
    d, f = d[keep], f[keep]
    if d.empty:
        return pd.DataFrame(columns=OUT_COLUMNS)

    # グループは結合データの整数コードで束ねる（文字列のまま何度も groupby するより速い。並びは結合データの昇順）
    codes, uniques = pd.factorize(d[KEY].to_numpy(), sort=True)
    g = pd.Series(codes, index=d.index)
    n = g.groupby(g).size()
    cnt = lambda col: f[col].groupby(g).sum().reindex(n.index).astype(float)
    wins, sec, thr, plc, board = cnt("win"), cnt("second"), cnt("third"), cnt("place"), cnt("board")

    def freq(k):
        return (k / n * 100.0).replace([np.inf, -np.inf], np.nan)

    win_f, ren_f, plc_f, sec_f, thr_f, top5_f = freq(wins), freq(wins + sec), freq(plc), freq(sec), freq(thr), freq(board)
    win_e = _estimate(wins, n)
    ren_e = _estimate(wins + sec, n)
    plc_e = _estimate(plc, n, upper=(plc_f <= PLC_SWITCH_PCT).fillna(False))
    sec_e = _estimate(sec, n)
    thr_e = _estimate(thr, n)

    avg_fin = f["finish_num"].groupby(g).mean().reindex(n.index)
    if PRE_NUM["単勝オッズ"] in d.columns:
        pay_win, pay_plc, odds = d[PRE_NUM["単勝配当"]], d[PRE_NUM["複勝配当"]], d[PRE_NUM["単勝オッズ"]]
    else:
        pay_win = pd.to_numeric(d.get("単勝配当", pd.Series(index=d.index, dtype=float)), errors="coerce").fillna(0)
        pay_plc = pd.to_numeric(d.get("複勝配当", pd.Series(index=d.index, dtype=float)), errors="coerce").fillna(0)
        odds = pd.to_numeric(d.get("単勝オッズ", pd.Series(index=d.index, dtype=float)), errors="coerce")
    rtn_win = (pay_win.where(f["win"], 0).groupby(g).sum().reindex(n.index) / n)
    rtn_plc = (pay_plc.where(f["place"], 0).groupby(g).sum().reindex(n.index) / n)
    # 単勝適正回収値: w=1/オッズ。100×Σ(w×配当/100×的中)/Σw（≒ 実勝利数 ÷ オッズから見た期待勝利数 ×100）
    w = pd.Series(np.where(odds > 0, 1.0 / odds, np.nan), index=d.index)
    w_sum = w.groupby(g).sum(min_count=1).reindex(n.index)
    w_ret = (w * pay_win / 100.0 * f["win"].astype(float)).groupby(g).sum(min_count=1).reindex(n.index)
    rtn_adj = (100.0 * w_ret / w_sum).replace([np.inf, -np.inf], np.nan)

    out = pd.DataFrame({KEY: uniques[n.index.to_numpy()], "件数": n.values.astype(int)})
    for name, fr, es in (("勝率", win_f, win_e), ("連対率", ren_f, ren_e), ("複勝率", plc_f, plc_e),
                         ("2着率", sec_f, sec_e), ("3着率", thr_f, thr_e)):
        out[f"{name}_val"] = fr.round(2).values
        out[name] = [_pair(a, b) for a, b in zip(fr.values, es.values)]
        out[f"提案{name}"] = [_pct(b) for b in es.values]
    out["掲示板率_val"] = top5_f.round(2).values
    out["掲示板率"] = [_pct(v) for v in top5_f.values]
    out["平均着順"] = avg_fin.round(2).values
    out["単勝回収値"] = rtn_win.round(2).values
    out["複勝回収値"] = rtn_plc.round(2).values
    out["単勝適正回収値"] = rtn_adj.round(2).values
    wd = wedge_columns(g, f["finish_num"], odds, year_series(d),
                       ref_year=ref_year if ref_year is not None else ref_year_of(d),
                       **(wedge_opts or wedge_options())).reindex(n.index)
    for c in WEDGE_COLUMNS:
        v = wd[c]
        out[c] = (v.round(4) if v.dtype.kind == "f" else v).values
    fill = REF_COLUMNS if ref_columns is None else ref_columns
    for c in REF_COLUMNS:
        if c in fill and c in d.columns:
            first = d[c].where(d[c] != "").groupby(g).first().reindex(n.index)
            out[c] = first.fillna("").values
        else:
            out[c] = ""
    return out.reset_index(drop=True)[OUT_COLUMNS]


MASTER_PREFIX = "master_"
SKIP_MASTERS = ("__inner_outer__",)


def master_frame(df: pd.DataFrame) -> pd.DataFrame:
    """master → 集計に使う馬（指数有効=1）。"""
    if "指数有効" in df.columns:
        df = df[df["指数有効"].astype(str) == "1"]
    return df


def family_frames(df: pd.DataFrame, fams=None, slim: pd.DataFrame | None = None):
    """(family, スキーム, キー付きの DataFrame) を順に返す。キーは shisu.families.build_keys で作る。
    返す DataFrame は aggregate_frame の入力に要る列だけ（slim。省略時は with_precomputed(df)）。"""
    slim = with_precomputed(df) if slim is None else slim
    for fam in (fams if fams is not None else fam_registry.active()):
        for scheme in fam.schemes:
            keys = fam_registry.build_keys(df, fam, scheme)
            sub = slim.loc[keys.index]                       # s2 の重なりでは同じ行が2回出る（loc が複製する）
            sub = sub.assign(**{KEY: keys.to_numpy()})
            yield fam, scheme, sub


def odds_part(sub: pd.DataFrame, mode: str) -> pd.DataFrame:
    """オッズ帯 mode の行（オッズ欠損は normal にだけ入る）。"""
    if mode == "normal":
        return sub
    if PRE_NUM["単勝オッズ"] in sub.columns:
        return sub[odds_mask_numeric(sub[PRE_NUM["単勝オッズ"]], mode)]
    return sub[odds_mask(sub["単勝オッズ"], mode)] if "単勝オッズ" in sub.columns else sub.iloc[0:0]


def restrict_schemes(fams, schemes: set[str] | None):
    """AGG_RANKBAND_SCHEMES（例 {"s1"}）で、s1/s2 の family の作るスキームを絞る（無くなる family は外す）。"""
    if not schemes:
        return list(fams)
    out = []
    for f in fams:
        if not f.uses_schemes:
            out.append(f)
            continue
        keep = tuple(sc for sc in f.schemes if sc in schemes)
        if keep:
            out.append(fam_registry.Family(f.name, f.parts, f.tail, f.label, f.layer,
                                           () if keep == ("s1", "s2") else keep))
    return out


def aggregate_master(src: str, out_dir: str, wedge_opts: dict | None = None, schemes: set[str] | None = None,
                     families: list[dict] | None = None) -> str:
    """master 1本 → 成績表。families は使う family の定義（to_dict の list。並列の子プロセスにも同じ定義を渡す）。"""
    src = Path(src)
    stem = file_stem(src)
    if not stem.startswith(MASTER_PREFIX) or stem in SKIP_MASTERS:
        return f"[SKIP] {src.name}: master のファイルではありません"
    if families is not None:
        fam_registry.set_active([fam_registry.Family.from_dict(d) for d in families])
    source = stem[len(MASTER_PREFIX):]
    df = master_frame(read_csv_any(src))
    for c in (RID, "着順"):
        if c not in df.columns:
            return f"[SKIP] {src.name}: 列 {c} がありません（master ではない可能性）"
    ref_year = ref_year_of(df)
    groups = adj = n_files = 0
    fams = restrict_schemes(fam_registry.active(), schemes)
    for fam in fams:                     # 作らないスキームの、前の実行の成績表は消す（build_db が取り込まないように）
        for sc in ("s1", "s2") if fam.uses_schemes else ():
            if sc not in fam.schemes:
                for mode in ODDS_MODES:
                    (Path(out_dir) / agg_filename(fam.name, mode, sc, source)).unlink(missing_ok=True)
    for fam, scheme, sub in family_frames(df, fams):
        for mode in ODDS_MODES:
            out = aggregate_frame(odds_part(sub, mode), ref_year, wedge_opts, ref_columns_for(fam))
            path = Path(out_dir) / agg_filename(fam.name, mode, scheme, source)
            if out.empty:                    # 0件の成績表は書かない（前の実行で書いた同じ名前のファイルは消す）
                path.unlink(missing_ok=True)
                continue
            write_csv(out, path)
            n_files += 1
            groups += len(out)
            adj += int(out["adjusted_WEDGE"].notna().sum()) if len(out) else 0
    return (f"[OK] {src.name}: {n_files} files groups={groups}(adj {adj}) / 年度窓の終わり={ref_year}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", default=str(config.master_dir()), help="master のフォルダ（既定 master_csv）")
    ap.add_argument("--families", default=None,
                    help="使う family の定義（JSON）。既定: selected_families.json があればそれ、無ければ初期セット")
    ap.add_argument("--out-dir", default=str(config.agg_dir()))
    ap.add_argument("--clean", action="store_true", help="出力先 agg_csv を空にしてから実行")
    ap.add_argument("--workers", type=int, default=1, help="並列数（既定1）")
    ap.add_argument("--wedge-min-year-count", type=int, default=None,
                    help=f"adjusted_WEDGE で使う年の最低件数（既定 {WEDGE_MIN_YEAR_COUNT}。環境変数 AGG_WEDGE_MIN_YEAR_COUNT）")
    ap.add_argument("--wedge-max-years", type=int, default=None,
                    help=f"adjusted_WEDGE で遡る年数の上限（窓の終わりから。既定 {WEDGE_MAX_YEARS}。環境変数 AGG_WEDGE_MAX_YEARS）")
    ap.add_argument("--wedge-decay", type=float, default=None,
                    help=f"年の減衰率（1年古くなるごとのウェイトの倍率。0<d≤1。既定 {WEDGE_DECAY}。環境変数 AGG_WEDGE_DECAY）")
    ap.add_argument("--wedge-window", choices=WINDOWS, default=None,
                    help=f"年度の窓の終わり: data=master 全体の最終年 / key=結合データ自身の最終年（既定 {WEDGE_WINDOW}）")
    args = ap.parse_args(argv)
    opts = wedge_options(args.wedge_min_year_count, args.wedge_window, args.wedge_max_years, args.wedge_decay)
    if opts["window"] not in WINDOWS:
        print(f"[ERROR] AGG_WEDGE_WINDOW は {WINDOWS} のどれか: {opts['window']}", file=sys.stderr)
        return 2
    try:
        check_decay(opts["decay"])
        if opts["max_years"] < 1:
            raise ValueError(f"max_years は1以上: {opts['max_years']}")
    except ValueError as e:
        print(f"[ERROR] WEDGE の設定: {e}", file=sys.stderr)
        return 2

    files = [p for p in sorted(Path(args.in_dir).glob(f"{MASTER_PREFIX}*.csv.gz")) if file_stem(p) not in SKIP_MASTERS]
    schemes = {s.strip() for s in os.getenv("AGG_RANKBAND_SCHEMES", "").split(",") if s.strip()}
    if not files:
        print(f"[ERROR] master（master_*.csv.gz）がありません: {args.in_dir}。replace_csv_ver3.py を先に実行してください",
              file=sys.stderr)
        return 2
    try:
        fams = fam_registry.configure(args.families)
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] family の定義: {e}", file=sys.stderr)
        return 2
    fams = restrict_schemes(fams, schemes)
    if not fams:
        print(f"[ERROR] AGG_RANKBAND_SCHEMES={','.join(sorted(schemes))} で作る family がありません", file=sys.stderr)
        return 2
    fam_dicts = [f.to_dict() for f in fams]
    if args.clean and Path(args.out_dir).exists():
        shutil.rmtree(args.out_dir)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    # build_db・照合・画面はこの定義を使う（AGG_RANKBAND_SCHEMES で絞ったスキームもここに残る）
    fam_registry.save_families(Path(args.out_dir) / "families.json", fams, {"source": fam_registry.active_source()})
    n_var = sum(len(f.schemes) for f in fams)
    print(f"[INFO] family: {fam_registry.active_source()} / {len(fams)} family・{n_var} 通り（スキーム込み）")
    print(f"[INFO] {len(files)} files / 推定={PROP_MODE} (q={LOWER_Q}/{UPPER_Q}) / scipy={'あり' if has_scipy() else 'なし(正規近似)'}"
          f" / WEDGE: 年の最低件数={opts['min_year_count']} 遡る年数≦{opts['max_years']}"
          f" 減衰率={opts['decay']} 窓={opts['window']}")
    jobs = [(str(p), args.out_dir, opts, None, fam_dicts) for p in files]
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(_run, jobs, chunksize=8))
    else:
        results = [_run(j) for j in jobs]
    errors = [r for r in results if r.startswith("[ERROR]")]
    for r in results:
        if not r.startswith("[OK]"):
            print(r)
    _print_wedge_coverage(results, opts["max_years"])
    n_ok = sum(r.startswith("[OK]") for r in results)
    print(f"[DONE] OK={n_ok} SKIP={sum(r.startswith('[SKIP]') for r in results)} ERROR={len(errors)}")
    if n_ok == 0:
        print("[ERROR] 集計できた master がありません。replace_csv_ver3.py を先に実行してください", file=sys.stderr)
        return 2
    return 1 if errors else 0


def _print_wedge_coverage(results: list[str], max_years: int = DEFAULT_MAX_YEARS) -> None:
    """adjusted_WEDGE が付いたグループの割合と、年度窓の終わりの年（master ごと）の範囲を出す。

    年の最低件数の条件はキーの粒度が細かいと満たしにくい。また窓の終わりは master ごとの最終年なので、
    更新が止まった master が混ざると、その指数だけ古い年の adjusted_WEDGE になる。どちらも確認用。
    """
    import re
    total = adj = 0
    ends: dict[str, list[str]] = {}
    for r in results:
        for g, a in re.findall(r"=(\d+)\(adj (\d+)\)", r):
            total += int(g)
            adj += int(a)
        m = re.search(r"^\[OK\] (.+?): .*年度窓の終わり=(\S+)$", r)
        if m:
            ends.setdefault(m.group(2), []).append(m.group(1))
    if total:
        print(f"[INFO] adjusted_WEDGE が付いたグループ: {adj}/{total} ({adj / total:.1%})")
    if len(ends) > 1:
        detail = ", ".join(f"{y}: {len(v)}本" for y, v in sorted(ends.items()))
        print(f"[WARN] 年度窓の終わり（master の最終年）が master によって違います: {detail}。"
              "古い年で止まった master の adjusted_WEDGE はその年までの値です", file=sys.stderr)
    elif ends:
        print(f"[INFO] 年度窓の終わり: {next(iter(ends))}年（この年から遡る{max_years}年のうち、件数を満たす年をすべて使う）")


def _run(job) -> str:
    try:
        return aggregate_master(*job)
    except Exception as e:
        return f"[ERROR] {Path(job[0]).name}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    sys.exit(main())
