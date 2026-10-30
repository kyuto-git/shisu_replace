# calc_entry_hensachi.py  (v5互換 + 便利オプション)
import os, glob, argparse
import pandas as pd
import numpy as np
import re
import unicodedata

# === 既存ロジック：列名検出 ===
INDEX_PATTERNS = [
    r"^カツラキ$",
    r"^GoldAirplane$",
    r"^Gold$",
    r"^Gold(指数)?$",
    r"^Gold[_ ]?Airplane$",   # 表記ゆれ保険
    r"^BC(index|Index)?$",
    r"^ZI$",
    r"^SAV$",
    r"^ST指数$",
    r"^合計値$",
    r"^展開(力)?$",
    r"^圧勝力$",
    r"^仕上り$",
    r"^先行力$",
    r"^追走力$",
    r"^持久力$",
    r"^持続力$",
    r"^瞬発力$",
    r"^対戦M$",
    r"^タイムM$",
    r"^想定前半( ?[3３][FＦ])?$",
    r"^想定後半( ?[3３][FＦ])?$",
    # 新名称（エイリアス）
    r"^マイニング値$",           # = タイムM（反転対象）
    r"^対戦型マイニング値$",       # = 対戦M（反転しない）
    r"^(?:Ω|オメガ)指数$",
    r"^かよちん指数$",
    r"^keibaai指数$",      # IGNORECASE により keibaAI も拾える
    r"^keibaAI指数$",
    r"^吉馬_SP能力値$",
    r"^吉馬_先行力$",
    r"^吉馬_SP信頼$",
    r"^吉馬_SP調整$",
    r"^吉馬_SP最大$",
    r"^吉馬_末脚力$",
]

# 「小さいほど良い」→反転してから偏差値化する列（既存踏襲）
REVERSE_PATTERNS = [
    r"^タイムM$",
    r"^展開(力)?$",
    r"^想定前半( ?[3３][FＦ])?$",
    r"^想定後半( ?[3３][FＦ])?$",
    r"^マイニング値$",   # 新名称（=タイムM）
    # 対戦M / 対戦型マイニング値 は反転しない
]

ALIASES = {
    "マイニング値": "タイムM",
    "対戦型マイニング値": "対戦M",
    "GoldAirplane": "Gold",
    "Gold指数": "Gold",
}
# 元ファイル「順位」列 → calc の基底名への対応（例：タイムM ←→ マイニング）
RANK_SRC_FROM_BASE = {
    "タイムM": "マイニング順位",
    "対戦M":   "対戦型マイニング順位",
}

# 便利デフォルト（追加）
DEFAULT_IN  = "/app/entries_in"
DEFAULT_OUT = "/app/entries_out"


