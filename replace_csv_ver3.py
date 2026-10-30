# -*- coding: utf-8 -*-
"""
replace_csv_ver3.py  — ver2ベース最小変更版
- 既存の置換・偏差値・順位帯・枠番グループ等の処理は維持
- 追加（１）結合データの先頭を「場所×芝・ダ×距離×(内・外)」に統一
- 追加（２）foragg_* に「場所 / 芝・ダ / 距離 / 芝(内・外)」の4列を明示的に同梱
- 追加（３）集計用に per-row の回収寄与列を付与（単勝回収寄与・複勝回収寄与）
  ※ 集計（合計・平均等）は aggregate_stats 側で実施する前提。ここでは素材として列を追加。
"""

import os
import re
import numpy as np
import pandas as pd
import unicodedata

# 新馬戦を除外するメトリック（ZI と同様の扱い）
_SHINBA_EXCLUDE_KEYS = [
    "ZI",                # 既存
    "吉馬_SP能力値",
    "吉馬_先行力",
    "吉馬_SP信頼",
    "吉馬_SP調整",
    "吉馬_SP最大",
    "吉馬_末脚力",
]

def _norm_upper(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s)).upper()

def _metric_excludes_shinba_from_filename(path_or_name: str) -> bool:
    base = _norm_upper(os.path.basename(str(path_or_name)))
    return any(_norm_upper(k) in base for k in _SHINBA_EXCLUDE_KEYS)
# =========================================
# 設定：ディレクトリ
# =========================================
CSV_DIR       = "./csv_files"     # 入力元
OUT_PROC_DIR  = "./replace_csv"   # processed_* 出力先（置換後デバッグ素材）
OUT_FORAGG_DIR= "./foragg_csv"    # foragg_* 出力先（集計素材）

os.makedirs(OUT_PROC_DIR, exist_ok=True)
os.makedirs(OUT_FORAGG_DIR, exist_ok=True)

# =========================================
# 設定：列名（存在チェック付きで利用）
# =========================================
RID_COL       = "レースID(新)"
RIDN_COL      = "レースID(新/馬番無)"
PLACE_COL     = "場所"
WEIGHT_COL    = "重量種別"
CLASS_COL     = "クラス名"
HEAD_COL      = "頭数"
INNER1_COL    = "芝(内・外)"   # 優先
INNER2_COL    = "芝(内外)"     # 代替（ファイルによって名前ぶれ対応）
SURFACE_COL   = "芝・ダ"
DIST_COL      = "距離"
COURSE_COL    = "コース区分"
FRAME_COL     = "枠番"
JOCKEY_COL    = "騎手"
INDEX_COL     = "指数"
RANK_COL      = "指数順位"

# レース結果系（集計に必要）
FIN_COL       = "着順"       # 文字列（取消・除外・中止 等が混在しうる）
ODDS_WIN_COL  = "単勝オッズ"
PAY_WIN_COL   = "単勝配当"
PAY_PLC_COL   = "複勝配当"   # 「複勝オッズ下限/上限」がある場合もあるが、配当があれば十分


# ---- 順位帯（レンジ）設定 ----
RANK_BAND_COL = "指数順位帯"
RANK_BANDS = [
    ("1~2位",  lambda r: r in (1, 2)),
    ("1~3位",  lambda r: r in (1, 2, 3)),
    ("1~4位",  lambda r: r in (1, 2, 3, 4)),
    ("1~5位",  lambda r: r in (1, 2, 3, 4, 5)),
    ("2~3位",  lambda r: r in (2, 3)),
    ("2~4位",  lambda r: r in (2, 3, 4)),
    ("2~5位",  lambda r: r in (2, 3, 4, 5)),
    ("3~4位",  lambda r: r in (3, 4)),
    ("3~5位",  lambda r: r in (3, 4, 5)),
    ("4~5位",  lambda r: r in (4, 5)),
    ("6位以下", lambda r: (isinstance(r, (int, float)) and r >= 6)),
]

# === 単勝オッズ フィルタ（素材の追加出力用） ===
# ここを変えるだけで閾値を可変にできます
ODDS_FILTERS = [
    ("odds10u_", lambda s: s < 10.0),   # 10.0未満
    ("odds5p_",  lambda s: s >= 5.0),   # 5.0以上（既存維持）
    ("odds10_50_", lambda s: (s >= 10.0) & (s < 50.0)), # ★追加：10倍以上50倍未満
]
# =========================================
# 文字コードの堅牢読み込み（ログ処理は入れない）
# =========================================

def smart_read_csv(path: str) -> pd.DataFrame:
    """
    入力CSVのエンコーディングを複数試す。
    - 1段目: 通常読み（エンジン自動）→ encodingのみ指定、dtype=str
    - 2段目: errors='ignore' でファイルオブジェクトから読む（engine='python'、※low_memoryは付けない）
    - 3段目: on_bad_lines='skip' を付けて読み飛ばし
    いずれも NaN は後段の処理でまとめて扱う前提（ここでは dtype=str を徹底）
    """
    encodings = ["cp932", "shift_jis", "utf-8-sig", "utf-8", "latin1"]
    last_err = None

    for enc in encodings:
        # 1) 通常読み（engine は自動。Cエンジンなら low_memory 既定OK）
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except Exception as e1:
            last_err = f"read_csv(path, encoding={enc}) -> {e1}"

        # 2) errors='ignore' で再挑戦（engine='python'。ここでは low_memory を付けない）
        try:
            with open(path, "r", encoding=enc, errors="ignore", newline="") as f:
                return pd.read_csv(f, dtype=str, engine="python")
        except Exception as e2:
            last_err = f"read_csv(fileobj, encoding={enc}, errors=ignore, engine=python) -> {e2}"

        # 3) 行スキップ許容で再挑戦
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, on_bad_lines="skip")
        except Exception as e3:
            last_err = f"read_csv(path, encoding={enc}, on_bad_lines=skip) -> {e3}"
            continue

    raise RuntimeError(f"failed to decode '{path}' with tried encodings. last={last_err}")


