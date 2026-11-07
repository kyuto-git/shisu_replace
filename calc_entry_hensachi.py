# -*- coding: utf-8 -*-
"""出馬表 CSV（entries_in/）に、指数ごとの偏差値・順位・帯を付けて entries_out/ に書き出す。

各指数列 X（正式名は shisu/indicators.py の台帳で決まる。例: GoldAirplane→Gold, マイニング値→タイムM,
先行力→先行）の直後に次の列を挿入する:
  X_偏差値, X_偏差値帯, X_順位, X_順位帯_s1, X_順位帯_s2
計算は過去側（replace_csv_ver3.py）と同じ shisu.hensachi.compute_indicator。
  - 同一レースで欠損2頭以上・全頭同値（全頭0の未取得など）・有効2頭未満 → そのレースは空欄
  - ZI は 0 を同レース非ゼロ平均で埋める、ZI・吉馬6種は新馬戦を空欄、小さいほど良い指数は反転
順位は元ファイルの順位列（マイニング順位 / 指数順位N など）を優先し、無ければ再計算する。

  python calc_entry_hensachi.py               # 個別出力 {元名}_hensachi.csv
  python calc_entry_hensachi.py --merge-one   # 個別＋統合 entries_merged_hensachi.csv
  python calc_entry_hensachi.py --merge-only  # 統合のみ（推奨。DB の xjoin が1本で済む）
  --clean を付けると entries_out の古い CSV を消してから出力する
統合は レースID(新) で全ファイルを横に結合する（空欄は後続ファイルの値で埋める。行は和集合）。

外部指数の自動結合（ext_indices/。過去側の replace_csv_ver3.py と同じ shisu.ext_indices.attach_filled）:
  entries_in/ に置く出馬表CSVは マイニング値・対戦型マイニング値 だけの1本（test.csv の形）でよい。
  ext_indices/ の指数（形式A〜D）を レースID(新)・レースID(旧) で各馬に結び付けてから偏差値・順位を計算する。
  出馬表CSVの値を優先する（レースごと: そのレースに 0・空欄以外の値が1頭でもあれば出馬表の値と TARGET の順位、
  列が無い・0・空欄だけのレースは ext_indices の値で埋める）。
  ファイルに無い馬は値 0・最下位（過去側と同じ）。--no-ext で結合しない。--ext-dir で場所を変える
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable

import pandas as pd

from shisu import config
from shisu.csvio import norm_series, read_csv_any, write_csv
from shisu.hensachi import RANK_SCHEMES, compute_indicator, int_text_series
from shisu.indicators import canonical_indicator, is_known_indicator, spec_for
from shisu.normalize import race_id_without_horse, shinba_mask

DEFAULT_MERGED_NAME = "entries_merged_hensachi.csv"
DERIVED_SUFFIXES = ("_偏差値", "_偏差値帯", "_順位") + tuple(f"_順位帯_{s}" for s in RANK_SCHEMES)

# 元ファイルの順位列（明示）: 正式名 → 列名候補
EXPLICIT_RANK_COLUMNS = {"タイムM": ("マイニング順位",), "対戦M": ("対戦型マイニング順位",)}
_NUMBERED_RANK = re.compile(r"^指数順位([0-9０-９]+)$")
_UNNAMED_SLOT = re.compile(r"^指数[0-9０-９]+$")   # 指数を読み込んでいない枠の見出し


def find_indicator_columns(df: pd.DataFrame) -> list[str]:
    """台帳にある指数の列（列順）。派生列（X_偏差値 など）や順位列は除く。"""
    out = []
    for c in df.columns:
        if any(str(c).endswith(s) for s in DERIVED_SUFFIXES):
            continue
        if is_known_indicator(c):
            out.append(c)
    return out


def build_rank_source_map(df: pd.DataFrame, idx_cols: list[str], ext_cols: Iterable[str] = ()) -> dict[str, str]:
    """指数列 → 元順位の列名。ext_cols は ext_indices から末尾に足した指数列（2〜4 の対応付けに使わない。
    TARGET が出した 指数順位N は出馬表にもとからある指数の順位なので、足した列に回さない）。

    1) 明示列（マイニング順位 / {列}順位 / {正式名}順位 / {正式名}_順位 / {列}_順位）
    2) 「指数順位N」が指数列のすぐ右にあれば、その指数列の順位（TARGET の出力は 指数列, 指数順位N の並び）
    3) 2 で決まらないものは、N 番目の「枠」に対応させる。枠 = 台帳にある指数列（1 で専用の順位列が
       見つかったものを除く）と、指数を読み込んでいない枠の見出し「指数N」を出現順に並べたもの
    4) 指数が1種類で無印「指数順位」があれば、それを使う
    どれにも当たらなければ未対応（順位は指数から計算し直す）。
    """
    cols = [str(c) for c in df.columns]
    rank_map: dict[str, str] = {}
    for col in idx_cols:
        canon = canonical_indicator(col)
        cands = list(EXPLICIT_RANK_COLUMNS.get(canon, ())) + [f"{col}順位", f"{canon}順位", f"{canon}_順位", f"{col}_順位"]
        hit = next((c for c in cands if c in df.columns), None)
        if hit:
            rank_map[col] = hit
    explicit = set(rank_map)
    used = set(rank_map.values())
    ext_cols = set(ext_cols)
    own = [c for c in idx_cols if c not in ext_cols]
    for i, c in enumerate(cols):
        if _NUMBERED_RANK.match(c) and c not in used and i > 0:
            left = cols[i - 1]
            if left in own and left not in rank_map:
                rank_map[left] = c
                used.add(c)
    slots = [c for c in cols if (c in own and c not in explicit) or _UNNAMED_SLOT.match(c)]
    for c in cols:
        m = _NUMBERED_RANK.match(c)
        if not m or c in used:
            continue
        n = int(unicodedata.normalize("NFKC", m.group(1)))
        if 1 <= n <= len(slots):
            slot = slots[n - 1]
            if slot in idx_cols and slot not in rank_map:
                rank_map[slot] = c
                used.add(c)
    still = [c for c in own if c not in rank_map]
    if "指数順位" in df.columns and len(own) == 1 and still:
        rank_map[still[0]] = "指数順位"
    return rank_map


def race_key(df: pd.DataFrame) -> pd.Series:
    if "レースID(新)" in df.columns or "レースID(新/馬番無)" in df.columns:
        rid = df.get("レースID(新)", pd.Series("", index=df.index))
        given = df["レースID(新/馬番無)"] if "レースID(新/馬番無)" in df.columns else None
        rk = race_id_without_horse(rid, given)
        if (rk != "").any():
            return rk
    if "場 R" in df.columns:
        return norm_series(df["場 R"])
    raise RuntimeError("レースID列が見つかりません（レースID(新) / レースID(新/馬番無) / 場 R）")


def process_df(df: pd.DataFrame, name: str = "(df)", verbose: bool = True,
               last_masks: dict[str, pd.Series] | None = None, ext_cols: Iterable[str] = ()) -> pd.DataFrame:
    """last_masks: 指数列 → 値から順位を付けるとき最下位にする馬（ext_indices のファイルに無かった馬）。
    ext_cols: attach_ext が末尾に足した指数列（build_rank_source_map を参照）。"""
    df = df.copy()
    last_masks = last_masks or {}
    rk = race_key(df)
    cls = df["クラス名"] if "クラス名" in df.columns else df.get("クラス", pd.Series("", index=df.index))
    shin = shinba_mask(cls)
    idx_cols = find_indicator_columns(df)
    if not idx_cols and verbose:
        print(f"[WARN] {name}: 指数列が見つかりません")
    rank_map = build_rank_source_map(df, idx_cols, ext_cols)
    seen: set[str] = set()
    for col in idx_cols:
        canon = canonical_indicator(col)
        if canon in seen:
            print(f"[WARN] {name}: 指数 {canon} の列が複数あります（{col} は無視）", file=sys.stderr)
            continue
        seen.add(canon)
        given = df[rank_map[col]] if col in rank_map else None
        if given is None and verbose:
            print(f"[INFO] {name}: {canon} の順位列が無いため、指数から順位を計算します")
        r = compute_indicator(df[col], rk, spec_for(canon), shin, given, last_masks.get(col))
        if verbose and r.attrs.get("direction") == "rank" and r.attrs.get("reverse") != spec_for(canon).reverse:
            print(f"[INFO] {name}: {canon} は TARGET の順位に合わせて偏差値の向きを「"
                  + ("小さいほど上" if r.attrs["reverse"] else "大きいほど上") + "」にしました（台帳の既定と逆）")
        derived = {
            f"{canon}_偏差値": int_text_series(r["偏差値"]),
            f"{canon}_偏差値帯": r["偏差値帯"].fillna(""),
            f"{canon}_順位": int_text_series(r["順位"]),
        }
        for s in RANK_SCHEMES:
            derived[f"{canon}_順位帯_{s}"] = r[f"順位帯_{s}"].fillna("")
        for c in derived:
            if c in df.columns:
                df.drop(columns=[c], inplace=True)
        pos = df.columns.get_loc(col) + 1
        for i, (c, s) in enumerate(derived.items()):
            df.insert(pos + i, c, s)
    return df


def load_ext(ext_root: Path, skip: Iterable[str] = (), log=print) -> dict:
    """ext_indices/ の指数（skip にあるもの・廃止したものを除く）→ {正式名: ExtLookup}。
    出馬表に値のある指数かどうかは attach_ext がファイル・レースごとに決める（ここでは読み込むだけ）。"""
    skip = set(skip)
    from shisu import ext_indices
    from shisu.indicators import is_discontinued
    if not Path(ext_root).is_dir():
        return {}
    sources, _skipped = ext_indices.discover(ext_root, log=lambda m: None)
    out = {}
    for name, src in sources.items():
        if name in skip:
            continue
        if is_discontinued(name):
            continue
        out[name] = ext_indices.load_source(src, log=lambda m: None)
    return out


def _nonzero(s: pd.Series) -> pd.Series:
    v = pd.to_numeric(norm_series(s), errors="coerce")
    return v.notna() & (v != 0)


def attach_ext(df: pd.DataFrame, lookups: dict, name: str = "(df)",
               log=print) -> tuple[pd.DataFrame, dict, set[str]]:
    """出馬表1本に ext_indices の指数を付ける。戻り値: (DataFrame, 指数列 → ファイルに無かった馬, 末尾に足した指数列)。

    出馬表の値を優先する。判定はレースごと: 出馬表に同じ指数の列があり、そのレースに 0・空欄以外の値が1頭でもあれば
    そのレースは出馬表の値（と TARGET の順位）を使う。列が無い・そのレースが 0・空欄だけ（TARGET が読み込んでいない）なら
    ext の値で埋める。列があればその列（と対応する順位の列）の該当レースの行を書き換え（列を消すと 指数順位N の対応が
    ずれるため）、無ければ末尾に「{正式名}」と、順位があれば「{正式名}順位」を足す。"""
    from shisu import ext_indices
    if not lookups:
        return df, {}, set()
    df = df.copy()
    idx_cols = find_indicator_columns(df)
    rank_map = build_rank_source_map(df, idx_cols)
    masks: dict[str, pd.Series] = {}
    added: set[str] = set()
    rk = None
    for canon, lookup in lookups.items():
        try:
            att = ext_indices.attach_filled(df, lookup)
        except KeyError as e:
            log(f"[WARN] {name}: 外部指数を結び付けられません（{e}）")
            return df, masks, added
        existing = [c for c in idx_cols if canonical_indicator(c) == canon]
        note = ""
        if existing:
            col = existing[0]
            if rk is None:
                rk = race_key(df)
            has = _nonzero(df[col]).groupby(rk).transform("any").astype(bool)
            fill = ~has
            n_races = rk.nunique()
            kept = rk[has].nunique()
            if not fill.any():
                log(f"[INFO] {name}: {canon}: 出馬表CSVの値を使います（ext_indices は使いません）")
                continue
            df[col] = df[col].astype(object)
            df.loc[fill, col] = att.values[fill]
            if col in rank_map:
                rc = rank_map[col]
                df[rc] = df[rc].astype(object)
                df.loc[fill, rc] = att.ranks[fill] if att.ranks is not None else ""
            elif att.ranks is not None:
                rc = f"{col}順位"
                df[rc] = att.ranks.where(fill, "")
            masks[col] = att.absent & fill
            hit = int((~att.absent & fill).sum())
            total = int(fill.sum())
            if kept:
                note = f"（出馬表に値のあるレース {kept}/{n_races} は出馬表の値を使用）"
            norank = 0
            if att.ranks is not None and not att.rank_column_empty:     # 順位の無いレースは ext で埋めたレースだけ数える
                norank = int(rk[fill & ~att.absent & (norm_series(att.ranks) == "")].nunique())
        else:
            col = canon
            df[col] = att.values
            if att.ranks is not None:
                df[f"{col}順位"] = att.ranks
            masks[col] = att.absent
            added.add(col)
            hit, total = att.hit, len(df)
            norank = att.norank_races
        if att.rank_column_empty:
            note += "（順位の列が空なので値から順位を計算）"
        elif norank:
            note += f"（順位の無いレース {norank} 件は値から順位を計算）"
        log(f"[INFO] {name}: 外部指数 {canon} を ext_indices から付けました（値のある馬 {hit}/{total}）{note}")
    return df, masks, added


def merge_frames(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    """レースID(新)（無ければ レースID(新/馬番無)+馬番 → 場 R+馬番）で横結合。

    先に出てきた値を優先し、空欄（"" / NaN）だけ後続の値で埋める。行は全ファイルの和集合。
    （旧版は NaN しか埋めず、帯の "" が埋まらなかった）
    """
    key = _merge_key_name(dfs[0])
    order: list[str] = []
    merged = None
    for df in dfs:
        d = df.copy()
        if key not in d.columns:
            raise RuntimeError(f"統合キー {key} が無いファイルがあります")
        d[key] = norm_series(d[key])
        if d[key].duplicated().any():
            raise RuntimeError(f"統合キー {key} が重複しています")
        for c in d.columns:
            if c not in order:
                order.append(c)
        d = d.set_index(key).replace({"": pd.NA})
        merged = d if merged is None else merged.combine_first(d)
    merged = merged.reset_index()
    merged = merged[[c for c in order if c in merged.columns]]
    return merged.astype(object).where(merged.notna(), "")


def _merge_key_name(df: pd.DataFrame) -> str:
    for k in ("レースID(新)", "レースID(新/馬番無)", "場 R"):
        if k in df.columns:
            return k
    raise RuntimeError("統合キー列が見つかりません")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", "--in_dir", dest="in_dir", default=str(config.entries_in_dir()))
    ap.add_argument("--out-dir", "--out_dir", dest="out_dir", default=str(config.entries_out_dir()))
    ap.add_argument("--merge-one", action="store_true", help="個別出力に加えて統合1ファイルも出力")
    ap.add_argument("--merge-only", action="store_true", help="統合1ファイルだけ出力")
    ap.add_argument("--merge-name", default=DEFAULT_MERGED_NAME)
    ap.add_argument("--clean", action="store_true", help="出力先 entries_out の CSV を消してから実行（古い出馬表を DB に入れないため）")
    ap.add_argument("--ext-dir", default=str(config.ext_dir()), help="外部指数CSV群のフォルダ（既定 ext_indices）")
    ap.add_argument("--no-ext", action="store_true", help="ext_indices の外部指数を結び付けない")
    args = ap.parse_args(argv)

    files = sorted(Path(args.in_dir).glob("*.csv"))
    if not files:
        print(f"[INFO] 入力CSVが見つかりません: {args.in_dir}")
        return 0
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for old in out_dir.glob("*.csv"):
            old.unlink()

    processed, rc = [], 0
    raws: dict[Path, pd.DataFrame] = {}
    for p in files:
        try:
            raws[p] = read_csv_any(p)
        except Exception as e:
            print(f"[ERROR] {p.name}: {type(e).__name__}: {e}")
            rc = 1
    lookups = {} if args.no_ext else load_ext(Path(args.ext_dir))
    for p, raw in raws.items():
        try:
            raw, masks, added = attach_ext(raw, lookups, p.name)
            df = process_df(raw, p.name, last_masks=masks, ext_cols=added)
        except Exception as e:
            print(f"[ERROR] {p.name}: {type(e).__name__}: {e}")
            rc = 1
            continue
        processed.append(df)
        if not args.merge_only:
            out = out_dir / f"{p.stem}_hensachi.csv"
            write_csv(df, out)
            print(f"[OK] {p.name} -> {out.name} ({len(df)} rows)")
    if (args.merge_one or args.merge_only) and processed:
        try:
            merged = merge_frames(processed)
        except Exception as e:
            print(f"[ERROR] merge: {e}")
            return 1
        out = out_dir / args.merge_name
        write_csv(merged, out)
        print(f"[OK] merged -> {out.name} ({len(merged)} rows, {merged.shape[1]} cols)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