def _extract_file_index_from_name(path: str) -> int | None:
    """
    ファイル名末尾の _<数字>.csv から数字を返す。
    例: shisu_20251012_2.csv -> 2 / 見つからなければ None
    """
    base = os.path.basename(str(path))
    m = re.search(r'_(\d+)\.csv$', base, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None

def _build_rank_source_map_for_df(df: pd.DataFrame, file_index: int | None,
                                  idx_cols: list[str]) -> dict[str, str]:
    """
    <基底名> → その指数の“元順位列名”のマップを作る。
    優先度:
      1) 明示列（<col>順位 / <基底名>順位 / <基底名>_順位 / <col>_順位 / RANK_SRC_FROM_BASE）
      2) 番号付き「指数順位N」を、DF上の列の出現順で idx_cols と 1対1 対応（位置対応）
      3) 無印「指数順位」は、ファイル内の指数が1種類のときのみ対応
      4) どうしても見つからなければ未マップ（後段で再計算フォールバック）
    ※ 再計算との一致率チェックは行わない（独自タイブレークを尊重するため）
    """
    rank_map: dict[str, str] = {}

    # 1) 明示列を最優先で拾う
    idx_cols_ordered = [c for c in df.columns if c in idx_cols]  # 列の並び順を保持
    for col in idx_cols_ordered:
        base = ALIASES.get(col, col)
        explicit_candidates = []
        if base in RANK_SRC_FROM_BASE:
            explicit_candidates.append(RANK_SRC_FROM_BASE[base])   # 例: タイムM → マイニング順位
        explicit_candidates += [f"{col}順位"]
        if col != base:
            explicit_candidates += [f"{base}順位"]
        explicit_candidates += [f"{base}_順位", f"{col}_順位"]

        for c in explicit_candidates:
            if c and c in df.columns:
                rank_map[base] = c
                break

    # 2) 番号付き「指数順位N」を位置対応で割り当て（全角数字も許容）
    #    例: ファイル内で 1番目の指数列 ↔ 1番目に出現する「指数順位N」
    num_pat = re.compile(r"^指数順位([0-9０-９]+)$")
    rank_cols_numbered_order = [c for c in df.columns if num_pat.match(str(c))]

    # 明示未マップの基底名たち（列順）
    remaining_bases = [ALIASES.get(c, c) for c in idx_cols_ordered if ALIASES.get(c, c) not in rank_map]

    for base, rcol in zip(remaining_bases, rank_cols_numbered_order):
        rank_map[base] = rcol

    # 3) 無印「指数順位」：指数が1種類のときだけ割り当て
    if "指数順位" in df.columns:
        still_missing = [b for b in remaining_bases if b not in rank_map]
        if len(still_missing) == 1:
            rank_map[still_missing[0]] = "指数順位"

    return rank_map

def col_match(name: str, patterns) -> bool:
    n = unicodedata.normalize("NFKC", str(name)).strip()
    return any(re.match(p, n, flags=re.IGNORECASE) for p in patterns)

def find_index_columns(df: pd.DataFrame):
    return [c for c in df.columns if col_match(c, INDEX_PATTERNS)]

def is_reverse_col(col_name: str) -> bool:
    base = ALIASES.get(col_name, col_name)
    return col_match(base, REVERSE_PATTERNS)

def to_band(h):
    if pd.isna(h): return ""
    h = float(h)
    if h >= 71: return "71以上"
    if h >= 66: return "66~70"
    if h >= 61: return "61~65"
    if h >= 51: return "51~60"
    if h >= 41: return "41~50"
    return "40以下"

def to_rank_band(v):
    """順位→帯へ丸める: 1~2位 / 3~5位 / 6~9位 / 10位~"""
    if pd.isna(v):
        return ""
    try:
        r = int(float(v))
    except Exception:
        return ""
    if r <= 2:
        return "1~2位"
    if r <= 5:
        return "3~5位"
    if r <= 9:
        return "6~9位"
    return "10位~"

def derive_race_key(df: pd.DataFrame):
    if "レースID(新/馬番無)" in df.columns:
        return df["レースID(新/馬番無)"]
    if "レースID(新)" in df.columns:
        s = df["レースID(新)"]
        if s.dtype.kind in "iu":
            return (s // 100).astype("Int64")
        else:
            return s.astype(str).str.slice(0, -2)
    if "場 R" in df.columns:
        return df["場 R"].astype(str)
    raise RuntimeError("レースID列が見つかりません（レースID(新/馬番無) / レースID(新) / 場 R）")

def hensachi_numeric(series, race_key, reverse=False, zi_special=False, cls_series=None):
    x = pd.to_numeric(series, errors="coerce")

    # ZI: 0 → 同レース非ゼロ平均、非ゼロ無ければ NaN（既存踏襲）
    if zi_special:
        def rep0(g):
            arr = g.values.astype(float)
            nz = arr[(~np.isnan(arr)) & (arr != 0)]
            if nz.size > 0:
                rep = nz.mean()
                return pd.Series(np.where((arr == 0) | np.isnan(arr), rep, arr), index=g.index)
            else:
                return pd.Series(np.full_like(arr, np.nan, dtype=float), index=g.index)
        x = x.groupby(race_key).transform(rep0)

    if reverse:
        x = -x

    # ZI: クラス名=新馬 は NaN（既存踏襲）
    if zi_special and cls_series is not None:
        mask = cls_series.astype(str).str.contains("新馬", na=False)
        x = x.mask(mask, np.nan)

    def to_h(g):
        arr = g.values.astype(float)
        valid = ~np.isnan(arr)
        if valid.sum() < 2:
            return pd.Series(np.full_like(arr, np.nan, dtype=float), index=g.index)
        m = np.nanmean(arr)
        s = np.nanstd(arr, ddof=0)
        if s == 0 or np.isnan(s):
            h = np.where(valid, 50.0, np.nan)
        else:
            z = (arr - m) / s
            h = 50.0 + 10.0 * z
        return pd.Series(h, index=g.index)

    return x.groupby(race_key).transform(to_h)

def rank_within_race(series, race_key, reverse=False, zi_special=False, cls_series=None, method="dense"):
    # レース内順位（1=最良）
    x = pd.to_numeric(series, errors="coerce")

    if zi_special:
        def rep0(g):
            arr = g.values.astype(float)
            nz = arr[(~np.isnan(arr)) & (arr != 0)]
            if nz.size > 0:
                rep = nz.mean()
                return pd.Series(np.where((arr == 0) | np.isnan(arr), rep, arr), index=g.index)
            else:
                return pd.Series(np.full_like(arr, np.nan, dtype=float), index=g.index)
        x = x.groupby(race_key).transform(rep0)

    if reverse:
        x = -x

    if zi_special and cls_series is not None:
        mask = cls_series.astype(str).str.contains("新馬", na=False)
        x = x.mask(mask, np.nan)

    # ここだけ method を差し替え
    return x.groupby(race_key).rank(ascending=False, method=method)

# === ここから追加（rank_within_race の直下に配置）========================
DEFAULT_MERGED_NAME = "entries_merged_hensachi.csv"


def read_csv_any(path: str) -> pd.DataFrame:
    for enc in ("cp932", "shift_jis", "utf-8-sig", "utf-8"):
        try:
            # return pd.read_csv(path, encoding=enc, dtype=str)  # ★ dtype=str を追加
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
        except Exception:
            continue
    raise RuntimeError(f"読み込み失敗: {path}")

def _choose_key(df: pd.DataFrame) -> str:
    for k in ("レースID(新)", "レースID(新/馬番無)", "場 R"):
        if k in df.columns:
            return k
    raise RuntimeError("結合キー列が見つかりません（レースID(新)/レースID(新/馬番無)/場 R）")

def merge_on_key(dfs: list[pd.DataFrame], key: str | None = None) -> pd.DataFrame:
    # 先頭DFのキーを基準に。明示keyが来たらそれを優先
    base_key = key or _choose_key(dfs[0])

    def _prep(df: pd.DataFrame) -> pd.DataFrame:
        df2 = df.copy()
        use_key = base_key if base_key in df2.columns else _choose_key(df2)
        df2[use_key] = df2[use_key].astype(str)
        return df2.set_index(use_key)

    base = _prep(dfs[0])
    for add in dfs[1:]:
        addi = _prep(add)
        for c in addi.columns:
            if c in base.columns:
                base[c] = base[c].where(base[c].notna(), addi[c])
            else:
                base[c] = addi[c]
    return base.reset_index()

# 既存の _pick_original_rank_series を置換
def _pick_original_rank_series(df: pd.DataFrame, col: str, base_name: str,
                               race_key, reverse: bool, zi_mode: bool, class_col,
                               rank_source_map: dict[str, str] | None):
    """
    優先順:
      A) rank_source_map[基底名] があればそれ（例: ST指数 → 「指数順位2」）
      B) 既存候補（RANK_SRC_FROM_BASE / <col>順位 / <基底名>順位 / <基底名>_順位 / <col>_順位）
      C) フォールバック再計算（同値同順位・次順位飛び = competition 'min'）
    """
    # A) マップ優先
    if rank_source_map and base_name in rank_source_map:
        cname = rank_source_map[base_name]
        if cname in df.columns:
            return pd.to_numeric(df[cname], errors="coerce")

    # B) 既存候補
    candidates = []
    if base_name in RANK_SRC_FROM_BASE:
        candidates.append(RANK_SRC_FROM_BASE[base_name])
    candidates.append(f"{col}順位")
    if col != base_name:
        candidates.append(f"{base_name}順位")
    candidates.append(f"{base_name}_順位")
    candidates.append(f"{col}_順位")
    # 単一指数ファイルだけ、汎用「指数順位」を許容
    if "指数順位" in df.columns:
        try:
            idx_cols_all = [c for c in df.columns if col_match(c, INDEX_PATTERNS)]
            if len(idx_cols_all) == 1:
                candidates.append("指数順位")
        except Exception:
            pass

    for c in candidates:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")

    # C) どうしても見つからない場合だけ再計算（competition: method='min'）
    x = pd.to_numeric(df[col], errors="coerce")
    if zi_mode:
        def rep0(g):
            arr = g.values.astype(float)
            nz = arr[(~np.isnan(arr)) & (arr != 0)]
            if nz.size > 0:
                rep = nz.mean()
                return pd.Series(np.where((arr == 0) | np.isnan(arr), rep, arr), index=g.index)
            else:
                return pd.Series(np.full_like(arr, np.nan, dtype=float), index=g.index)
        x = x.groupby(race_key).transform(rep0)
    if reverse:
        x = -x
    if zi_mode and class_col is not None:
        mask = class_col.astype(str).str.contains("新馬", na=False)
        x = x.mask(mask, np.nan)
    return x.groupby(race_key).rank(ascending=False, method="min")

def process_df(df: pd.DataFrame, verbose=True, fname="(df)", rank_source_map: dict[str, str] | None = None) -> pd.DataFrame:
    """
    既存のファイル処理の“中身”を DataFrame 版に切り出し。
    偏差値・順位列を付与して DF を返す。
    """
    # process_df() の先頭あたり（race_key 算出の前）に追加
    # --- 列名の互換補完（app.py が参照する名に合わせる）---
    if "芝・ダ" not in df and "芝ダ" in df:
        df["芝・ダ"] = df["芝ダ"]

    if "クラス名" not in df and "クラス" in df:
        df["クラス名"] = df["クラス"]

    # あればでOK（なくても動くが、あるとキーの一致率が上がる）
    if "芝(内・外)" not in df and "外回り" in df:
        df["芝(内・外)"] = df["外回り"].astype(str).str.strip().map(
            lambda v: "外" if v in ("外","外回り","True","1","true") else ("内" if v in ("内","0","False","false") else "")
        )
    # race_key = derive_race_key(df)
    race_key = derive_race_key(df).astype("category")
    class_col = df["クラス名"] if "クラス名" in df.columns else (df["クラス"] if "クラス" in df.columns else None)

    idx_cols = find_index_columns(df)
    if not idx_cols and verbose:
        print(f"[WARN] 指数列が見つかりません: {fname}")

    # for col in idx_cols:
    #     base_name = ALIASES.get(col, col)
    #     reverse   = is_reverse_col(base_name)
    #     zi_mode   = (base_name == "ZI")

    #     hn = hensachi_numeric(
    #         df[col], race_key=race_key, reverse=reverse,
    #         zi_special=zi_mode, cls_series=class_col
    #     ).round(0)
    #     hb = hn.map(to_band)

    #     rk = _pick_original_rank_series(
    #         df, col=col, base_name=base_name,
    #         race_key=race_key, reverse=reverse, zi_mode=zi_mode, class_col=class_col,
    #         rank_source_map=rank_source_map
    #     )

    #     col_h = f"{base_name}_偏差値"
    #     col_b = f"{base_name}_偏差値帯"
    #     col_r = f"{base_name}_順位"

    #     for c in (col_h, col_b, col_r):
    #         if c in df.columns:
    #             df.drop(columns=[c], inplace=True)

    #     df.insert(df.columns.get_loc(col) + 1, col_h, hn)
    #     df.insert(df.columns.get_loc(col) + 2, col_b, hb)
    #     df.insert(df.columns.get_loc(col) + 3, col_r, rk)

    for col in idx_cols:
        base_name = ALIASES.get(col, col)
        reverse   = is_reverse_col(base_name)
        zi_mode   = (base_name == "ZI")

        # 偏差値・帯
        hn = hensachi_numeric(
            df[col], race_key=race_key, reverse=reverse,
            zi_special=zi_mode, cls_series=class_col
        ).round(0)
        hb = hn.map(to_band)

        # 元順位（マッピング優先 → 候補列 → 再計算フォールバック）
        rk = _pick_original_rank_series(
            df, col=col, base_name=base_name,
            race_key=race_key, reverse=reverse, zi_mode=zi_mode, class_col=class_col,
            rank_source_map=rank_source_map
        )

        # ★ 追加：順位帯
        rb = rk.map(to_rank_band)

        # 列名
        col_h  = f"{base_name}_偏差値"
        col_b  = f"{base_name}_偏差値帯"
        col_r  = f"{base_name}_順位"
        col_rb = f"{base_name}_順位帯"   # ★ 新規

        # 既存列があれば消してから決まった順序で挿入
        for c in (col_h, col_b, col_r, col_rb):
            if c in df.columns:
                df.drop(columns=[c], inplace=True)

        base_idx = df.columns.get_loc(col)
        df.insert(base_idx + 1, col_h,  hn)
        df.insert(base_idx + 2, col_b,  hb)
        df.insert(base_idx + 3, col_r,  rk)
        df.insert(base_idx + 4, col_rb, rb)   # ★ 追加

    return df

def process_file(in_path, out_path, verbose=True):
    df0 = read_csv_any(in_path)
    # このファイル内の指数列を抽出
    idx_cols0 = find_index_columns(df0)
    k = _extract_file_index_from_name(in_path)
    rank_map0 = _build_rank_source_map_for_df(df0, k, idx_cols0)

    df = process_df(df0, verbose=verbose, fname=os.path.basename(in_path), rank_source_map=rank_map0)
    df.to_csv(out_path, index=False, encoding="shift_jis")
    if verbose:
        print(f"[OK] {os.path.basename(in_path)} -> {os.path.basename(out_path)}  ({len(df)} rows)")

def main():
    ap = argparse.ArgumentParser()
    # 互換：従来の --in_dir/--out_dir も、新設の --in-dir/--out-dir も受け付ける
    ap.add_argument("--in_dir", default=None)
    ap.add_argument("--out_dir", default=None)
    ap.add_argument("--in-dir",  dest="in_dir_dash",  default=None)
    ap.add_argument("--out-dir", dest="out_dir_dash", default=None)

    # 追加：統合出力オプション
    ap.add_argument("--merge-one",  action="store_true",
                    help="entries_in 内の全CSVを『レースID(新)』で横結合して1ファイル出力（個別出力も併せて実施）")
    ap.add_argument("--merge-only", action="store_true",
                    help="横結合の1ファイルのみ出力（個別出力は行わない）")
    ap.add_argument("--merge-name", default=DEFAULT_MERGED_NAME,
                    help=f"統合出力のファイル名（既定: {DEFAULT_MERGED_NAME}）")

    args = ap.parse_args()
    in_dir  = args.in_dir or args.in_dir_dash or DEFAULT_IN
    out_dir = args.out_dir or args.out_dir_dash or DEFAULT_OUT
    os.makedirs(out_dir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(in_dir, "*.csv")))
    if not files:
        print("[INFO] 入力CSVが見つかりません:", in_dir)
        return

    # 1) 個別出力（既定挙動）
    if not args.merge_only:
        for p in files:
            base = os.path.basename(p)
            out = os.path.join(out_dir, os.path.splitext(base)[0] + "_hensachi.csv")
            try:
                process_file(p, out)
            except Exception as e:
                print(f"[ERROR] {base}: {e}")

    # 2) 統合1ファイル出力（オプション）
    if args.merge_one or args.merge_only:
        try:
            processed_dfs = []
            for p in files:
                df0 = read_csv_any(p)
                # 各ファイルで元順位の対応付けマップを“中身で”推定
                idx_cols0 = find_index_columns(df0)
                k = _extract_file_index_from_name(p)  # 使わないが引数シグネチャ維持OK
                rank_map0 = _build_rank_source_map_for_df(df0, k, idx_cols0)

                # ★ 各ファイル個別に process_df（ここで偏差値＆<指数名>_順位 を確定）
                dfp = process_df(df0, verbose=False, fname=os.path.basename(p), rank_source_map=rank_map0)
                processed_dfs.append(dfp)

            # ★ ここでは“結合のみ”。再計算はしない！
            # merged = merge_on_key(processed_dfs, key="レースID(新)")
            merged = merge_on_key(processed_dfs)  # 自動選択に変更

            merged_path = os.path.join(out_dir, args.merge_name)
            merged.to_csv(merged_path, index=False, encoding="shift_jis")
            print(f"[OK] merged -> {os.path.basename(merged_path)}  ({len(merged)} rows, {merged.shape[1]} cols)")
        except Exception as e:
            print(f"[ERROR] merge: {e}")

if __name__ == "__main__":
    main()