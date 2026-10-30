# build_db.py  — entries_* から 10→14パターン×指数ベースの結合キーを前展開し、unified_all と突合した結果を保存
import os, glob, re, sqlite3, argparse
import pandas as pd
import numpy as np

BASE_DIR      = "/app"
FORAGG_DIR    = os.path.join(BASE_DIR, "foragg_csv")
ENTRIES_DIR   = os.path.join(BASE_DIR, "entries_out")
DB_PATH       = os.path.join(BASE_DIR, "racing.db")

# -------------------------
# 既存 foragg_* 用の共通定義（既存踏襲）
# -------------------------
COMMON_COLS = [
    "場所","芝・ダ","距離","芝(内・外)","結合データ",
    "件数","勝率","連対率","複勝率","2着率","3着率",
    "掲示板率",
    "平均着順","単勝回収値","複勝回収値","単勝適正回収値",
    "提案勝率","提案連対率","提案複勝率","提案2着率","提案3着率",
    "勝率_val","連対率_val","複勝率_val","2着率_val","3着率_val",
    "掲示板率_val",
]
INDEX_COLS = [
    "場所","芝・ダ","距離","芝(内・外)","芝(内外)","結合データ",
    "勝率_val","連対率_val","複勝率_val","2着率_val","3着率_val",
    "掲示板率_val",
]

# -------------------------
# entries_* 用の正規化パラメータ（既存踏襲）
# -------------------------
PLACE_COL   = "場所"
SURF_COL    = "芝・ダ"        # （calc: 芝ダ）
DIST_COL    = "距離"
INOUT_COL   = "芝(内・外)"     # （calc: 外回り → 京都/新潟のみ「内/外」埋め）
CLASS_COL   = "クラス名"       # （calc: クラス）
HEAD_COL    = "頭数"          # （calc: 頭）
WEIGHT_COL  = "重量種別"       # 生成（calcには無い）
RACE_NAME   = "レース名"       # "Ｈ"/"H" でハンデ判定
AGE_LIMIT   = "年限定"         # "上" を含まなければ馬齢

ENTRIES_INDEX_COLS = [
    "場所","芝・ダ","距離","芝(内・外)","芝(内外)","結合データ",
    "レースID(新/馬番無)","場 R","馬番"
]

# ========= ユーティリティ =========
def _norm(s):
    if s is None: return ""
    t = str(s).strip()
    if t in ("None","none","NaN","nan"): return ""
    return t

NAME_CAND = ("馬名","馬名S","馬名1","horse","出走馬名")
def _row_display_name(rowdict):
    for c in NAME_CAND:
        if c in rowdict and _norm(rowdict[c]): return _norm(rowdict[c])
    return ""

def _mk_wakuban_group_fallback(w, heads=None):
    try: w = int(str(w))
    except: return ""
    try: heads = int(str(heads))
    except: heads = None
    if heads is not None:
        if 10 <= heads <= 13:
            return "内枠" if w <= 5 else "外枠"
        elif 14 <= heads <= 16:
            return "内枠" if w <= 4 else "外枠"
        elif 17 <= heads <= 18:
            if w <= 3: return "内枠"
            if 4 <= w <= 6: return "中枠"
            return "外枠"
    if w <= 2: return "内枠"
    if 3 <= w <= 6: return "中枠"
    return "外枠"

def _key_join(*parts):
    out = [str(x) for x in parts if _norm(x)!=""]
    return "×".join(out)

IND_ALIASES = {
    "GoldAirplane": ["GoldAirplane","Gold","Gold指数","Gold_Airplane"],
    "Gold": ["Gold","GoldAirplane","Gold指数","Gold_Airplane"],
    "BC": ["BC","BCindex","BCIndex"],
    "ST指数": ["ST指数","ST"],
    "展開": ["展開","展開力"],
    "想定前半": ["想定前半","想定前半3F","想定前半３F"],
    "想定後半": ["想定後半","想定後半3F","想定後半３F"],
    "タイムM": ["タイムM","マイニング値","タイム指数","TimeM"],
    "対戦M": ["対戦M","対戦型マイニング値","MatchM"],
    "合計値": ["合計値","Total","総合"],
}
def _indicator_candidates(base):
    b = re.sub(r"\s+", "", str(base or "")).strip("_")
    cands = set([b])
    if b in IND_ALIASES:
        cands.update(IND_ALIASES[b])
    return list(cands)

def _normalize_band_token(s):
    s = _norm(s)
    if not s: return []
    dash = re.sub(r"[~〜－–—‐\u2212]", "-", s)
    tilde = re.sub(r"[-〜－–—‐\u2212]", "~", s)
    wave = re.sub(r"[-~－–—‐\u2212]", "〜", s)
    return list(dict.fromkeys([s, dash, tilde, wave]))

