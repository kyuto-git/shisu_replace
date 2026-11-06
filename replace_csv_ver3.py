# -*- coding: utf-8 -*-
"""過去レース CSV → 正規化・偏差値・条件（22種）→ master（1頭1行。aggregate_stats_ver2.py の入力）。

入力（csv_files/）:
  (a) 基本CSV（新方式）: {地域}_タイムM.csv / {地域}_対戦M.csv … TARGET レース検索 ★6タブの「レース一覧★画面イメージ」。
      指数 列にマイニング（タイムM）か対戦型マイニング（対戦M）。単勝配当・複勝配当つき（無くても動く。回収値は0）
      距離 は「ダ1200」の形でもよい（芝・ダ と 距離 に分ける）。障害（トラックコード51〜・芝ダ=障）は除く
  (b) 従来の {地域}_{指数名}.csv（★8タブで1指数ずつ出力したもの）もそのまま読める
  (c) ext_indices/（外部指数CSV群。shisu/ext_indices.py）があれば、地域ごとの基本CSV（タイムM を優先、無ければ 対戦M）の
      各馬に外部指数を レースID(新)・レースID(旧) で結び付け、「{地域}_{指数名}」の入力があるものとして処理する
      （csv_files に同じ地域・指数のファイルがあれば、そちらを使う）
  廃止した指数（カツラキ・HPRTV5C・成績ST指数・一隊・33rap・上がり1F/2F）は読まない

出力:
  master_csv/master_{地域}_{指数名}.csv.gz  1頭1行（頭数10以上）。キーの部品・22条件・偏差値・順位・成績の列
  master_csv/__inner_outer__.csv.gz         レースID(新)先頭16桁 → 芝(内・外)（前走の内外を引くため。出馬表側も使う）
  replace_csv/processed_{元名}.csv          確認用（--processed のときだけ。normal family のキー）
集計（どの family のキーで数えるか）は aggregate_stats_ver2.py が master から行う（family ごとの素材は作らない）。
"""
from __future__ import annotations

import argparse
import functools
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from shisu import config
from shisu.conditions import INNER_FILE, CONDITION_COLUMNS, derive, inner_lookup_from, standardize_history
from shisu.csvio import norm_series, read_csv_any, write_csv
from shisu.families import build_keys, get as get_family
from shisu.hensachi import RANK_SCHEMES, compute_indicator, int_text_series
from shisu.indicators import canonical_indicator, is_discontinued, parse_source_name, spec_for
from shisu.normalize import (apply_age_class_rule, apply_distance_rules, class_series, distance_series,
                             frame_group_series, horse_race_id, int_series, race_date, race_id_without_horse, race_year,
                             shinba_mask, split_surface_distance, weight_series)
from shisu.odds import odds_bucket

MIN_HEADS = 10   # 頭数10頭以上のレースだけ使う（TARGET 側の抽出条件と同じ）
BASE_INDICATORS = ("タイムM", "対戦M")          # 基本CSV（ext_indices を結び付ける元）になる指数。左ほど優先
MASTER_COLUMNS = (["レースID(新)", "レースID(新/馬番無)", "日付", "year", "地域", "指数名",
                   "場所", "芝・ダ", "距離_raw", "距離", "芝(内・外)", "重量種別", "クラス名", "頭数",
                   "枠番", "枠番グループ", "馬番", "馬名", "騎手",
                   "指数", "指数順位_元", "順位", "偏差値", "偏差値帯"] + [f"順位帯_{s}" for s in RANK_SCHEMES]
                  + ["指数有効", "着順", "単勝オッズ", "オッズ帯", "単勝配当", "複勝配当"] + list(CONDITION_COLUMNS))


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series("", index=df.index, dtype=object)