# =========================================
# SJIS 安全化（保存直前に適用）
# =========================================
def sanitize_df_for_sjis(df: pd.DataFrame) -> pd.DataFrame:
    """
    列名も値も、全て shift_jis で書ける形にする（dtypeに依存しない）
    """
    import unicodedata

    trans = {
        "〜": "～",
        "∼": "～",
        "～": "～",

        "−": "-",
        "‐": "-",
        "\u2011": "-",  # NON-BREAKING HYPHEN
        "‒": "-",
        "–": "-",
        "—": "-",

        "\u00A0": " ",
        "•": "・",
        "·": "・",

        "Ⅰ": "I",  "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V",
        "Ⅵ": "VI", "Ⅶ": "VII","Ⅷ": "VIII","Ⅸ": "IX","Ⅹ": "X",
        "Ⅺ": "XI", "Ⅻ": "XII",
    }

    def _safe(x):
        # まず全部 str 化（None / nan も含めて扱う）
        s = "" if x is None else str(x)

        # 1) 互換正規化（丸数字→通常数字など）
        s = unicodedata.normalize("NFKC", s)

        # 2) 追加置換
        for k, v in trans.items():
            s = s.replace(k, v)

        # 3) shift_jis に通らない文字は除去
        try:
            s.encode("shift_jis")
            return s
        except Exception:
            return s.encode("shift_jis", errors="ignore").decode("shift_jis", errors="ignore")

    df2 = df.copy()

    # ✅ 列名（ヘッダー）も安全化
    df2.columns = [_safe(c) for c in df2.columns]

    # ✅ 全列を dtype 無視で安全化（ここが今回の肝）
    for c in df2.columns:
        df2[c] = df2[c].map(_safe)

    return df2

# def sanitize_df_for_sjis(df: pd.DataFrame) -> pd.DataFrame:
#     """
#     SJIS/CP932 に非対応・化けやすい文字を“保存直前”にだけ安全化する。
#     - NFKC 正規化で丸数字→半角数字、全角英数→半角 などの互換正規化
#     - 波ダッシュ/チルダの統一
#     - マイナス/ダッシュ類の統一
#     - ローマ数字は ASCII へ（必要なら）
#     """
#     import unicodedata

#     trans = {
#         # 波ダッシュ/チルダの統一（U+301C, U+223C など → U+FF5E）
#         "〜": "～",    # U+301C → U+FF5E
#         "∼": "～",    # U+223C → U+FF5E
#         "～": "～",    # 明示

#         # マイナス/ハイフン/ダッシュの統一（ASCII '-' へ）
#         "−": "-",     # U+2212
#         "‐": "-",     # U+2010
#         "\u2011": "-",# ★ U+2011 (NON-BREAKING HYPHEN) ← 追加
#         "-": "-",     # U+2011
#         "‒": "-",     # U+2012
#         "–": "-",     # U+2013
#         "—": "-",     # U+2014

#         # 中黒・NBSP など
#         "\u00A0": " ",  # NBSP → space
#         "•": "・",
#         "·": "・",

#         # ローマ数字（必要な場合）
#         "Ⅰ": "I",  "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V",
#         "Ⅵ": "VI", "Ⅶ": "VII","Ⅷ": "VIII","Ⅸ": "IX","Ⅹ": "X",
#         "Ⅺ": "XI", "Ⅻ": "XII",
#     }

#     def _safe(s):
#         if not isinstance(s, str):
#             return s
#         # 1) まず互換正規化（丸数字→半角数字、全角英数→半角 等）
#         s = unicodedata.normalize("NFKC", s)
#         # 2) 追加の置換
#         for k, v in trans.items():
#             s = s.replace(k, v)
#         # 3) 最後に CP932 非対応を除去
#         try:
#             s.encode("cp932")
#             return s
#         except Exception:
#             return s.encode("cp932", errors="ignore").decode("cp932", errors="ignore")

#     return df.apply(lambda c: c.map(_safe) if c.dtype == "object" else c)

# =========================================
# ユーティリティ
# =========================================
def _inner_col(df: pd.DataFrame) -> str:
    if INNER1_COL in df.columns:
        return INNER1_COL
    if INNER2_COL in df.columns:
        return INNER2_COL
    return None

def to_int_str(x) -> str:
    try:
        v = int(float(str(x).strip()))
        return str(v)
    except Exception:
        return ""

# =========================================
# 追加：指数欠損ルール適用（レース単位）
# =========================================
def _ridn_series(df: pd.DataFrame) -> pd.Series:
    """レースID(新/馬番無) が無ければフォールバックを作る"""
    if RIDN_COL in df.columns:
        return df[RIDN_COL].astype(str)
    if RID_COL in df.columns:
        return df[RID_COL].astype(str).str.slice(0, -2)
    return pd.Series(["ALL"] * len(df), index=df.index)