def _normalize_rank_variants(s):
    """
    入力例: '1', '1.0', '1位', ' 1 位 ', など
    出力例: ['1', '1位', '1.0'] など（順序は安定化）
    """
    s0 = _norm(s)
    if not s0:
        return []
    s0 = s0.replace(" ", "").replace("\u3000", "")
    # まず '位' を除いた数値を抽出
    s_num = re.sub(r'位$', '', s0)
    variants = []
    try:
        i = int(float(s_num))
        # 最低限そろえる2形
        variants.extend([str(i), f"{i}位"])
        # 元の表記も残す（foraggが '3.0' を使っていた場合に保険）
        if s0 not in variants:
            variants.append(s0)
        # 元が小数表記だった場合の 'i.0' も追加しておく
        if re.search(r'\.\d+$', s_num):
            f = str(float(s_num))
            if f not in variants: variants.append(f)
    except:
        # 数値化できなければ、'位' 付け/無しの両方を候補化
        base = s_num
        for v in [base, base + "位", s0]:
            v = v.strip()
            if v and v not in variants:
                variants.append(v)
    # 重複除去の上で返却
    out = []
    for v in variants:
        if v and v not in out:
            out.append(v)
    return out

def _normalize_rank_band_token(s):
    """
    '1~2位' / '1-2位' / '1〜2位' / '1~2' / '1-2' / '1〜2' を相互に候補化。
    前後の空白は除去。
    """
    s = _norm(s)
    if not s: return []
    s = re.sub(r"\s+", "", s)
    base = re.sub(r"[~〜－–—‐\u2212]", "~", s)  # 統一を '~' に
    if not base.endswith("位"):
        base_with_i = base + "位"
    else:
        base_with_i = base
    out = set()
    for sym in ["~","-","〜"]:
        v = base_with_i.replace("~", sym)
        out.add(v)
        out.add(v.rstrip("位"))
    return list(out)

def _rank_to_band(rank_val):
    """
    数値rankから、保守的な帯を作る（必要に応じて閾値は調整可）
    1-2, 3-5, 6-9, 10~ とする。
    """
    try:
        r = int(float(str(rank_val)))
    except:
        return ""
    if r <= 2:  return "1~2位"
    if r <= 5:  return "3~5位"
    if r <= 9:  return "6~9位"
    return "10位~"

def _collect_indicator_bases_from_cols(cols_iterable):
    cols = list(cols_iterable)
    bases = []
    for c in cols:
        if c.endswith("_偏差値"):
            base = c[:-4]
            if (base + "_順位") in cols:
                bases.append(base)
    return sorted(set(bases))

def _coalesce_duplicate_columns_inplace(df: pd.DataFrame, name: str):
    """同名列が複数ある場合、左から順に非空(非NaN/非"")を残して結合し、右側をdrop"""
    cols = [c for c in df.columns if c == name]
    if len(cols) <= 1:
        return
    base = cols[0]
    for c in cols[1:]:
        left = df[base]
        right = df[c]
        if isinstance(left, pd.DataFrame):
            left = left.iloc[:, 0]
        if isinstance(right, pd.DataFrame):
            right = right.iloc[:, 0]
        df[base] = left.where(left.notna() & (left.astype(str) != ""), right)
        df.drop(columns=[c], inplace=True)

def _safe_rename_or_coalesce(df: pd.DataFrame, src: str, dst: str):
    """
    src->dst へ統合。dstが既にあるなら coalesce して src を削除。
    無ければ rename。どちらも無ければ何もしない。
    """
    if src not in df.columns:
        return
    if dst in df.columns:
        # dst ←(空だけ) src を埋め、src は削除
        s_dst = df[dst]; s_src = df[src]
        if isinstance(s_dst, pd.DataFrame): s_dst = s_dst.iloc[:, 0]
        if isinstance(s_src, pd.DataFrame): s_src = s_src.iloc[:, 0]
        df[dst] = s_dst.where(s_dst.notna() & (s_dst.astype(str) != ""), s_src)
        df.drop(columns=[src], inplace=True)
    else:
        df.rename(columns={src: dst}, inplace=True)

def _col_as_series(df: pd.DataFrame, name: str) -> pd.Series:
    """同名複数列でも必ず Series を返す（左端列を採用）"""
    if name not in df.columns:
        return pd.Series(index=df.index, dtype=object)
    obj = df[name]
    return obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj


def sanitize(name: str) -> str:
    base = os.path.splitext(os.path.basename(name))[0]
    s = re.sub(r"[^\w]", "_", base)
    if re.match(r"^\d", s):
        s = "t_" + s
    return s

def extract_bayes_percent(series: pd.Series) -> pd.Series:
    s = series.astype(str)
    s = s.str.replace("（", "(", regex=False).str.replace("）", ")", regex=False)
    m = s.str.extract(r'^\s*([\d.]+)%\s*(?:\(\s*([\d.]+)%\s*\))?')
    val = m[1].where(m[1].notna(), m[0])
    return pd.to_numeric(val, errors="coerce")

# def _extract_odds_tag(tbl: str) -> str:
#     s = str(tbl or "")
#     if "_odds10u_" in s: return "odds10u"
#     if "_odds5p_"  in s: return "odds5p"
#     return "normal"

def _extract_odds_tag(tbl: str) -> str:
    s = str(tbl or "")
    if "_odds10u_" in s: return "odds10u"
    if "_odds5p_"  in s: return "odds5p"
    if "_odds10_50_" in s: return "odds10_50" # ★追加
    return "normal"