def normalize_history(raw: pd.DataFrame) -> pd.DataFrame:
    """TARGET の列を正規化して、キーの部品と集計に使う列をそろえる。"""
    df = pd.DataFrame(index=raw.index)
    rid = norm_series(_col(raw, "レースID(新)"))
    # レース単位（16桁）のIDは馬番を付けて馬単位（18桁）にする（1頭=1行を保つ。horse_race_id の説明）
    df["レースID(新)"] = horse_race_id(rid, _col(raw, "馬番"))
    df.attrs["race_level_ids"] = int((df["レースID(新)"] != rid).sum())
    df.attrs["race_level_unfixed"] = int((rid.str.fullmatch(r"\d{16}") & (df["レースID(新)"] == rid)).sum())
    df["レースID(新/馬番無)"] = race_id_without_horse(rid, _col(raw, "レースID(新/馬番無)")
                                                      if "レースID(新/馬番無)" in raw.columns else None)
    df["レースID(旧)"] = norm_series(_col(raw, "レースID(旧)"))
    date = race_date(df["レースID(新)"])
    if "日付S" in raw.columns:
        ds = norm_series(raw["日付S"]).str.replace(r"\D", "", regex=True).str.slice(0, 8)
        date = date.where(date != "", ds.where(ds.str.fullmatch(r"(19|20)\d{6}"), ""))
    df["日付"] = date
    df["year"] = race_year(df["日付"])
    df["場所"] = norm_series(_col(raw, "場所"))
    surface, dist_raw = split_surface_distance(_col(raw, "芝・ダ", "芝ダ"), _col(raw, "距離"))
    df["芝・ダ"] = surface
    dist_num = distance_series(dist_raw)
    df["距離_raw"] = dist_num.astype(object).where(dist_num.notna(), "").map(lambda v: "" if v == "" else str(int(v)))
    inner = norm_series(_col(raw, "芝(内・外)", "芝(内外)"))
    dist, inner = apply_distance_rules(df["場所"], df["芝・ダ"], dist_num, inner)
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
    track = pd.to_numeric(norm_series(_col(raw, "トラックコード(JV)")), errors="coerce")
    df["障害"] = (df["芝・ダ"] == "障") | (track >= 51).fillna(False)
    return df


def _race_key(df: pd.DataFrame) -> pd.Series:
    rk = df["レースID(新/馬番無)"]
    if (rk == "").all():
        print("[WARN] レースIDが無いため全行を1レースとして偏差値を計算します", file=sys.stderr)
        return pd.Series("ALL", index=df.index)
    return rk


def process_frame(raw: pd.DataFrame, stem: str, region: str, indicator: str, master_dir: str,
                  processed_dir: str | None, inner_lookup: dict | None = None, given_rank: bool | None = None,
                  label: str = "") -> str:
    """1地域×1指数の入力（1行=1頭）→ master_{stem}.csv.gz。戻り値はログの1行。"""
    label = label or f"{stem}.csv"
    spec = spec_for(indicator)
    if "指数" not in raw.columns:
        return f"[SKIP] {label}: 指数 列がありません"
    df = normalize_history(raw)
    notes = []
    if df.attrs.get("race_level_ids"):
        notes.append(f"レース単位のレースID {df.attrs['race_level_ids']}行に馬番を付けて18桁にしました")
    if df.attrs.get("race_level_unfixed"):
        print(f"[WARN] {label}: レースID(新) がレース単位（16桁）で馬番が読めない行が {df.attrs['race_level_unfixed']} 行あります。"
              "1頭を識別できないので、TARGET の出力に 馬番 列を入れてください", file=sys.stderr)
    jumps = int(df["障害"].sum())
    if jumps:
        notes.append(f"障害 {jumps} 行を除外")
        keep = ~df["障害"]
        raw, df = raw[keep], df[keep]

    use_rank = ("指数順位" in raw.columns) if given_rank is None else given_rank
    ind = compute_indicator(df["指数"], _race_key(df), spec, shinba_mask(df["クラス名_raw"]),
                            df["指数順位_元"] if use_rank else None)
    df["順位"] = int_text_series(ind["順位"])
    df["偏差値"] = int_text_series(ind["偏差値"])
    df["偏差値帯"] = ind["偏差値帯"]
    for s in RANK_SCHEMES:
        df[f"順位帯_{s}"] = ind[f"順位帯_{s}"]
    df["指数有効"] = ind["usable"].astype(int)

    if "頭数" in raw.columns or "頭" in raw.columns:
        heads_ok = (df["頭数_num"] >= MIN_HEADS).fillna(False).astype(bool)
    else:  # 旧版と同じく、頭数列が無ければ絞らない（TARGET 側で10頭以上に抽出済みの前提）
        print(f"[WARN] {label}: 頭数 列が無いため頭数{MIN_HEADS}以上の絞り込みをしません", file=sys.stderr)
        heads_ok = pd.Series(True, index=df.index)

    cond = derive(standardize_history(raw, df), inner_lookup)
    for c in CONDITION_COLUMNS:
        df[c] = cond[c]
    m = df[heads_ok].copy()
    m["地域"] = region
    m["指数名"] = indicator
    m["オッズ帯"] = odds_bucket(m["単勝オッズ"])
    write_csv(m[MASTER_COLUMNS], Path(master_dir) / f"master_{stem}.csv.gz")
    if processed_dir:
        work = m[m["指数有効"] == 1]
        key = build_keys(work, get_family("normal"), "")
        rep = pd.DataFrame({"レースID(新)": work.loc[key.index, "レースID(新)"].to_numpy(), "結合データ": key.to_numpy()})
        write_csv(rep, Path(processed_dir) / f"processed_{stem}.csv")
    n_use = int((m["指数有効"] == 1).sum())
    if len(m) == 0:
        print(f"[WARN] {label}: 使える行が0件です（列名・頭数・指数の欠損を確認してください）", file=sys.stderr)
    return (f"[OK] {label}: 指数={indicator} 地域={region} 行={len(raw)} master={len(m)} 指数あり={n_use}"
            f"（頭数<{MIN_HEADS}・障害を除く）" + (f" / {'、'.join(notes)}" if notes else ""))


