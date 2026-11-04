# -*- coding: utf-8 -*-
"""過去レース CSV → 正規化・偏差値・結合データ → 集計素材（foragg_*）を出力する。

入力 : csv_files/{地域}_{指数名}.csv（TARGET「レース一覧★画面イメージ」、1行=1頭、1ファイル=1指数）
出力 : foragg_csv/foragg_{tag}_{元名}.csv   集計素材（1行=1頭×1キー）。aggregate_stats_ver2.py の入力
       replace_csv/processed_*.csv          結合データの確認用（TARGET 取込用。--no-processed で省略）
       master_csv/master_{元名}.csv          正規化済み 1頭1行（WEDGE・組合せ分析用。--no-master で省略）

tag = オッズ帯（なし/odds10u_/odds10_50_/odds50p_）× family（10種）
    + オッズ帯 × 順位帯スキーム（rankband_s1_/rankband_s2_）× band family（5種）
    → 入力1本あたり 80 本。行が0件でもヘッダのみで書き出す（古いファイルが残らないように）。

正規化・偏差値・キーの規則はすべて shisu パッケージにある（出馬表側と共通）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from shisu import config
from shisu.csvio import norm_series, read_csv_any, write_csv
from shisu.hensachi import compute_indicator, int_text_series
from shisu.indicators import parse_source_name, spec_for
from shisu.keys import BAND_FAMILIES, BASE_FAMILIES, SCHEMES, build_key_series
from shisu.naming import foragg_filename, foragg_tag
from shisu.normalize import (apply_age_class_rule, apply_distance_rules, class_series, distance_series,
                             frame_group_series, int_series, race_date, race_id_without_horse, race_year,
                             shinba_mask, weight_series)
from shisu.odds import ODDS_MODES, odds_bucket, odds_mask

MIN_HEADS = 10   # 頭数10頭以上のレースだけ使う（TARGET 側の抽出条件と同じ）

# 素材の列（aggregate_stats_ver2.py が読む）
FORAGG_COLUMNS = ["レースID(新)", "結合データ", "着順", "単勝オッズ", "単勝配当", "複勝配当",
                  "場所", "芝・ダ", "距離", "芝(内・外)", "馬番", "馬名", "頭数", "日付", "year"]
BAND_COL = "指数順位帯"
MASTER_COLUMNS = ["レースID(新)", "レースID(新/馬番無)", "日付", "year", "地域", "指数名",
                  "場所", "芝・ダ", "距離_raw", "距離", "芝(内・外)", "重量種別", "クラス名", "頭数",
                  "枠番", "枠番グループ", "馬番", "馬名", "騎手",
                  "指数", "指数順位_元", "順位", "偏差値", "偏差値帯", "順位帯_s1", "順位帯_s2", "指数有効",
                  "着順", "単勝オッズ", "オッズ帯", "単勝配当", "複勝配当"]


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series("", index=df.index, dtype=object)


def normalize_history(raw: pd.DataFrame) -> pd.DataFrame:
    """TARGET の列を正規化して、キーの部品と集計に使う列をそろえる。"""
    df = pd.DataFrame(index=raw.index)
    df["レースID(新)"] = norm_series(_col(raw, "レースID(新)"))
    df["レースID(新/馬番無)"] = race_id_without_horse(df["レースID(新)"], _col(raw, "レースID(新/馬番無)")
                                                      if "レースID(新/馬番無)" in raw.columns else None)
    df["日付"] = race_date(df["レースID(新)"])
    df["year"] = race_year(df["日付"])
    df["場所"] = norm_series(_col(raw, "場所"))
    df["芝・ダ"] = norm_series(_col(raw, "芝・ダ", "芝ダ"))
    df["距離_raw"] = norm_series(_col(raw, "距離"))
    inner = norm_series(_col(raw, "芝(内・外)", "芝(内外)"))
    dist, inner = apply_distance_rules(df["場所"], df["芝・ダ"], distance_series(_col(raw, "距離")), inner)
    df["距離"] = dist
    df["芝(内・外)"] = inner
    df["重量種別"] = weight_series(_col(raw, "重量種別"))
    df["クラス名_raw"] = norm_series(_col(raw, "クラス名", "クラス"))
    df["クラス名"] = apply_age_class_rule(class_series(_col(raw, "クラス名", "クラス")), df["重量種別"])
    heads = int_series(_col(raw, "頭数", "頭"))
    df["頭数_num"] = heads
    df["頭数"] = heads.astype(object).where(heads.notna(), "").map(lambda v: "" if v == "" else str(int(v)))
    df["枠番"] = norm_series(_col(raw, "枠番", "枠"))
    df["枠番グループ"] = frame_group_series(df["枠番"], df["頭数"])
    df["馬番"] = norm_series(_col(raw, "馬番"))
    df["馬名"] = norm_series(_col(raw, "馬名", "馬名S", "馬名1"))
    df["騎手"] = norm_series(_col(raw, "騎手"))
    df["指数"] = norm_series(_col(raw, "指数"))
    df["指数順位_元"] = norm_series(_col(raw, "指数順位"))
    for c in ("着順", "単勝オッズ", "単勝配当", "複勝配当"):
        df[c] = norm_series(_col(raw, c))
    return df


def _race_key(df: pd.DataFrame) -> pd.Series:
    rk = df["レースID(新/馬番無)"]
    if (rk == "").all():
        print("[WARN] レースIDが無いため全行を1レースとして偏差値を計算します", file=sys.stderr)
        return pd.Series("ALL", index=df.index)
    return rk


def process_file(path: str, foragg_dir: str, processed_dir: str | None, master_dir: str | None,
                 schemes: tuple[str, ...]) -> str:
    path = Path(path)
    base = path.name
    region, indicator = parse_source_name(path.stem)
    spec = spec_for(indicator)
    raw = read_csv_any(path)
    if "指数" not in raw.columns:
        return f"[SKIP] {base}: 指数 列がありません"
    df = normalize_history(raw)

    given_rank = df["指数順位_元"] if "指数順位" in raw.columns else None
    ind = compute_indicator(df["指数"], _race_key(df), spec, shinba_mask(df["クラス名_raw"]), given_rank)
    df["順位"] = int_text_series(ind["順位"])
    df["偏差値"] = int_text_series(ind["偏差値"])
    df["偏差値帯"] = ind["偏差値帯"]
    for s in SCHEMES:
        df[f"順位帯_{s}"] = ind[f"順位帯_{s}"]
    df["指数有効"] = ind["usable"].astype(int)

    heads_ok = (df["頭数_num"] >= MIN_HEADS).fillna(False).astype(bool)
    work = df[heads_ok & ~ind["race_excluded"] & ~ind["shinba_excluded"]]

    if master_dir:
        m = df[heads_ok].copy()
        m["地域"] = region
        m["指数名"] = indicator
        m["オッズ帯"] = odds_bucket(m["単勝オッズ"])
        write_csv(m[MASTER_COLUMNS], Path(master_dir) / f"master_{base}")

    n_files = 0
    for mode in ODDS_MODES:
        sub = work[odds_mask(work["単勝オッズ"], mode)]
        for fam in BASE_FAMILIES:
            _export(sub, fam, mode, "", base, foragg_dir, processed_dir)
            n_files += 1
        for scheme in schemes:
            for fam in BAND_FAMILIES:
                _export(sub, fam, mode, scheme, base, foragg_dir, processed_dir)
                n_files += 1
    if processed_dir:  # 代表（normal・絞り込みなし）
        key = build_key_series(work, "normal")
        rep = work.loc[key != "", ["レースID(新)"]].assign(結合データ=key[key != ""])
        write_csv(rep, Path(processed_dir) / f"processed_{base}")
    used = int(len(work))
    return (f"[OK] {base}: 指数={indicator} 地域={region} 行={len(df)} 使用={used} "
            f"(頭数<{MIN_HEADS}・除外レース・新馬除外を除く) → {n_files} files")


def _export(sub: pd.DataFrame, family: str, mode: str, scheme: str, base: str,
            foragg_dir: str, processed_dir: str | None) -> None:
    rband_col = f"順位帯_{scheme}" if scheme else None
    key = build_key_series(sub, family, rband_col)
    keep = key != ""
    out = sub.loc[keep].copy()
    out["結合データ"] = key[keep]
    cols = list(FORAGG_COLUMNS)
    if scheme:
        out[BAND_COL] = sub.loc[keep, rband_col]
        cols.insert(2, BAND_COL)
    write_csv(out[cols], Path(foragg_dir) / foragg_filename(family, mode, scheme, base))
    if processed_dir:
        pcols = ["レースID(新)", "結合データ"] + ([BAND_COL] if scheme else [])
        write_csv(out[pcols], Path(processed_dir) / f"processed_{foragg_tag(family, mode, scheme)}_{base}")


def _active_schemes() -> tuple[str, ...]:
    env = os.environ.get("RANKBAND_SCHEMES", ",".join(SCHEMES))
    picked = tuple(s.strip() for s in env.split(",") if s.strip() in SCHEMES)
    return picked or SCHEMES


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv-dir", default=str(config.csv_dir()))
    ap.add_argument("--foragg-dir", default=str(config.foragg_dir()))
    ap.add_argument("--processed-dir", default=str(config.processed_dir()))
    ap.add_argument("--master-dir", default=str(config.master_dir()))
    ap.add_argument("--no-processed", action="store_true", help="processed_* を出力しない")
    ap.add_argument("--no-master", action="store_true", help="master_* を出力しない")
    ap.add_argument("--clean", action="store_true", help="出力先（foragg/processed/master）を空にしてから実行")
    ap.add_argument("--workers", type=int, default=1, help="並列数（既定1。メモリに余裕があれば 2〜4）")
    args = ap.parse_args(argv)

    files = sorted(p for p in Path(args.csv_dir).glob("*.csv"))
    if not files:
        print(f"[INFO] 入力CSVがありません: {args.csv_dir}")
        return 0
    processed_dir = None if args.no_processed else args.processed_dir
    master_dir = None if args.no_master else args.master_dir
    if args.clean:
        for d in (args.foragg_dir, processed_dir, master_dir):
            if d and Path(d).exists():
                shutil.rmtree(d)
    for d in (args.foragg_dir, processed_dir, master_dir):
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)

    schemes = _active_schemes()
    jobs = [(str(p), args.foragg_dir, processed_dir, master_dir, schemes) for p in files]
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(_run, jobs))
    else:
        results = [_run(j) for j in jobs]
    for r in results:
        print(r)
    return 1 if any(r.startswith("[ERROR]") for r in results) else 0


def _run(job) -> str:
    try:
        return process_file(*job)
    except Exception as e:  # 1ファイルの失敗で全体を止めない
        return f"[ERROR] {Path(job[0]).name}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    sys.exit(main())
