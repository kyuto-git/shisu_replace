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

一部の指数だけの差し替え（--indicators タイムM,対戦M。--mining-only はその短い書き方。2026-11-12〜）:
  馬体重の発表後にマイニングの確定値が出たとき用。entries_in/ の出馬表CSVを上書きしてから実行すると、
  entries_out/ の統合ファイル（entries_merged_hensachi.csv）を読み、指定した指数の値・順位・偏差値・帯と、
  出馬表CSVの基本の列（馬体重・増減・騎手など。指数とその順位の列以外）だけを入れ替えて書き戻す。
  ほかの外部指数の列は前のまま（ext_indices/ は読み直さない）。結果は、同じ入力で通常の処理をしたときと同じ。
  統合ファイルが無い・入力に新しい馬がいるときは、通常の処理（全指数）に切り替える。
"""
from __future__ import annotations

import argparse
import os
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
MINING_INDICATORS = ("タイムM", "対戦M")
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
               last_masks: dict[str, pd.Series] | None = None, ext_cols: Iterable[str] = (),
               only: Iterable[str] | None = None) -> pd.DataFrame:
    """last_masks: 指数列 → 値から順位を付けるとき最下位にする馬（ext_indices のファイルに無かった馬）。
    ext_cols: attach_ext が末尾に足した指数列（build_rank_source_map を参照）。
    only: 計算する指数（正式名）。None なら全部（一部の指数だけの差し替えで使う）。

    派生列は指数ごとに作ってためておき、最後に1回の concat で指数列のすぐ右に並べる
    （df.insert を指数の数×5回くり返すと DataFrame が断片化して PerformanceWarning が大量に出る）。"""
    last_masks = last_masks or {}
    only = None if only is None else set(only)
    rk = race_key(df)
    cls = df["クラス名"] if "クラス名" in df.columns else df.get("クラス", pd.Series("", index=df.index))
    shin = shinba_mask(cls)
    idx_cols = find_indicator_columns(df)
    if not idx_cols and verbose:
        print(f"[WARN] {name}: 指数列が見つかりません")
    rank_map = build_rank_source_map(df, idx_cols, ext_cols)
    seen: set[str] = set()
    after: dict[str, dict[str, pd.Series]] = {}          # 指数列 → そのすぐ右に入れる派生列
    for col in idx_cols:
        canon = canonical_indicator(col)
        if canon in seen:
            print(f"[WARN] {name}: 指数 {canon} の列が複数あります（{col} は無視）", file=sys.stderr)
            continue
        seen.add(canon)
        if only is not None and canon not in only:
            continue
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
        for sc in RANK_SCHEMES:
            derived[f"{canon}_順位帯_{sc}"] = r[f"順位帯_{sc}"].fillna("")
        after[col] = derived
    replaced = {c for d in after.values() for c in d}    # 入力にもとからあった同じ名前の列は、作り直したもので置き換える
    pieces: list[pd.Series] = []
    names: list[str] = []
    for i, c in enumerate(df.columns):
        if c in replaced:
            continue
        pieces.append(df.iloc[:, i])
        names.append(c)
        for dc, ser in after.get(c, {}).items():
            pieces.append(ser.reindex(df.index))
            names.append(dc)
    if not pieces:
        return df.copy()
    out = pd.concat(pieces, axis=1)
    out.columns = names
    return out


def load_ext(ext_root: Path, skip: Iterable[str] = (), log=print, only: Iterable[str] | None = None) -> dict:
    """ext_indices/ の指数（skip にあるもの・廃止したものを除く）→ {正式名: ExtLookup}。
    出馬表に値のある指数かどうかは attach_ext がファイル・レースごとに決める（ここでは読み込むだけ）。
    only を渡すとその指数だけ読む（ext_indices に置ける指数が1つも無ければ、フォルダも見ない）。"""
    skip = set(skip)
    from shisu import ext_indices
    from shisu.indicators import is_discontinued
    if only is not None:
        only = set(only)
        if not only & set(ext_indices.SUPPORTED):      # マイニング（タイムM・対戦M）は出馬表CSVの列から取る
            return {}
    if not Path(ext_root).is_dir():
        return {}
    sources, _skipped = ext_indices.discover(ext_root, log=lambda m: None)
    out = {}
    for name, src in sources.items():
        if name in skip or (only is not None and name not in only):
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
    new_cols: dict[str, pd.Series] = {}       # 末尾に足す列（最後に1回の concat で足す。1列ずつ足すと断片化する）

    def finish() -> pd.DataFrame:
        if not new_cols:
            return df
        return pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)

    rk = None
    for canon, lookup in lookups.items():
        try:
            att = ext_indices.attach_filled(df, lookup)
        except KeyError as e:
            log(f"[WARN] {name}: 外部指数を結び付けられません（{e}）")
            return finish(), masks, added
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
                new_cols[f"{col}順位"] = att.ranks.where(fill, "")
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
            new_cols[col] = att.values
            if att.ranks is not None:
                new_cols[f"{col}順位"] = att.ranks
            masks[col] = att.absent
            added.add(col)
            hit, total = att.hit, len(df)
            norank = att.norank_races
        if att.rank_column_empty:
            note += "（順位の列が空なので値から順位を計算）"
        elif norank:
            note += f"（順位の無いレース {norank} 件は値から順位を計算）"
        log(f"[INFO] {name}: 外部指数 {canon} を ext_indices から付けました（値のある馬 {hit}/{total}）{note}")
    return finish(), masks, added


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


def parse_indicators(text: str) -> list[str]:
    """'タイムM, 対戦M'（別名・全角も可）→ 正式名のリスト。台帳に無い名前は ValueError。"""
    out: list[str] = []
    for raw in re.split(r"[,、，]", text or ""):
        raw = raw.strip()
        if not raw:
            continue
        if not is_known_indicator(raw):
            raise ValueError(f"台帳に無い指数です: {raw}")
        canon = canonical_indicator(raw)
        if canon not in out:
            out.append(canon)
    return out


def _owned_columns(raw: pd.DataFrame, keep: set[str]) -> set[str]:
    """出馬表CSVの列のうち、keep 以外の指数のもの（指数列・その順位列・派生列）。
    一部の指数だけの差し替えでは、これらの列は統合ファイルの前の値（ext_indices から付けた値）を残す。"""
    idx_cols = find_indicator_columns(raw)
    rank_map = build_rank_source_map(raw, idx_cols)
    out: set[str] = set()
    for col in idx_cols:
        canon = canonical_indicator(col)
        if canon in keep:
            continue
        out.add(col)
        if col in rank_map:
            out.add(rank_map[col])
        out |= {f"{canon}{sfx}" for sfx in DERIVED_SUFFIXES}
    return out


def refresh_indicators(raws: dict[Path, pd.DataFrame], merged_path: Path, targets: list[str],
                       lookups: dict | None = None, log=print) -> bool:
    """統合ファイル merged_path の、targets の指数（値・順位・偏差値・帯）と出馬表CSVの基本の列だけを入れ替える。
    戻り値: 入れ替えたら True。できない（統合ファイルが無い・読めない・入力に新しい馬がいる・入力のレースから馬が消えた）
    なら False（呼び出し側が通常の処理に切り替える）。入力に無いレース（別の日の出馬表など）の行は前のまま残す。"""
    if not merged_path.exists():
        log(f"[INFO] {merged_path.name} がまだ無いので、通常の処理（全指数）をします")
        return False
    try:
        old = read_csv_any(merged_path)
    except Exception as e:
        log(f"[WARN] {merged_path.name} を読めません（{type(e).__name__}: {e}）。通常の処理（全指数）をします")
        return False
    keep = set(targets)
    processed, protected = [], set()
    for path, raw in raws.items():
        protected |= _owned_columns(raw, keep)
        raw2, masks, added = attach_ext(raw, lookups or {}, path.name, log=log)
        processed.append(process_df(raw2, path.name, verbose=False, last_masks=masks, ext_cols=added, only=keep))
    fresh = merge_frames(processed)
    for t in targets:
        if f"{t}_順位" not in fresh.columns:
            log(f"[WARN] 入力の出馬表CSVに {t} の列がありません（{t} は前の値のままです。"
                "TARGET でマイニングを読み込んでから CSV を出し直してください）")
    key = _merge_key_name(fresh)
    if key not in old.columns:
        log(f"[WARN] {merged_path.name} に {key} の列がありません。通常の処理（全指数）をします")
        return False
    old[key] = norm_series(old[key])
    if old[key].duplicated().any() or (old[key] == "").any():
        log(f"[WARN] {merged_path.name} の {key} が重複・空欄です。通常の処理（全指数）をします")
        return False
    o, f = old.set_index(key), fresh.set_index(key)
    new_rows = f.index.difference(o.index)
    if len(new_rows):
        log(f"[INFO] 入力に、前の統合ファイルに無い馬が {len(new_rows)} 頭います（出馬表が変わった）。"
            "外部指数を付け直すので、通常の処理（全指数）をします")
        return False
    # 入力にあるレースから馬が消えている（取消の馬を除いて出し直した など）なら、そのレースの順位・偏差値は全指数で変わる
    races = set(f.index.astype(str).str.slice(0, 16)) if key == "レースID(新)" else set()
    gone = [k for k in o.index.difference(f.index) if str(k)[:16] in races]
    if gone:
        log(f"[INFO] 入力のレースから、前の統合ファイルにいた馬が {len(gone)} 頭消えています（出馬表が変わった）。"
            "全指数の順位・偏差値を付け直すので、通常の処理（全指数）をします")
        return False
    common = f.index                                   # 入れ替える行（入力にある馬）
    take = [c for c in f.columns if c not in protected]
    have = [c for c in take if c in o.columns]
    extra = [c for c in take if c not in o.columns]    # 統合ファイルに無かった列（末尾に足す）
    before = o.loc[common, have].astype(object)
    after = f[have].astype(object)
    changed_cells = (before.where(before.notna(), "") != after.where(after.notna(), ""))
    explicit_ranks = {c for t in targets for c in EXPLICIT_RANK_COLUMNS.get(t, ())}
    derived = {f"{t}{sfx}" for t in targets for sfx in DERIVED_SUFFIXES}
    target_cols = [c for c in have if c in derived or c in explicit_ranks
                   or (is_known_indicator(c) and canonical_indicator(c) in keep)]
    o = o.astype(object)
    o.loc[common, have] = after
    if extra:
        add = pd.DataFrame("", index=o.index, columns=extra, dtype=object)
        add.loc[common, extra] = f[extra].astype(object)
        o = pd.concat([o, add], axis=1)
    out = o.reset_index()
    out = out[[c for c in old.columns] + extra]
    out = out.astype(object).where(out.notna(), "")
    tmp = merged_path.with_name(merged_path.name + ".part")
    write_csv(out, tmp)
    os.replace(tmp, merged_path)
    kept = len(o.index.difference(common))
    n_changed = int(changed_cells.any(axis=1).sum())
    n_target = int(changed_cells[[c for c in target_cols if c in changed_cells.columns]].any(axis=1).sum()) if target_cols else 0
    log(f"[OK] {'・'.join(targets)} と基本の列を差し替えました -> {merged_path.name}"
        f"（{len(common)} 頭のうち値が変わった馬 {n_changed} 頭・うち {'・'.join(targets)} が変わった馬 {n_target} 頭。"
        f"ほかの指数の列は前のまま）")
    if kept:
        log(f"[INFO] 入力に無いレースの {kept} 頭の行は前のまま残しました（消すには通常の --merge-only --clean）")
    return True


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
    ap.add_argument("--indicators", default=None,
                    help="この指数だけ差し替える（カンマ区切り。例 タイムM,対戦M）。統合ファイルを読み、指定した指数の値・順位・"
                         "偏差値・帯と出馬表CSVの基本の列だけを入れ替える（ほかの外部指数は前のまま。ext_indices は読み直さない）")
    ap.add_argument("--mining-only", action="store_true",
                    help=f"--indicators {','.join(MINING_INDICATORS)} と同じ（馬体重の発表後のマイニング確定値の差し替え用）")
    args = ap.parse_args(argv)
    try:
        targets = parse_indicators(args.indicators or "")
        if args.indicators is not None and not targets:   # 空の指定（空のシェル変数など）で全部の計算にしない
            raise ValueError("指数名がありません（例 --indicators タイムM,対戦M）")
    except ValueError as e:
        print(f"[ERROR] --indicators: {e}", file=sys.stderr)
        return 2
    if args.mining_only:
        targets += [t for t in MINING_INDICATORS if t not in targets]

    files = sorted(Path(args.in_dir).glob("*.csv"))
    if not files:
        print(f"[INFO] 入力CSVが見つかりません: {args.in_dir}")
        return 0
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    merged_path = out_dir / args.merge_name
    if args.clean:                                     # 一部の指数だけの差し替えでは、統合ファイルは消さない（読んで入れ替える）
        for old in out_dir.glob("*.csv"):
            if not (targets and old.name == merged_path.name):
                old.unlink()

    processed, rc = [], 0
    raws: dict[Path, pd.DataFrame] = {}
    for p in files:
        try:
            raws[p] = read_csv_any(p)
        except Exception as e:
            print(f"[ERROR] {p.name}: {type(e).__name__}: {e}")
            rc = 1
    if targets:
        if rc:
            print("[ERROR] 読めない出馬表CSVがあるので、差し替えをやめました", file=sys.stderr)
            return 1
        try:
            lookups = {} if args.no_ext else load_ext(Path(args.ext_dir), only=targets)
            if refresh_indicators(raws, merged_path, targets, lookups):
                return 0
        except Exception as e:
            print(f"[ERROR] 差し替え: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        args.merge_only = True                         # 通常の処理に切り替える（統合ファイルを作る）
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