def process_file(path: str, master_dir: str, processed_dir: str | None, inner_lookup: dict | None = None) -> str:
    path = Path(path)
    region, indicator = parse_source_name(path.stem)
    if is_discontinued(indicator):
        return f"[SKIP] {path.name}: {indicator} は廃止した指数なので読みません"
    return process_frame(read_csv_any(path), path.stem, region, indicator, master_dir, processed_dir,
                         inner_lookup, label=path.name)


@functools.lru_cache(maxsize=2)
def _read_base(path: str) -> pd.DataFrame:
    return read_csv_any(path)


def process_ext(base_path: str, indicator: str, master_dir: str, processed_dir: str | None,
                inner_lookup: dict | None, ext_root: str) -> str:
    """基本CSV（地域ごと）の各馬に ext_indices の指数を結び付けて、{地域}_{指数名} として処理する。"""
    from shisu import ext_indices
    base_path = Path(base_path)
    region_raw = base_path.stem.rsplit("_", 1)[0] if "_" in base_path.stem else base_path.stem
    region, _ = parse_source_name(base_path.stem)
    sources, _unknown = ext_indices.discover(ext_root, log=lambda m: None)
    src = sources.get(indicator)
    if src is None:
        return f"[SKIP] ext_indices に {indicator} がありません"
    lookup = ext_indices.load_source(src, log=lambda m: None)
    raw = _read_base(str(base_path)).copy()
    values, ranks = ext_indices.attach(raw, lookup)
    raw["指数"] = values
    if ranks is not None:
        raw["指数順位"] = ranks
    elif "指数順位" in raw.columns:
        raw = raw.drop(columns=["指数順位"])               # 基本CSVの順位（タイムM など）を使わない
    hit = int((values != "").sum())
    stem = f"{region_raw}_{indicator}"
    msg = process_frame(raw, stem, region, indicator, master_dir, processed_dir, inner_lookup,
                        given_rank=ranks is not None, label=f"{base_path.name} + ext_indices/{indicator}")
    return msg + f" / 外部指数が付いた馬 {hit}/{len(raw)}"


def collect_inner_lookup(paths: list[Path]) -> dict[str, str]:
    """前走の内外を引く表（レースID(新)先頭16桁 → 芝(内・外)）。地域ごとに1本読めば足りる。"""
    out: dict[str, str] = {}
    for p in paths:
        try:
            raw = read_csv_any(p)
        except Exception:
            continue
        if "レースID(新)" not in raw.columns:
            continue
        out.update(inner_lookup_from(norm_series(raw["レースID(新)"]), norm_series(_col(raw, "芝(内・外)", "芝(内外)"))))
    return out