def apply_index_missing_policy(df: pd.DataFrame) -> pd.DataFrame:
    """
    ルール：
      - 同一レースで '指数' 欠損が2頭以上 → そのレース全頭を除外
      - 同一レースで '指数' 欠損が1頭だけ → その馬は偏差値計算から除外（NaNのまま）＆ '指数順位' を空にする
    """
    if INDEX_COL not in df.columns or len(df) == 0:
        return df

    ridn = _ridn_series(df)
    idx_num = pd.to_numeric(df[INDEX_COL], errors="coerce")  # 空/非数は NaN
    miss = idx_num.isna()

    # レースごとの欠損頭数
    miss_cnt = miss.groupby(ridn).transform("sum")

    # 2頭以上欠損のレースは丸ごと落とす
    keep_race = miss_cnt < 2
    df2 = df.loc[keep_race].copy()

    # 1頭だけ欠損の馬：順位を空に（帯展開/結合キーからも事実上外れる）
    if RANK_COL in df2.columns and len(df2):
        ridn2 = _ridn_series(df2)
        idx2 = pd.to_numeric(df2[INDEX_COL], errors="coerce")
        miss2 = idx2.isna()
        miss_cnt2 = miss2.groupby(ridn2).transform("sum")
        only_one_missing = miss2 & (miss_cnt2 == 1)
        if only_one_missing.any():
            df2.loc[only_one_missing, RANK_COL] = ""

    return df2

# === 固定帯スキーム定義（A/B の2通り） ===
RANKBAND_SCHEMES = {
    "rankband_s1": [  # A
        ("1~3位",  lambda r: r is not None and 1 <= r <= 3),
        ("4~6位",  lambda r: r is not None and 4 <= r <= 6),
        ("7~9位",  lambda r: r is not None and 7 <= r <= 9),
        ("10位~",  lambda r: r is not None and r >= 10),
    ],
    "rankband_s2": [  # B
        ("1~5位",   lambda r: r is not None and 1 <= r <= 5),
        ("6~10位",  lambda r: r is not None and 6 <= r <= 10),
        ("11位~",   lambda r: r is not None and r >= 11),
    ],
}

def _to_int_or_none(v):
    try:
        return int(float(str(v)))
    except Exception:
        return None

def assign_fixed_rankband(df: pd.DataFrame, scheme_name: str) -> pd.DataFrame:
    """
    各行に“1つだけ”順位帯を割り当てる（行増殖しない）。
    scheme_name: "rankband_s1" または "rankband_s2"
    """
    if RANK_COL not in df.columns:
        return df.assign(**{RANK_BAND_COL: ""})

    if scheme_name not in RANKBAND_SCHEMES:
        raise ValueError(f"unknown scheme: {scheme_name}")

    ranks = df[RANK_COL].map(_to_int_or_none)
    out = df.copy()
    bands = []
    rules = RANKBAND_SCHEMES[scheme_name]
    for i in df.index:
        r = ranks.get(i)
        label = ""
        for band_name, pred in rules:
            if pred(r):
                label = band_name
                break
        bands.append(label)
    out[RANK_BAND_COL] = bands
    return out

# =========================================
# 基本クリーニング＆置換（ver2既存ロジック＋最小追記）
# =========================================
def basic_clean(df: pd.DataFrame) -> pd.DataFrame:
    # 文字列化＆空白除去
    df = df.astype(str).replace(r"\s+", "", regex=True).replace("　", "", regex=False)

    # 重量種別：別定/定量 → 定量
    if WEIGHT_COL in df.columns:
        df[WEIGHT_COL] = df[WEIGHT_COL].replace({"別定": "定量", "定量": "定量"})

    # クラス名グループ化
    if CLASS_COL in df.columns:
        df[CLASS_COL] = df[CLASS_COL].replace({
            "500万": "1・2_勝", "1000万": "1・2_勝", "1勝": "1・2_勝", "2勝": "1・2_勝",
            "1600万": "準OP・OP", "3勝": "準OP・OP", "ｵｰﾌﾟﾝ": "準OP・OP", "OP(L)": "準OP・OP",
            "Ｇ２": "重賞", "Ｇ３": "重賞"
        })
        # （A）馬齢 ×（1・2_勝／準OP・OP）→ 1勝・OP
        if WEIGHT_COL in df.columns:
            mask = (df[WEIGHT_COL] == "馬齢") & (df[CLASS_COL].isin(["1・2_勝", "準OP・OP"]))
            df.loc[mask, CLASS_COL] = "1勝・OP"

    # コース区分の末尾 ,A/B/C/D のカンマ除去＆ A/B/C/D は空に
    if COURSE_COL in df.columns:
        df[COURSE_COL] = df[COURSE_COL].str.replace(",", "", regex=False)
        df[COURSE_COL] = df[COURSE_COL].replace({"A": "", "B": "", "C": "", "D": ""})

    # 指数順位を整数文字列化（1.0 → 1）
    if RANK_COL in df.columns:
        df[RANK_COL] = df[RANK_COL].map(to_int_str)

    # 枠番グループ（頭数で可変）— 9頭以下は空
    if FRAME_COL in df.columns and HEAD_COL in df.columns:
        def _frame_group(row):
            try:
                n = int(float(str(row.get(HEAD_COL, "") or "0")))
                f = int(float(str(row.get(FRAME_COL, "") or "0")))
            except Exception:
                return ""
            if n <= 9:
                return ""
            if 10 <= n <= 13:
                return "内枠" if 1 <= f <= 5 else ("外枠" if 6 <= f <= 8 else "")
            if 14 <= n <= 16:
                return "内枠" if 1 <= f <= 4 else ("外枠" if 5 <= f <= 8 else "")
            if 17 <= n <= 18:
                if 1 <= f <= 3: return "内枠"
                if 4 <= f <= 6: return "中枠"
                if 7 <= f <= 8: return "外枠"
                return ""
            return ""
        df["枠番グループ"] = df.apply(_frame_group, axis=1)

    return df