# ====== 距離レンジ置換（既存踏襲） ======
def apply_place_distance_rules_inplace(df: pd.DataFrame):
    def _apply(place=None, turf=None, distance=None, inner=None,
               new_turf=None, new_distance=None, new_inner=None):
        cond = pd.Series(True, index=df.index)
        if place is not None and PLACE_COL in df.columns:
            cond &= (df[PLACE_COL] == place)
        if turf is not None and SURF_COL in df.columns:
            cond &= (df[SURF_COL] == turf)
        if distance is not None and DIST_COL in df.columns:
            cond &= (df[DIST_COL].astype(str) == str(distance))
        if inner is not None and INOUT_COL in df.columns:
            cond &= (df[INOUT_COL] == inner)
        if new_turf is not None and SURF_COL in df.columns:
            df.loc[cond, SURF_COL] = new_turf
        if new_distance is not None and DIST_COL in df.columns:
            df.loc[cond, DIST_COL] = new_distance
        if new_inner is not None and INOUT_COL in df.columns:
            df.loc[cond, INOUT_COL] = new_inner

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
    # 中京（ダのみ）
    _apply(place="中京", turf="ダ", distance="1800", new_distance="18-19")
    _apply(place="中京", turf="ダ", distance="1900", new_distance="18-19")
    # 京都（内外統合＆レンジ統一）
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
    # 阪神（芝のみ）
    _apply(place="阪神", turf="芝", distance="1600", new_distance="16-18")
    _apply(place="阪神", turf="芝", distance="1800", new_distance="16-18")
    _apply(place="阪神", turf="芝", distance="2400", new_distance="24-26")
    _apply(place="阪神", turf="芝", distance="2600", new_distance="24-26")
    _apply(place="阪神", turf="芝", distance="3000", new_distance="30-32")
    _apply(place="阪神", turf="芝", distance="3200", new_distance="30-32")
    # 小倉（芝のみ）
    _apply(place="小倉", turf="芝", distance="1700", new_distance="17-18")
    _apply(place="小倉", turf="芝", distance="1800", new_distance="17-18")

# ====== entries_* の正規化（既存踏襲＋微修正） ======
def normalize_entries_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # --- まず index 重複を除去（align/reindex の例外回避）---
    if df.index.has_duplicates:
        df.reset_index(drop=True, inplace=True)

    # --- 値の前処理（既存・そのまま）---
    for c in df.columns:
        df[c] = df[c].map(lambda x: str(x).strip().replace("　", "") if pd.notna(x) else "")

    # --- 同義列の安全統合（重複名を作らない）---
    _safe_rename_or_coalesce(df, "芝ダ", "芝・ダ")
    _safe_rename_or_coalesce(df, "頭",  "頭数")
    _safe_rename_or_coalesce(df, "クラス", "クラス名")

    # ここで主要列の“同名重複”を完全解消（merge 後の二重化対策）
    for key_col in ["場所","芝・ダ","距離","芝(内・外)","芝(内外)","クラス名","頭数",
                    "レースID(新/馬番無)","レースID(新)","場 R","枠番","枠","騎手","馬名"]:
        if key_col in df.columns:
            _coalesce_duplicate_columns_inplace(df, key_col)

    if "距離" in df.columns and "距離_raw" not in df.columns:
        df["距離_raw"] = df["距離"]
    if "外回り" in df.columns:
        def _inner_from_outer(row):
            place = row.get(PLACE_COL, "")
            val   = row.get("外回り", "")
            if place in ("京都", "新潟"):
                return "外" if str(val) == "外" else "内"
            return ""
        df[INOUT_COL] = df.apply(_inner_from_outer, axis=1)
    else:
        if INOUT_COL not in df.columns:
            df[INOUT_COL] = ""
    # ★ ここに挿入（重複名の掃除）
    if INOUT_COL in df.columns:
        _coalesce_duplicate_columns_inplace(df, INOUT_COL)
    if CLASS_COL in df.columns:
        df[CLASS_COL] = df[CLASS_COL].replace({
            "500万": "1・2_勝", "1000万": "1・2_勝", "1勝": "1・2_勝", "2勝": "1・2_勝",
            "1600万": "準OP・OP", "3勝": "準OP・OP", "ｵｰﾌﾟﾝ": "準OP・OP", "OP(L)": "準OP・OP",
            "Ｇ２": "重賞", "Ｇ３": "重賞"
        })
    if RACE_NAME in df.columns or AGE_LIMIT in df.columns:
        rn = df.get(RACE_NAME, "")
        yl = df.get(AGE_LIMIT, "")
        def _weight_type(rname, ylimit):
            rname = str(rname); ylimit = str(ylimit)
            if ("Ｈ" in rname) or ("H" in rname):
                return "ハンデ"
            elif "上" not in ylimit:
                return "馬齢"
            else:
                return "定量"
        df[WEIGHT_COL] = [ _weight_type(rn[i] if i in df.index else "", yl[i] if i in df.index else "") for i in df.index ]
        df[WEIGHT_COL] = df[WEIGHT_COL].replace({"別定": "定量"})
    else:
        if WEIGHT_COL not in df.columns:
            df[WEIGHT_COL] = ""
    if (WEIGHT_COL in df.columns) and (CLASS_COL in df.columns):
        w = _col_as_series(df, WEIGHT_COL).astype(str).to_numpy() == "馬齢"
        c = _col_as_series(df, CLASS_COL).astype(str).to_numpy()
        mask = w & np.isin(c, ["1・2_勝","準OP・OP"])
        df.loc[pd.Series(mask, index=df.index), CLASS_COL] = "1勝・OP"
    if "枠番グループ" not in df.columns:
        src_col = None
        for c in ("枠番", "枠"):
            if c in df.columns:
                src_col = c; break
        if src_col:
            df["枠番グループ"] = [
                _mk_wakuban_group_fallback(df.at[i, src_col], df.at[i, HEAD_COL] if HEAD_COL in df.columns else None)
                for i in df.index
            ]
        else:
            df["枠番グループ"] = ""
    if DIST_COL in df.columns:
        # "1600m" → "1600" に統一（レンジ置換・foragg照合の前提を揃える）
        df[DIST_COL] = df[DIST_COL].astype(str).str.replace("m", "", regex=False)
    apply_place_distance_rules_inplace(df)
    parts = [
        df.get(PLACE_COL, pd.Series([""]*len(df))).astype(str),
        df.get(SURF_COL,  pd.Series([""]*len(df))).astype(str),
        df.get(DIST_COL,  pd.Series([""]*len(df))).astype(str),
        df.get(INOUT_COL, pd.Series([""]*len(df))).astype(str),
    ]
    joined = pd.Series([""]*len(df))
    for p in parts: joined = joined + "×" + p
    df["結合データ"] = joined.str.lstrip("×").str.replace("××","×", regex=False).str.rstrip("×")
    return df

