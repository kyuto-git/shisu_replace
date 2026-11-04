# -*- coding: utf-8 -*-
"""集計素材（foragg_csv/foragg_*.csv、1行=1頭）→ 結合データごとの成績表（agg_csv/ に同名で出力）。

素材は上書きしないので、集計条件を変えたときは aggregate → build_db だけ再実行すればよい。

出力列:
  結合データ, 件数, 勝率_val, 勝率, 連対率_val, 連対率, 複勝率_val, 複勝率, 2着率_val, 2着率,
  3着率_val, 3着率, 掲示板率_val, 掲示板率, 平均着順, 単勝回収値, 複勝回収値, 単勝適正回収値,
  提案勝率, 提案連対率, 提案複勝率, 提案2着率, 提案3着率, 場所, 芝・ダ, 距離, 芝(内・外)
  - 「勝率」などの文字列は '頻度%（推定%）'。*_val はこの CSV では頻度%。
    （build_db が DB に取り込むとき、5つの率の *_val は括弧内の推定値に置き換わる）
  - 推定値は shisu/estimates.py（既定: 事後分布の下側10%点、複勝率は頻度20%以下なら上側90%点）

環境変数: AGG_PROP_MODE(lower|mean), AGG_LOWER_Q(0.10), AGG_UPPER_Q(0.90), AGG_PRIOR_ALPHA/BETA(0.5),
          PLC_SWITCH_THRESHOLD_PCT(20), AGG_MIN_SAMPLE(5), AGG_RANKBAND_SCHEMES(例 "s1")
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
from shisu.naming import parse_foragg_stem

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

OUT_COLUMNS = [KEY, "件数", "勝率_val", "勝率", "連対率_val", "連対率", "複勝率_val", "複勝率",
               "2着率_val", "2着率", "3着率_val", "3着率", "掲示板率_val", "掲示板率",
               "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値",
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
    if PROP_MODE == "mean":
        return posterior_mean(k, n, PRIOR_A, PRIOR_B) * 100.0
    lo = beta_quantile(k, n, LOWER_Q, PRIOR_A, PRIOR_B) * 100.0
    if upper is None:
        return lo
    hi = beta_quantile(k, n, UPPER_Q, PRIOR_A, PRIOR_B) * 100.0
    return lo.where(~upper, hi)


def aggregate_frame(df: pd.DataFrame) -> pd.DataFrame:
    """素材 DataFrame（文字列）→ 成績表。"""
    for c in (RID, KEY, "着順"):
        if c not in df.columns:
            raise KeyError(c)
    df = df[df[KEY].astype(str) != ""]

    # 重複排除（1頭=1件）: レースID × 結合データ（× 順位帯）× 馬番（無ければ馬名）
    subset = [RID, KEY] + ([BAND] if BAND in df.columns else [])
    horse = next((c for c in ("馬番", "馬名") if c in df.columns and (df[c] != "").any()), None)
    if horse:
        subset.append(horse)
    df = df.drop_duplicates(subset=subset, keep="first")

    fl = parse_finish(df["着順"], df["頭数"] if "頭数" in df.columns else None)
    use = fl["denom"]
    d, f = df[use], fl[use]
    if d.empty:
        return pd.DataFrame(columns=OUT_COLUMNS)

    g = d[KEY]
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

    out = pd.DataFrame({KEY: n.index, "件数": n.values.astype(int)})
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
    for c in REF_COLUMNS:
        if c in d.columns:
            first = d[c].where(d[c] != "").groupby(g).first().reindex(n.index)
            out[c] = first.fillna("").values
        else:
            out[c] = ""
    out = out[out["件数"] >= MIN_SAMPLE].reset_index(drop=True)
    return out[OUT_COLUMNS]


def aggregate_file(src: str, out_dir: str) -> str:
    src = Path(src)
    if parse_foragg_stem(src.stem) is None:
        return f"[SKIP] {src.name}: ファイル名が規約外（旧仕様の odds5p など）"
    df = read_csv_any(src)
    try:
        out = aggregate_frame(df)
    except KeyError as e:
        return f"[SKIP] {src.name}: 列 {e} がありません（集計済みファイルの可能性）"
    write_csv(out, Path(out_dir) / src.name)
    return f"[OK] {src.name}: {len(out)} groups"


def _selected(path: Path, schemes: set[str]) -> bool:
    if not schemes:
        return True
    p = parse_foragg_stem(path.stem)
    return p is None or not p["scheme"] or p["scheme"] in schemes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", default=str(config.foragg_dir()))
    ap.add_argument("--out-dir", default=str(config.agg_dir()))
    ap.add_argument("--clean", action="store_true", help="出力先 agg_csv を空にしてから実行")
    ap.add_argument("--workers", type=int, default=1, help="並列数（既定1）")
    args = ap.parse_args(argv)

    files = sorted(Path(args.in_dir).glob("foragg_*.csv"))
    schemes = {s.strip() for s in os.getenv("AGG_RANKBAND_SCHEMES", "").split(",") if s.strip()}
    files = [p for p in files if _selected(p, schemes)]
    if not files:
        print(f"[INFO] 素材がありません: {args.in_dir}")
        return 0
    if args.clean and Path(args.out_dir).exists():
        shutil.rmtree(args.out_dir)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    print(f"[INFO] {len(files)} files / 推定={PROP_MODE} (q={LOWER_Q}/{UPPER_Q}) / scipy={'あり' if has_scipy() else 'なし(正規近似)'}")
    jobs = [(str(p), args.out_dir) for p in files]
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(_run, jobs, chunksize=8))
    else:
        results = [_run(j) for j in jobs]
    errors = [r for r in results if r.startswith("[ERROR]")]
    for r in results:
        if not r.startswith("[OK]"):
            print(r)
    print(f"[DONE] OK={sum(r.startswith('[OK]') for r in results)} SKIP={sum(r.startswith('[SKIP]') for r in results)} ERROR={len(errors)}")
    return 1 if errors else 0


def _run(job) -> str:
    try:
        return aggregate_file(*job)
    except Exception as e:
        return f"[ERROR] {Path(job[0]).name}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    sys.exit(main())