# =========================================
# 距離まとめ（ver2相当）— 必須部分のみ（札幌芝・中京芝はコメントアウトのまま）
# =========================================
def apply_place_distance_rules(df: pd.DataFrame) -> pd.DataFrame:
    inner_col = _inner_col(df)
    if DIST_COL in df.columns:
        df[DIST_COL] = df[DIST_COL].astype(str)

    def _apply(*, place=None, turf=None, distance=None, inner=None,
               new_turf=None, new_distance=None, new_inner=None):
        cond = pd.Series(True, index=df.index)
        if place is not None and PLACE_COL in df.columns:
            cond &= (df[PLACE_COL] == place)
        if turf is not None and SURFACE_COL in df.columns:
            cond &= (df[SURFACE_COL] == turf)
        if distance is not None and DIST_COL in df.columns:
            cond &= (df[DIST_COL] == str(distance))
        if inner is not None and inner_col is not None:
            cond &= (df[inner_col] == inner)
        if new_turf is not None and SURFACE_COL in df.columns:
            df.loc[cond, SURFACE_COL] = new_turf
        if new_distance is not None and DIST_COL in df.columns:
            df.loc[cond, DIST_COL] = new_distance
        if new_inner is not None and inner_col is not None:
            df.loc[cond, inner_col] = new_inner

    # 札幌（芝はコメントアウト）
    # _apply(place="札幌", turf="芝", distance="1500", new_distance="15-18")
    # _apply(place="札幌", turf="芝", distance="1800", new_distance="15-18")

    # 福島
    _apply(place="福島", turf="芝", distance="1700", new_distance="17-18")
    _apply(place="福島", turf="芝", distance="1800", new_distance="17-18")

    # 新潟（内・外）
    _apply(place="新潟", inner="内", turf="芝", distance="1200", new_distance="12-14")
    _apply(place="新潟", inner="内", turf="芝", distance="1400", new_distance="12-14")
    _apply(place="新潟", inner="外", turf="芝", distance="1600", new_distance="16-18")
    _apply(place="新潟", inner="外", turf="芝", distance="1800", new_distance="16-18")
    _apply(place="新潟", inner="内", turf="芝", distance="2000", new_distance="20-24")
    _apply(place="新潟", inner="内", turf="芝", distance="2200", new_distance="20-24")
    _apply(place="新潟", inner="内", turf="芝", distance="2400", new_distance="20-24")
    _apply(place="新潟", inner="外", turf="芝", distance="3000", new_distance="30-32")
    _apply(place="新潟", inner="外", turf="芝", distance="3200", new_distance="30-32")

    # 中山
    _apply(place="中山", turf="ダ", distance="2400", new_distance="24-25")
    _apply(place="中山", turf="ダ", distance="2500", new_distance="24-25")

    # 東京
    _apply(place="東京", turf="芝", distance="1600", new_distance="16-18")
    _apply(place="東京", turf="芝", distance="1800", new_distance="16-18")
    _apply(place="東京", turf="芝", distance="2300", new_distance="23-24")
    _apply(place="東京", turf="芝", distance="2400", new_distance="23-24")
    _apply(place="東京", turf="ダ", distance="1300", new_distance="13-14")
    _apply(place="東京", turf="ダ", distance="1400", new_distance="13-14")

    # 中京（芝はコメントアウト）
    # _apply(place="中京", turf="芝", distance="1200", new_distance="12-14")
    # _apply(place="中京", turf="芝", distance="1400", new_distance="12-14")
    _apply(place="中京", turf="ダ", distance="1800", new_distance="18-19")
    _apply(place="中京", turf="ダ", distance="1900", new_distance="18-19")

    # 京都（内外統合）
    inner_col = _inner_col(df)
    _apply(place="京都", inner="内", turf="芝", distance="1400", new_distance="14-16")
    _apply(place="京都", inner="内", turf="芝", distance="1600", new_distance="14-16")
    _apply(place="京都", inner="外", turf="芝", distance="1400", new_distance="14-18")
    _apply(place="京都", inner="外", turf="芝", distance="1600", new_distance="14-18")
    _apply(place="京都", inner="外", turf="芝", distance="1800", new_distance="14-18")
    _apply(place="京都", inner="内", turf="芝", distance="2000", new_distance="2000", new_inner="内外")
    _apply(place="京都", inner="外", turf="芝", distance="2000", new_distance="2000", new_inner="内外")
    _apply(place="京都", inner="外", turf="芝", distance="2200", new_distance="22-24")
    _apply(place="京都", inner="外", turf="芝", distance="2400", new_distance="22-24")
    _apply(place="京都", inner="外", turf="芝", distance="3000", new_distance="30-32")
    _apply(place="京都", inner="外", turf="芝", distance="3200", new_distance="30-32")
    _apply(place="京都", turf="ダ", distance="1800", new_distance="18-19")
    _apply(place="京都", turf="ダ", distance="1900", new_distance="18-19")

    # 阪神
    _apply(place="阪神", turf="芝", distance="1600", new_distance="16-18")
    _apply(place="阪神", turf="芝", distance="1800", new_distance="16-18")
    _apply(place="阪神", turf="芝", distance="2400", new_distance="24-26")
    _apply(place="阪神", turf="芝", distance="2600", new_distance="24-26")
    _apply(place="阪神", turf="芝", distance="3000", new_distance="30-32")
    _apply(place="阪神", turf="芝", distance="3200", new_distance="30-32")

    # 小倉
    _apply(place="小倉", turf="芝", distance="1700", new_distance="17-18")
    _apply(place="小倉", turf="芝", distance="1800", new_distance="17-18")

    return df