# ============== foragg_* のロード ==============
def load_foragg_tables(con, cur, foragg_dir=FORAGG_DIR):
    files = sorted(glob.glob(os.path.join(foragg_dir, "*.csv")))
    if not files:
        print("no csv in foragg_csv/ (skipped)")
        return [], set()
    loaded = []; created = set()
    for p in files:
        try:
            df = pd.read_csv(p, encoding="shift_jis")
        except Exception:
            df = pd.read_csv(p)
        for col in ["勝率","連対率","複勝率","2着率","3着率"]:
            if col in df.columns:
                df[col + "_val"] = extract_bayes_percent(df[col])
        # ▼追加（掲示板率の _val 化）
        if "掲示板率" in df.columns and "掲示板率_val" not in df.columns:
            df["掲示板率_val"] = extract_bayes_percent(df["掲示板率"])

        tname = "foragg_" + sanitize(p)
        df.to_sql(tname, con, if_exists="replace", index=False)
        created.add(tname)
        # for idx_col in INDEX_COLS + ["結合データ"]:
        #     if idx_col in df.columns:
        #         safe_idx_col = re.sub(r'[^0-9a-zA-Z_]', '_', idx_col)
        #         cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{tname}_{safe_idx_col} ON "{tname}"("{idx_col}")')

        # 先頭で定義を追加
        BAND_COL = "指数順位帯"   # ★追加

        # 取り込み時のインデックス作成
        for idx_col in INDEX_COLS + ["結合データ", BAND_COL]:  # ★BAND_COL を追加
            if idx_col in df.columns:
                safe_idx_col = re.sub(r'[^0-9a-zA-Z_]', '_', idx_col)
                cur.execute(
                    f'CREATE INDEX IF NOT EXISTS idx_{tname}_{safe_idx_col} '
                    f'ON "{tname}"("{idx_col}")'
                )

        cur.execute("INSERT OR REPLACE INTO __tables_meta__ (table_name, source_file) VALUES (?,?)",
                    (tname, os.path.basename(p)))
        loaded.append((tname, set(df.columns)))
        print(f"[foragg] loaded: {os.path.basename(p)} -> {tname} (cols={len(df.columns)})")
    return loaded, created

# ============== entries_* のロード ==============
def load_entries_tables(con, cur, entries_dir=ENTRIES_DIR):
    files = sorted(glob.glob(os.path.join(entries_dir, "*.csv")))
    if not files:
        print("no csv in entries_out/ (skipped)")
        return [], set()
    loaded = []; created = set()
    for p in files:
        df = None
        for enc in ("cp932","shift_jis","utf-8-sig","utf-8","latin1"):
            try:
                df = pd.read_csv(p, encoding=enc); break
            except Exception:
                pass
        if df is None:
            print(f"[entries] read failed: {os.path.basename(p)} (skipped)")
            continue
        df_norm = normalize_entries_df(df)
        tname = "entries_" + sanitize(p)
        df_norm.to_sql(tname, con, if_exists="replace", index=False)
        created.add(tname)
        for idx_col in ENTRIES_INDEX_COLS:
            if idx_col in df_norm.columns:
                safe_idx_col = re.sub(r'[^0-9a-zA-Z_]', '_', idx_col)
                cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{tname}_{safe_idx_col} ON "{tname}"("{idx_col}")')
        cur.execute("INSERT OR REPLACE INTO __tables_meta__ (table_name, source_file) VALUES (?,?)",
                    (tname, os.path.basename(p)))
        loaded.append((tname, set(df_norm.columns)))
        print(f"[entries] loaded: {os.path.basename(p)} -> {tname} (cols={len(df_norm.columns)})")
    return loaded, created