def plan_jobs(files: list[Path], ext_root: Path | None, log=print) -> tuple[list[Path], list[tuple[Path, str]], list[str]]:
    """(そのまま処理するファイル, (基本CSV, 外部指数) の組, ログ)。"""
    notes: list[str] = []
    direct: list[Path] = []
    present: set[tuple[str, str]] = set()
    bases: dict[str, tuple[int, Path]] = {}
    for p in files:
        region, indicator = parse_source_name(p.stem)
        if is_discontinued(indicator):
            notes.append(f"[SKIP] {p.name}: {indicator} は廃止した指数なので読みません")
            continue
        direct.append(p)
        present.add((region, canonical_indicator(indicator)))
        if indicator in BASE_INDICATORS:
            prio = BASE_INDICATORS.index(indicator)
            if region not in bases or prio < bases[region][0]:
                bases[region] = (prio, p)
    pairs: list[tuple[Path, str]] = []
    if ext_root is not None and Path(ext_root).is_dir():
        from shisu import ext_indices
        sources, unknown = ext_indices.discover(ext_root, log=log)
        for path, why in unknown:
            notes.append(f"[INFO] ext_indices: 使わないファイル {path}（{why}）")
        if sources and not bases:
            notes.append("[WARN] ext_indices はありますが、結び付ける基本CSV（{地域}_タイムM.csv / {地域}_対戦M.csv）が csv_files にありません")
        for region, (_prio, base) in sorted(bases.items()):
            for ind in sorted(sources):
                if is_discontinued(ind):
                    continue
                if (region, ind) in present:
                    notes.append(f"[INFO] {region}_{ind}: csv_files のファイルを使います（ext_indices は使いません）")
                    continue
                pairs.append((base, ind))
    return direct, pairs, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv-dir", default=str(config.csv_dir()))
    ap.add_argument("--ext-dir", default=str(config.ext_dir()), help="外部指数CSV群のフォルダ（既定 ext_indices）")
    ap.add_argument("--no-ext", action="store_true", help="ext_indices を使わない")
    ap.add_argument("--master-dir", default=str(config.master_dir()))
    ap.add_argument("--processed-dir", default=str(config.processed_dir()))
    ap.add_argument("--processed", action="store_true", help="processed_*（normal family のキーの確認用）も出力する")
    ap.add_argument("--clean", action="store_true", help="出力先（master と、--processed なら processed）を空にしてから実行")
    ap.add_argument("--workers", type=int, default=1, help="並列数（既定1。メモリに余裕があれば 2〜4）")
    args = ap.parse_args(argv)

    files = sorted(p for p in Path(args.csv_dir).glob("*.csv"))
    if not files:
        print(f"[INFO] 入力CSVがありません: {args.csv_dir}")
        return 0
    processed_dir = args.processed_dir if args.processed else None
    if args.clean:
        for d in (args.master_dir, processed_dir):
            if d and Path(d).exists():
                shutil.rmtree(d)
    for d in (args.master_dir, processed_dir):
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)

    ext_root = None if args.no_ext else Path(args.ext_dir)
    direct, pairs, notes = plan_jobs(files, ext_root)
    for n in notes:
        print(n)
    # 前走の内外を引く表: 地域ごとの基本CSV（無ければ最初のファイル）から
    by_region: dict[str, Path] = {}
    for p in direct:
        region, indicator = parse_source_name(p.stem)
        if region not in by_region or indicator in BASE_INDICATORS:
            by_region[region] = p
    inner = collect_inner_lookup(list(by_region.values()))
    write_csv(pd.DataFrame({"レースID16": list(inner), "芝(内・外)": list(inner.values())}),
              Path(args.master_dir) / INNER_FILE)

    jobs = [("file", str(p), args.master_dir, processed_dir, inner) for p in direct]
    jobs += [("ext", str(b), ind, args.master_dir, processed_dir, inner, str(ext_root)) for b, ind in pairs]
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(_run, jobs))
    else:
        results = [_run(j) for j in jobs]
    for r in results:
        print(r)
    return 1 if any(r.startswith("[ERROR]") for r in results) else 0


def _run(job) -> str:
    kind, rest = job[0], job[1:]
    try:
        return process_file(*rest) if kind == "file" else process_ext(*rest)
    except Exception as e:  # 1ファイルの失敗で全体を止めない
        return f"[ERROR] {Path(rest[0]).name}{'' if kind == 'file' else ' + ' + rest[1]}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    sys.exit(main())