# =========================================
# “低いほど良い” & ZI特例 を含む偏差値付与（ver2相当）
# =========================================

def _normalize_fname_for_metric(fname: str) -> str:
    """
    反転対象判定のためにファイル名を正規化する:
    - '3F' / '３F' を除去
    - 全角→半角のばらつきを吸収（簡易）
    - スペース除去
    """
    s = str(fname)
    s = s.replace("３F", "").replace("3F", "").replace("３Ｆ", "").replace("3Ｆ", "")
    s = s.replace("　", "").replace(" ", "")
    return s

def add_hensachi(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    """
    指数をレース単位で偏差値化し、偏差値帯に分類する（堅牢版）。
    - ZI は絶対に“反転しない”
    - ZI の 0 は同レース非ゼロ平均で置換（真ん中寄せ）
    - ZI かつ クラス名=新馬 は偏差値を付与しない（NaN）
    - “低いほど良い”（タイムM/想定前半/想定後半/展開）は符号反転してから標準化
    """

    # === 前提列の確認・補助列の用意 ===
    idx_col = INDEX_COL            # "指数"
    ridn    = RIDN_COL             # "レースID(新/馬番無)"

    if idx_col not in df.columns:
        df["偏差値"] = np.nan
        df["偏差値帯"] = ""
        return df

    # 新馬判定列を必ず作成
    if "_is_shinba" not in df.columns:
        if CLASS_COL in df.columns:
            df["_is_shinba"] = df[CLASS_COL].astype(str).str.contains("新馬", na=False)
        else:
            df["_is_shinba"] = False

    # レースキー（馬番なし）が無い場合のフォールバック
    if ridn not in df.columns:
        if RID_COL in df.columns:
            df[ridn] = df[RID_COL].astype(str).str.slice(0, -2)
        else:
            # どうにも無ければ全体1グループで妥協
            df[ridn] = "ALL"

    # === ZI特例・反転判定 ===
    import unicodedata
    fname = os.path.basename(str(filename))
    norm  = unicodedata.normalize("NFKC", _normalize_fname_for_metric(fname)).upper()
    zi_mode = ("ZI" in norm)  # ZIは絶対に反転しない

    lower_is_better_keys = ["タイムM","想定前半","想定後半","展開"]
    # ファイル名に上記語が含まれていて、かつ ZI でないなら反転対象
    is_reverse_target = (not zi_mode) and any(k in norm for k in lower_is_better_keys)

    # === 数値化 & 特例処理 ===
    x = pd.to_numeric(df[idx_col], errors="coerce")

    # ZI: 0 を同レース非ゼロ平均で置換（非ゼロがなければ NaN）
    if zi_mode:
        def rep0(g):
            arr = g.values.astype(float)
            nz = arr[(~np.isnan(arr)) & (arr != 0)]
            if nz.size > 0:
                rep = nz.mean()
                return pd.Series(np.where((arr == 0) | np.isnan(arr), rep, arr), index=g.index)
            else:
                return pd.Series(np.full_like(arr, np.nan, dtype=float), index=g.index)
        x = x.groupby(df[ridn]).transform(rep0)

    # “低いほど良い”は反転（ただし ZI は絶対に反転しない）
    if is_reverse_target:
        x = -x

    df["_index_num"] = x

    # === 偏差値 ===
    vals  = df["_index_num"]
    valid = vals.groupby(df[ridn]).transform(lambda s: s.notna().sum())
    m     = vals.groupby(df[ridn]).transform("mean")
    s     = vals.groupby(df[ridn]).transform("std", ddof=0)

    hensachi = pd.Series(np.nan, index=df.index, dtype="float64")
    std_ok   = (s > 0) & (~s.isna()) & (valid >= 2)   # 有効<2 or sd=0 は付けない
    hensachi[std_ok] = (vals[std_ok] - m[std_ok]) / s[std_ok] * 10 + 50

    # ZI: 新馬は NaN
    if zi_mode:
        hensachi = hensachi.mask(df["_is_shinba"], np.nan)

    # （残す方）df["偏差値"] 代入の“後”に1回だけ
    df["偏差値"] = hensachi.round(0)
    if zi_mode or _metric_excludes_shinba_from_filename(filename):
        df["偏差値"] = df["偏差値"].mask(df["_is_shinba"], np.nan)

    # === 偏差値帯 ===
    def _to_band(v):
        if pd.isna(v): return ""
        v = float(v)
        if v >= 71:   return "71以上"
        if v >= 66:   return "66~70"
        if v >= 61:   return "61~65"
        if v >= 51:   return "51~60"
        if v >= 41:   return "41~50"
        return "40以下"

    df["偏差値帯"] = df["偏差値"].map(_to_band)
    return df

def make_rankband_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    指数順位(整数)をもとに、各行を“該当する順位帯”ごとに増殖する。
    例：順位=2 → 「1~2位」「1~3位」「1~4位」「1~5位」「2~3位」「2~4位」「2~5位」
    """
    if RANK_COL not in df.columns:
        return df.assign(**{RANK_BAND_COL: ""})  # 安全策

    # まず順位を整数化（小数点が付いてしまう問題の保険）
    def _to_int(v):
        try:
            return int(float(str(v)))
        except Exception:
            return None

    ranks = df[RANK_COL].map(_to_int)
    base  = df.copy()
    base[RANK_COL] = ranks

    out_frames = []
    for band_name, pred in RANK_BANDS:
        mask = base[RANK_COL].map(lambda r: pred(r) if r is not None else False)
        if mask.any():
            tmp = base.loc[mask].copy()
            tmp[RANK_BAND_COL] = band_name
            out_frames.append(tmp)

    if not out_frames:
        # どの帯にも該当しない場合でも空列を持たせて返す
        return base.assign(**{RANK_BAND_COL: ""})
    return pd.concat(out_frames, axis=0, ignore_index=True)


# =========================================
# （１）結合データの先頭を統一：場所×芝・ダ×距離×(内・外)
# =========================================
def build_joined(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    inner_col = _inner_col(df)

    def col(name: str) -> pd.Series:
        # 存在しない列は df.index に合わせた空文字列の Series を返す
        if name in df.columns:
            return df[name].astype(str)
        else:
            return pd.Series([""] * len(df), index=df.index)

    parts = []
    # 先頭は共通：場所×芝・ダ×距離×(内・外)
    parts += [col(PLACE_COL), col(SURFACE_COL), col(DIST_COL)]
    if inner_col:
        parts.append(col(inner_col))

    # モード別の後段
    if mode == "normal":
        parts += [col(WEIGHT_COL), col(CLASS_COL), col(RANK_COL)]
    elif mode == "rankB":
        parts += [col("枠番グループ"), col(RANK_COL)]
    elif mode == "hensachiA":
        parts += [col(WEIGHT_COL), col(CLASS_COL), col("偏差値帯")]
    elif mode == "hensachiB":
        parts += [col("枠番グループ"), col("偏差値帯")]
    elif mode == "rankJ":
        parts += [col(JOCKEY_COL), col(RANK_COL)]
    elif mode == "hensachiJ":
        parts += [col(JOCKEY_COL), col("偏差値帯")]
    elif mode == "rankBJ":
        parts += [col("枠番グループ"), col(JOCKEY_COL), col(RANK_COL)]
    elif mode == "hensachiBJ":
        parts += [col("枠番グループ"), col(JOCKEY_COL), col("偏差値帯")]
    elif mode == "normalJ":
        parts += [col(WEIGHT_COL), col(CLASS_COL), col(JOCKEY_COL), col(RANK_COL)]
    elif mode == "hensachiAJ":
        parts += [col(WEIGHT_COL), col(CLASS_COL), col(JOCKEY_COL), col("偏差値帯")]
    elif mode == "normal_band":
        parts += [col(WEIGHT_COL), col(CLASS_COL), col(RANK_BAND_COL)]
    elif mode == "rankB_band":
        parts += [col("枠番グループ"), col(RANK_BAND_COL)]
    elif mode == "rankJ_band":
        parts += [col(JOCKEY_COL), col(RANK_BAND_COL)]
    elif mode == "rankBJ_band":
        parts += [col("枠番グループ"), col(JOCKEY_COL), col(RANK_BAND_COL)]
    elif mode == "normalJ_band":  # ★ここを追加
        parts += [col(WEIGHT_COL), col(CLASS_COL), col(JOCKEY_COL), col(RANK_BAND_COL)]
    else:
        raise ValueError(f"unknown mode: {mode}")

    # ★ df.index に揃えた空文字 Series から連結（インデックス整列を避ける）
    joined = pd.Series("", index=df.index)
    for p in parts:
        joined = joined + "×" + p
    joined = joined.str.lstrip("×").str.replace("××", "×", regex=False).str.rstrip("×")

    # RID 列（存在しない場合も index 揃え）
    rid_series = df[RID_COL].astype(str) if RID_COL in df.columns else pd.Series([""] * len(df), index=df.index)

    # ▼ここを書き換え
    data = {RID_COL: rid_series, "結合データ": joined}
    if RANK_BAND_COL in df.columns:  # 帯展開時に必ず載せる
        data[RANK_BAND_COL] = df[RANK_BAND_COL].astype(str)

    # out = pd.DataFrame({ RID_COL: rid_series, "結合データ": joined })
    out = pd.DataFrame(data)
    return out.fillna("")

# =========================================
# （３）集計に必要な per-row 回収寄与列を付与
# =========================================

def add_return_contrib(df: pd.DataFrame) -> pd.DataFrame:
    # 着順 → 1=勝ち, 2/3=複勝内、取消/除外は除外（NaN）、失格/中止は最下位相当（99999）
    def _rank_to_int(x):
        s = str(x)
        if re.search(r"(取消|除外)", s):
            return np.nan   # 分母から除外
        if re.search(r"(失格|中止)", s):
            return 99999    # 最下位相当（分母に含める前提は集計側で）
        try:
            return int(float(s))
        except Exception:
            return np.nan

    r = df.get(FIN_COL)
    if r is None:
        return df  # 元ファイルに着順が無ければ何もしない

    # 数値化（NaN, 99999 を保持）
    ranki = r.map(_rank_to_int)
    ranki = pd.to_numeric(ranki, errors="coerce")  # object → float に統一

    # 的中フラグ（NaNはFalse）
    win_hit = ranki.eq(1).fillna(False)         # 単勝的中：1着
    plc_hit = ranki.le(3).fillna(False)         # 複勝的中：1～3着

    # 配当（数値化して欠損0）
    pay_win = pd.to_numeric(df.get(PAY_WIN_COL, 0), errors="coerce").fillna(0)
    pay_plc = pd.to_numeric(df.get(PAY_PLC_COL, 0), errors="coerce").fillna(0)

    # 文字列で保持（後段CSV出力との整合）
    df["単勝的中"]     = win_hit.astype(int).astype(str)              # "1"/"0"
    df["複勝的中"]     = plc_hit.astype(int).astype(str)
    df["単勝回収寄与"] = np.where(win_hit, pay_win, 0).astype(int).astype(str)
    df["複勝回収寄与"] = np.where(plc_hit, pay_plc, 0).astype(int).astype(str)
    return df



# =========================================
# foragg 出力（２）コース4列を明示的に同梱
# =========================================

# ==== 集計スクリプト（aggregate_stats_ver2.py）が期待する列仕様に揃える ====

# 集計に必須の列（これだけは必ず出す）
AGG_MANDATORY_COLS = [
    "レースID(新)", "結合データ", "着順", "単勝オッズ", "単勝配当", "複勝配当"
]

# あれば併記したい任意列（存在チェックしてから採用）
AGG_OPTIONAL_COLS = [
    # コース分解（フィルタ用）
    "場所", "芝・ダ", "距離", "芝(内・外)", "芝(内外)"
    # 重複排除・平均着順精度の担保に役立つ列（存在すれば取り込む）
    "馬番","馬名","馬名S","頭数","頭"
]

def _ensure_columns_exist(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """指定列が無ければ空列で補う（型を文字列化する前の段階で呼ぶ）"""
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df


def export_foragg(src_df: pd.DataFrame, joined_df: pd.DataFrame, filename: str, tag: str) -> None:
    """
    foragg_*.csv を「aggregate_stats_ver2.py が想定する列名」で出力する。
    - band 展開時の多対多直積を避けるため、結合キーに「指数順位帯」を含める
    - ZI の新馬は素材から除外（_is_shinba=True）※ joined_df 側は reindex で安全に除外
    - SJIS 安全化して Shift_JIS で保存
    """
    base = os.path.basename(str(filename))

    # --- 新馬行の除外対象（ZI と 吉馬6指数） ---
    if _metric_excludes_shinba_from_filename(base) and ("_is_shinba" in src_df.columns):
        mask_src = ~src_df["_is_shinba"].fillna(False)
        src_df = src_df.loc[mask_src].copy()
        mask_joined = mask_src.reindex(joined_df.index).fillna(True)
        joined_df = joined_df.loc[mask_joined].copy()

    # --- 結合データの軽整形 ---
    if "結合データ" in joined_df.columns:
        joined_df["結合データ"] = (
            joined_df["結合データ"].astype(str)
            .str.replace("××", "×", regex=False)
            .str.rstrip("×")
        )

    # --- band 対応：結合キーの決定 ---
    merge_keys = [RID_COL]  # "レースID(新)"
    use_band_key = (RANK_BAND_COL in src_df.columns) and (RANK_BAND_COL in joined_df.columns)
    if use_band_key:
        merge_keys.append(RANK_BAND_COL)

    # joined 側の出力列
    out_cols = [RID_COL, "結合データ"] + ([RANK_BAND_COL] if use_band_key else [])
    out = joined_df[out_cols].copy()

    # 元データ側（rename 等があればここで）
    base_df = src_df.copy()

    # 必須列を空列で補完
    base_df = _ensure_columns_exist(base_df, AGG_MANDATORY_COLS)

    # 取り込み列：キー + 必須 + 任意
    pick_cols = [c for c in AGG_MANDATORY_COLS if c not in ("レースID(新)", "結合データ")]
    opt_cols  = [c for c in AGG_OPTIONAL_COLS if c in base_df.columns]
    base_pick = list(dict.fromkeys(merge_keys + pick_cols + opt_cols))  # 重複排除

    # --- merge（多対多直積を防ぐキー設計）---
    merged = out.merge(
        base_df[base_pick],
        on=merge_keys, how="left", copy=False
    )

    # NaN → 空文字、型を文字列化
    merged = merged.fillna("")
    for c in merged.columns:
        merged[c] = merged[c].astype(str)

    # SJIS 安全化＆保存（foragg_* は集計素材/最終成果物の置き場）
    os.makedirs(OUT_FORAGG_DIR, exist_ok=True)
    out_path = os.path.join(OUT_FORAGG_DIR, f"foragg_{tag}_{base}")
    sanitize_df_for_sjis(merged).to_csv(out_path, index=False, encoding="shift_jis")

# =========================================
# processed_* 出力（既存）
# =========================================

def export_processed(src_df: pd.DataFrame, joined_df: pd.DataFrame, filename: str) -> None:
    """
    processed_*（置換結果の可視確認用ファイル）を出力する。
    - ZI ファイルの場合は「新馬（_is_shinba）」の行を丸ごと除外する。
    - 結合データの "××" 縮約、末尾 "×" 除去を行う。
    - Shift_JIS に安全な形で保存する。
    """
    base = os.path.basename(str(filename))

    # --- 新馬行の除外対象（ZI と 吉馬6指数） ---  ※これ1つに統一
    if _metric_excludes_shinba_from_filename(base) and ("_is_shinba" in src_df.columns):
        mask = ~src_df["_is_shinba"].fillna(False)
        mask = mask.reindex(joined_df.index).fillna(True)  # ← 安全に揃える
        joined_df = joined_df.loc[mask].copy()

    # 結合データの軽い整形
    if "結合データ" in joined_df.columns:
        joined_df["結合データ"] = (
            joined_df["結合データ"].astype(str)
            .str.replace("××", "×", regex=False)
            .str.rstrip("×")
        )

    # 保存（SJIS 安全化）
    os.makedirs(OUT_PROC_DIR, exist_ok=True)
    out = sanitize_df_for_sjis(joined_df.fillna(""))
    out_path = os.path.join(OUT_PROC_DIR, f"processed_{base}")
    out.to_csv(out_path, index=False, encoding="shift_jis")

# =========================================
# メイン処理（ver2 構成を維持）
# =========================================
MODES = [
    ("normal",     "normal"),
    ("rankB",      "rankB"),
    ("hensachiA",  "hensachiA"),
    ("hensachiB",  "hensachiB"),
    ("rankJ",      "rankJ"),
    ("hensachiJ",  "hensachiJ"),
    ("rankBJ",     "rankBJ"),
    ("hensachiBJ", "hensachiBJ"),
    ("normalJ",    "normalJ"),
    ("hensachiAJ", "hensachiAJ"),
]
# 帯は別ループで処理する
# build_joined() 側には normalJ_band があるので、出力にも含める
BAND_MODES = ["normal_band", "rankB_band", "rankJ_band", "rankBJ_band", "normalJ_band"]

def process_one_file(file_path: str):
    # 入力
    df = smart_read_csv(file_path)

    # 基本クリーン
    df = basic_clean(df)

    # 距離まとめ
    df = apply_place_distance_rules(df)

    # ★ 追加：指数欠損ルール（2頭以上欠損レースを丸ごと除外、1頭欠損は順位空＆偏差値から除外）
    df = apply_index_missing_policy(df)

    # 偏差値
    df = add_hensachi(df, filename=file_path)

    # 頭数一桁のレースは除外
    if HEAD_COL in df.columns:
        def _ok(h):
            try:
                return int(float(str(h))) >= 10
            except Exception:
                return False
        df = df[df[HEAD_COL].map(_ok)]

    # per-row 回収寄与
    df = add_return_contrib(df)

    base = os.path.basename(file_path)

    # === 単勝オッズ（数値化；無ければ全NaN） ===
    odds_num = pd.to_numeric(df.get(ODDS_WIN_COL, ""), errors="coerce")

    # ---------- 非band（10パターン） ----------
    for mode, tag in MODES:
        joined = build_joined(df, mode)
        export_foragg(df, joined, base, tag)
        export_processed(df, joined, f"{tag}_{base}")  # デバッグ用

        # ★ フィルタ版（odds10u 等）を追加出力
        for prefix, pred in ODDS_FILTERS:
            sub = df[pred(odds_num)].copy()
            if not sub.empty:
                joined_sub = build_joined(sub, mode)
                export_foragg(sub, joined_sub, base, f"{prefix}{tag}")
                export_processed(sub, joined_sub, f"{prefix}{tag}_{base}")

    # 代表 processed（normal）
    joined_normal = build_joined(df, "normal")
    export_processed(df, joined_normal, base)

    # # ---------- band（4パターン） ----------
    # df_band = make_rankband_df(df)
    # odds_num_band = pd.to_numeric(df_band.get(ODDS_WIN_COL, ""), errors="coerce")

    # for tag in BAND_MODES:
    #     joined_band = build_joined(df_band, tag)
    #     export_foragg(df_band, joined_band, base, tag)
    #     export_processed(df_band, joined_band, f"{tag}_{base}")

    #     # ★ フィルタ版（odds10u 等）を追加出力
    #     for prefix, pred in ODDS_FILTERS:
    #         sub_band = df_band[pred(odds_num_band)].copy()
    #         if not sub_band.empty:
    #             joined_sub_band = build_joined(sub_band, tag)
    #             export_foragg(sub_band, joined_sub_band, base, f"{prefix}{tag}")
    #             export_processed(sub_band, joined_sub_band, f"{prefix}{tag}_{base}")

    # ---------- band（固定帯：s1/s2） ----------
    # 有効スキームは環境変数で切替（例: "s1", "s2", "s1,s2"）。未指定は両方。
    _scheme_env = os.environ.get("RANKBAND_SCHEMES", "s1,s2")
    ACTIVE_RANKBAND_SCHEMES = []
    for token in [t.strip() for t in _scheme_env.split(",") if t.strip()]:
        if token == "s1":
            ACTIVE_RANKBAND_SCHEMES.append("rankband_s1")
        elif token == "s2":
            ACTIVE_RANKBAND_SCHEMES.append("rankband_s2")

    if not ACTIVE_RANKBAND_SCHEMES:
        ACTIVE_RANKBAND_SCHEMES = ["rankband_s1", "rankband_s2"]

    for scheme_name in ACTIVE_RANKBAND_SCHEMES:
        df_band = assign_fixed_rankband(df, scheme_name)
        odds_num_band = pd.to_numeric(df_band.get(ODDS_WIN_COL, ""), errors="coerce")

        for tag in BAND_MODES:
            # テーブル名に “_fam_” トークンを残すため、prefix側に scheme を付与
            prefixed_tag = f"{scheme_name}_{tag}"  # ex) rankband_s1_normal_band
            joined_band = build_joined(df_band, tag)

            export_foragg(df_band, joined_band, base, prefixed_tag)
            export_processed(df_band, joined_band, f"{prefixed_tag}_{base}")

            # フィルタ版（odds10u 等）も同様に prefix を維持
            for prefix, pred in ODDS_FILTERS:
                sub_band = df_band[pred(odds_num_band)].copy()
                if not sub_band.empty:
                    joined_sub_band = build_joined(sub_band, tag)
                    export_foragg(sub_band, joined_sub_band, base, f"{prefix}{prefixed_tag}")
                    export_processed(sub_band, joined_sub_band, f"{prefix}{prefixed_tag}_{base}")

def main():
    for name in sorted(os.listdir(CSV_DIR)):
        if not name.lower().endswith(".csv"):
            continue
        src_path = os.path.join(CSV_DIR, name)
        process_one_file(src_path)

if __name__ == "__main__":
    main()