def rebuild_unified_all(con, cur, foragg_loaded):
    cur.execute("DROP TABLE IF EXISTS unified_all")
    cur.execute("""
        CREATE TABLE unified_all (
            "場所" TEXT,
            "芝・ダ" TEXT,
            "距離" TEXT,
            "芝(内・外)" TEXT,
            "結合データ" TEXT,
            "件数" INT,
            "勝率" TEXT,
            "連対率" TEXT,
            "複勝率" TEXT,
            "2着率" TEXT,
            "3着率" TEXT,
            "掲示板率" TEXT,
            "平均着順" REAL,
            "単勝回収値" REAL,
            "複勝回収値" REAL,
            "単勝適正回収値" REAL,
            "提案勝率" TEXT,
            "提案連対率" TEXT,
            "提案複勝率" TEXT,
            "提案2着率" TEXT,
            "提案3着率" TEXT,
            "勝率_val" REAL,
            "連対率_val" REAL,
            "複勝率_val" REAL,
            "2着率_val" REAL,
            "3着率_val" REAL,
            "掲示板率_val" REAL,
            "テーブル名" TEXT
        )
    """)
    for tname, cols in foragg_loaded:
        parts = []
        for c in COMMON_COLS:
            if c in cols:
                if c.endswith("_val"): parts.append(f'CAST("{c}" AS REAL) AS "{c}"')
                else: parts.append(f'"{c}"')
            else:
                if c.endswith("_val"): parts.append(f'CAST(NULL AS REAL) AS "{c}"')
                else: parts.append(f'NULL AS "{c}"')
        parts.append(f'"{tname}" AS "テーブル名"')
        sql = f'INSERT INTO unified_all SELECT {", ".join(parts)} FROM "{tname}"'
        cur.execute(sql)

    for idx_col in INDEX_COLS + ["結合データ","テーブル名"]:
        safe_idx_col = re.sub(r'[^0-9a-zA-Z_]', '_', idx_col)
        try:
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_unified_all_{safe_idx_col} ON unified_all("{idx_col}")')
        except sqlite3.OperationalError:
            pass
    try:
        cur.execute('CREATE INDEX IF NOT EXISTS idx_unified_all_key_tbl ON unified_all("結合データ","テーブル名")')
    except sqlite3.OperationalError:
        pass

def build_entries_all(con, cur, entries_loaded):
    cur.execute("DROP TABLE IF EXISTS entries_all")
    cur.execute("""
        CREATE TABLE entries_all (
            "レースID(新/馬番無)" TEXT,
            "レースID(新)"       TEXT,
            "場 R"               TEXT,
            "場所"               TEXT,
            "芝・ダ"             TEXT,
            "距離"               TEXT,
            "芝(内・外)"         TEXT,
            "クラス名"           TEXT,
            "重量種別"           TEXT,
            "頭数"               TEXT,
            "馬番"               TEXT,
            "馬名"               TEXT,
            "騎手"               TEXT,
            "結合データ"         TEXT
        )
    """)
    for tname, cols in entries_loaded:
        def expr(col):
            alts = {
                "馬名": ["馬名","馬名S","馬名1","出走馬名","horse"],
                "枠番": ["枠番","枠"],
                "騎手": ["騎手","騎手名","Jockey"],
                "芝(内・外)": ["芝(内・外)","芝(内外)"],
            }
            if col in alts:
                for c in alts[col]:
                    if c in cols:
                        return f'"{c}" AS "{col}"' if c != col else f'"{c}"'
                return f'NULL AS "{col}"'
            else:
                return f'"{col}"' if col in cols else f'NULL AS "{col}"'
        take = [
            expr("レースID(新/馬番無)"),
            expr("レースID(新)"),
            expr("場 R"),
            expr("場所"),
            expr("芝・ダ"),
            expr("距離"),
            expr("芝(内・外)"),
            expr("クラス名"),
            expr("重量種別"),
            expr("頭数"),
            expr("馬番"),
            expr("馬名"),
            expr("騎手"),
            expr("結合データ"),
        ]
        cur.execute(f'INSERT INTO entries_all SELECT {", ".join(take)} FROM "{tname}"')
    for col in ["場 R","レースID(新/馬番無)","レースID(新)","場所","芝・ダ","距離","芝(内・外)","結合データ","馬番","騎手","馬名"]:
        try:
            safe = re.sub(r'[^0-9a-zA-Z_]', '_', col)
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_entries_all_{safe} ON entries_all("{col}")')
        except sqlite3.OperationalError:
            pass

# ========= xjoin 高速化ユーティリティ =========
def ensure_unified_indexes(con, cur):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='unified_all'")
    if not cur.fetchone():
        print("[xjoin] WARN: unified_all が見つかりません。foragg 未取り込みか、DB未構築の可能性。xjoin はスキップします。", flush=True)
        return False

    for idx_col in INDEX_COLS + ["結合データ","テーブル名"]:
        safe = re.sub(r'[^0-9a-zA-Z_]', '_', idx_col)
        try:
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_unified_all_{safe} ON unified_all("{idx_col}")')
        except sqlite3.OperationalError:
            pass
    try:
        cur.execute('CREATE INDEX IF NOT EXISTS idx_unified_all_key_tbl ON unified_all("結合データ","テーブル名")')
    except sqlite3.OperationalError:
        pass

    con.commit()
    return True

_CAND_TABLES_CACHE = {}
def _get_candidate_tables(cur_u, fam, base):
    key = (fam, base)
    if key in _CAND_TABLES_CACHE:
        return _CAND_TABLES_CACHE[key]

    fam_token = f"_{fam}_"
    cand_tables = set()
    for cand in _indicator_candidates(base):
        # LIKEの代わりにinstrで厳密に '_' + fam + '_' トークン一致
        cur_u.execute(
            'SELECT DISTINCT "テーブル名" FROM unified_all '
            'WHERE instr("テーブル名", ?) > 0 AND instr("テーブル名", ?) > 0',
            (fam_token, cand)
        )
        cand_tables.update(r[0] for r in cur_u.fetchall())

    out = sorted(cand_tables)
    _CAND_TABLES_CACHE[key] = out
    return out

def _query_unified_for_key(cur_u, fam, base, key):
    if not key:
        return []
    cand_tables = _get_candidate_tables(cur_u, fam, base)
    if not cand_tables:
        return []
    in_marks = ",".join(["?"] * len(cand_tables))
    sql_eq = f'''
      SELECT "テーブル名","結合データ" AS matched_key,"件数",
             "勝率_val","勝率","連対率_val","連対率","複勝率_val","複勝率",
             "2着率_val","2着率","3着率_val","3着率",
             "掲示板率_val","掲示板率",
             "平均着順","単勝回収値","複勝回収値","単勝適正回収値"
      FROM unified_all
      WHERE "結合データ" = ?
        AND "テーブル名" IN ({in_marks})
      ORDER BY "複勝率_val" DESC, "連対率_val" DESC
      LIMIT 50
    '''
    cur_u.execute(sql_eq, (key, *cand_tables))
    return [dict(zip([c[0] for c in cur_u.description], r)) for r in cur_u.fetchall()]

# ========= entries_xjoin_* を構築 =========

# [CHG] family=14
# 新（14パターン：rank系のbandを追加）
# FAMILY_LIST = [
#     "normal","normalJ","rankB","rankJ","rankBJ",        # 順位（rank）
#     "hensachiA","hensachiB","hensachiJ","hensachiBJ","hensachiAJ"         # 偏差値帯（hensachi）
# ]

# 新（15パターン：rank系のbandも既定で有効化）
FAMILY_LIST = [
    # rank（従来）
    "normal","normalJ","rankB","rankJ","rankBJ",
    # rank-band（★今回の追加）
    "normal_band","rankB_band","rankJ_band","rankBJ_band","normalJ_band",
    # hensachi（従来）
    "hensachiA","hensachiB","hensachiJ","hensachiBJ","hensachiAJ"
]

def _build_key_candidates(row, base):
    place = _norm(row.get("場所"))
    sd    = _norm(row.get("芝・ダ"))
    dist  = _norm(row.get("距離"))
    inout = _norm(row.get("芝(内・外)")) or _norm(row.get("芝(内外)"))
    wtype = _norm(row.get("重量種別"))
    cls   = _norm(row.get("クラス名"))
    waku  = _norm(row.get("枠番")) or _norm(row.get("枠"))
    wakg  = _norm(row.get("枠番グループ")) or _mk_wakuban_group_fallback(waku, row.get("頭数"))
    jock  = _norm(row.get("騎手"))

    rank  = _norm(row.get(base + "_順位"))
    hensachi_band_raw = _norm(row.get(base + "_偏差値帯"))  # 66~70 等
    rank_band_raw     = _norm(row.get(base + "_順位帯"))    # 1~2位 等（あれば理想）

    # band候補（偏差値帯）
    hensachi_band_variants = _normalize_band_token(hensachi_band_raw) if hensachi_band_raw else [""]

    # band候補（順位帯）：列が無ければ rank からフォールバック生成
    if rank_band_raw:
        rank_band_variants = _normalize_rank_band_token(rank_band_raw)
    else:
        rb = _rank_to_band(rank) if rank else ""
        rank_band_variants = _normalize_rank_band_token(rb) if rb else [""]

    # # in/out は「あり/なし」両方
    # 未設定でも foragg 側が「内/外/内外」を持つ場合にヒットできるよう、常に両取り
    if inout:
        inout_variants = [inout, ""]
    else:
        # 未設定なら保険で広めに試す（阪神で foragg に「内」が混入している等のケースを救う）
        inout_variants = ["", "内", "外", "内外"]

    keys = {}

    def add_key(tag, *tail):
        arr = keys.setdefault(tag, set())
        for io in inout_variants:
            head = _key_join(place, sd, dist, io)
            arr.add(_key_join(head, *tail))

    # === rank（順位） ===
    if rank:
        for rv in _normalize_rank_variants(rank):
            add_key("normal",    wtype, cls, rv)
            add_key("rankB",     wakg, rv)
            add_key("rankJ",     jock, rv)
            add_key("rankBJ",    wakg, jock, rv)
            add_key("normalJ",   wtype, cls, jock, rv)

    # === rank-band（順位帯） ===
    for b in rank_band_variants:
        if not b: continue
        add_key("normal_band",   wtype, cls, b)
        add_key("rankB_band",    wakg, b)
        add_key("rankJ_band",    jock, b)
        add_key("rankBJ_band",   wakg, jock, b)
        add_key("normalJ_band",  wtype, cls, jock, b)

    # === hensachi-band（偏差値帯） ===
    for b in hensachi_band_variants:
        if not b: continue
        add_key("hensachiA",   wtype, cls, b)
        add_key("hensachiB",   wakg, b)
        add_key("hensachiJ",   jock, b)
        add_key("hensachiBJ",  wakg, jock, b)
        add_key("hensachiAJ",  wtype, cls, jock, b)

    like_prefix = _key_join(place, sd, dist)
    return keys, like_prefix

def build_entries_xjoin_tables(con, cur, entries_loaded):
    if not ensure_unified_indexes(con, cur):
        return

    page_size  = int(os.getenv("XJOIN_PAGE_SIZE",  "800"))
    batch_size = int(os.getenv("XJOIN_BATCH_SIZE", "2000"))
    strict = os.getenv("XJOIN_STRICT", "1") == "1"
    fam_filter = os.getenv("XJOIN_FAMILIES", "").strip()
    if fam_filter:
        families = [f for f in FAMILY_LIST if f in {x.strip() for x in fam_filter.split(",")}]
    else:
        families = FAMILY_LIST

    # [CHG] base×family あたりヒットのトップ1だけ採用するオプション
    top1 = os.getenv("XJOIN_ONE_PER_BASE_FAM", "0") == "1"

    cur_u = con.cursor()
    cur_r = con.cursor()

    for tname, cols in entries_loaded:
        xname = "entries_xjoin_" + tname.replace("entries_", "", 1)
        print(f"[xjoin] start: {xname}", flush=True)

        cur.execute(f'DROP TABLE IF EXISTS "{xname}"')
        cur.execute(f"""
          CREATE TABLE "{xname}" (
            "source_table"           TEXT,
            "レースID(新/馬番無)"     TEXT,
            "場 R"                   TEXT,
            "馬番"                   TEXT,
            "枠番"                   TEXT,
            "枠番グループ"           TEXT,
            "騎手"                   TEXT,
            "馬名"                   TEXT,
            "base"                   TEXT,
            "family"                 TEXT,
            "key"                    TEXT,
            "like_prefix"            TEXT,
            "テーブル名"             TEXT,
            "件数"                   INT,
            "勝率_val"               REAL,
            "勝率"                   TEXT,
            "連対率_val"             REAL,
            "連対率"                 TEXT,
            "複勝率_val"             REAL,
            "複勝率"                 TEXT,
            "2着率_val"              REAL,
            "2着率"                  TEXT,
            "3着率_val"              REAL,
            "3着率"                  TEXT,
            "掲示板率_val"           REAL,
            "掲示板率"               TEXT,
            "平均着順"               REAL,
            "単勝回収値"             REAL,
            "複勝回収値"             REAL,
            "単勝適正回収値"         REAL,
            "hit_key"               TEXT,
            "odds_tag"              TEXT
          )
        """)
        for cidx in ["レースID(新/馬番無)","場 R","馬番","family","base","key","テーブル名"]:
            safe = re.sub(r'[^0-9a-zA-Z_]', '_', cidx)
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{xname}_{safe} ON "{xname}"("{cidx}")')

        # [CHG] 一意インデックスで重複混入を抑止
        cur.execute(f'''
          CREATE UNIQUE INDEX IF NOT EXISTS uq_{xname}_core
          ON "{xname}"(
            "source_table","レースID(新/馬番無)","場 R","馬番",
            "base","family","key","テーブル名"
          )
        ''')

        col_rows  = cur_r.execute(f'PRAGMA table_info("{tname}")').fetchall()
        col_names = [r[1] for r in col_rows]
        total     = cur_r.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
        bases     = _collect_indicator_bases_from_cols(col_names)
        if not bases:
            print(f"[xjoin] note: {tname} に *_順位+*_偏差値 のペアが見当たりません（スキップ）", flush=True)
            continue

        print(f"[xjoin] scanning {tname} rows={total}, bases={len(bases)}, families={len(families)}, page={page_size}, batch={batch_size}", flush=True)

        # ins_cols = ["source_table","レースID(新/馬番無)","場 R","馬番","枠番","枠番グループ","騎手","馬名","base","family","key","like_prefix",
        #             "テーブル名","件数","勝率_val","勝率","連対率_val","連対率","複勝率_val","複勝率","平均着順","単勝回収値","複勝回収値","単勝適正回収値","hit_key", "odds_tag"]
        ins_cols = [
        "source_table","レースID(新/馬番無)","場 R","馬番","枠番","枠番グループ","騎手","馬名",
        "base","family","key","like_prefix","テーブル名","件数",
        "勝率_val","勝率","連対率_val","連対率","複勝率_val","複勝率",
        "2着率_val","2着率","3着率_val","3着率","掲示板率_val","掲示板率",
        "平均着順","単勝回収値","複勝回収値","単勝適正回収値",
        "hit_key","odds_tag"
        ]
        cols_quoted = ", ".join([f'"{c}"' for c in ins_cols])
        qmarks = ",".join(["?"]*len(ins_cols))

        # [CHG] 重複無視に変更
        ins_sql = f'INSERT OR IGNORE INTO "{xname}" ({cols_quoted}) VALUES ({qmarks})'

        query_cache = {}
        offset    = 0
        inserted  = 0
        processed = 0

        try:
            while offset < total:
                cur_r.execute(
                    f'SELECT * FROM "{tname}" ORDER BY CAST(COALESCE("馬番","0") AS INT) LIMIT ? OFFSET ?',
                    (page_size, offset)
                )
                fetched = cur_r.fetchall()
                if not fetched:
                    break

                batch_vals = []
                for tup in fetched:
                    rd = dict(zip(col_names, tup))
                    name = _row_display_name(rd)
                    if not rd.get("馬名"):
                        rd["馬名"] = name

                    for base in bases:
                        key_map, like_prefix = _build_key_candidates(rd, base)
                        for fam in families:
                            kset = key_map.get(fam)
                            if not kset:
                                continue
                            for key in sorted(kset):
                                qk = (fam, base, key, like_prefix)
                                hits = query_cache.get(qk)
                                if hits is None:
                                    hits = _query_unified_for_key(cur_u, fam, base, key)
                                    # [CHG] トップ1のみ採用オプション
                                    if top1 and hits:
                                        hits = hits[:1]
                                    query_cache[qk] = hits
                                if not hits:
                                    continue
                                for h in hits:
                                    batch_vals.append([
                                        tname,
                                        _norm(rd.get("レースID(新/馬番無)")),
                                        _norm(rd.get("場 R")),
                                        _norm(rd.get("馬番")),
                                        _norm(rd.get("枠番") or rd.get("枠")),
                                        _norm(rd.get("枠番グループ")),
                                        _norm(rd.get("騎手")),
                                        name,
                                        base, fam, key, like_prefix,
                                        _norm(h.get("テーブル名")),
                                        h.get("件数"),
                                        float(h.get("勝率_val") or 0.0),
                                        _norm(h.get("勝率")),
                                        float(h.get("連対率_val") or 0.0),
                                        _norm(h.get("連対率")),
                                        float(h.get("複勝率_val") or 0.0),
                                        _norm(h.get("複勝率")),
                                        float(h.get("2着率_val") or 0.0),
                                        _norm(h.get("2着率")),
                                        float(h.get("3着率_val") or 0.0),
                                        _norm(h.get("3着率")),
                                        float(h.get("掲示板率_val") or 0.0),
                                        _norm(h.get("掲示板率")),
                                        h.get("平均着順"),
                                        h.get("単勝回収値"),
                                        h.get("複勝回収値"),
                                        h.get("単勝適正回収値"),
                                        _norm(h.get("matched_key")),  
                                        _extract_odds_tag(_norm(h.get("テーブル名")))
                                    ])
                                    if len(batch_vals) >= batch_size:
                                        cur.executemany(ins_sql, batch_vals)
                                        con.commit()
                                        inserted += len(batch_vals)
                                        batch_vals = []

                    processed += 1

                if batch_vals:
                    cur.executemany(ins_sql, batch_vals)
                    con.commit()
                    inserted += len(batch_vals)

                offset += len(fetched)
                print(f"[xjoin] {xname}: processed {min(offset,total)}/{total} rows, inserted {inserted}", flush=True)

            print(f"[xjoin] built: {xname} (inserted {inserted}, total_rows {total}, bases {len(bases)})", flush=True)

        except KeyboardInterrupt:
            print(f"[xjoin] interrupted: {xname} (processed {processed}, inserted {inserted})", flush=True)
            raise

def drop_stale_tables(con, cur, keep_tables: set):
    cur2 = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'foragg_%' OR name LIKE 'entries_%');"
    )
    existing = {r[0] for r in cur2.fetchall()}
    stale = existing - keep_tables - {n for n in existing if n.startswith("entries_xjoin_")}
    for t in sorted(stale):
        cur.execute(f'DROP TABLE IF EXISTS "{t}"')
        cur.execute('DELETE FROM __tables_meta__ WHERE table_name=?', (t,))
        print(f"[prune] dropped: {t}")

def parse_cli():
    p = argparse.ArgumentParser(description="foragg/entries を SQLite に取り込み＋xjoin 前展開（互換維持）")
    p.add_argument("--mode", choices=["both","foragg","entries"], default="both",
                   help="取り込み対象（既定: both=従来通り）")
    p.add_argument("--fresh", action="store_true", help="DBを作り直す（racing.db を削除してゼロから）")
    p.add_argument("--prune", action="store_true", help="今回作らなかった古い foragg_/entries_ テーブルを削除")
    p.add_argument("--no-xjoin", action="store_true", help="entries_xjoin_* を作らない（既定は作る）")
    p.add_argument("--foragg-dir", default=FORAGG_DIR)
    p.add_argument("--entries-dir", default=ENTRIES_DIR)
    p.add_argument("--db-path", default=DB_PATH)
    return p.parse_args()

def main():
    args = parse_cli()
    if args.fresh and os.path.exists(args.db_path):
        os.remove(args.db_path)

    con = sqlite3.connect(args.db_path)
    cur = con.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS __tables_meta__ (
            table_name TEXT PRIMARY KEY,
            source_file TEXT
        )
    """)
    cur.execute("DELETE FROM __tables_meta__")

    foragg_loaded  = []
    entries_loaded = []
    keep_tables = set()

    if args.mode in ("both","foragg"):
        foragg_loaded, created = load_foragg_tables(con, cur, args.foragg_dir)
        keep_tables |= created
        rebuild_unified_all(con, cur, foragg_loaded)

    if args.mode in ("both","entries"):
        entries_loaded, created = load_entries_tables(con, cur, args.entries_dir)
        keep_tables |= created
        build_entries_all(con, cur, entries_loaded)
        if not args.no_xjoin:
            build_entries_xjoin_tables(con, cur, entries_loaded)

    if args.prune:
        drop_stale_tables(con, cur, keep_tables)

    con.commit()
    con.close()
    print(f"OK: wrote DB -> {args.db_path}  (foragg tables: {len(foragg_loaded)}, entries tables: {len(entries_loaded)}, mode={args.mode}, prune={args.prune}, xjoin={not args.no_xjoin})")

if __name__ == "__main__":
    main()