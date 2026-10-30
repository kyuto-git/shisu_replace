# replace_csv.py
import os
import re
import unicodedata
import numpy as np
import pandas as pd

# 入力: /app/csv_files、出力: /app/replace_csv を想定（docker run -v $(pwd):/app）
CSV_DIR = "/app/csv_files"
OUT_DIR = "/app/replace_csv"

# =========================================================
# 設定（運用で編集OK）
# =========================================================
# ファイル名に含まれていれば「低いほど良い」と判定（部分一致）
LOWER_IS_BETTER_KEYWORDS = {
    "タイムM", "想定前半", "想定後半", "展開",
    # 例: "タイムM(補正)", "ラップ前半" を追加したい場合はここに追記
}

# ZI 判定：contains（どこかに 'ZI' が含まれていれば True）
ZI_DETECT_TOKEN = "ZI"

# ZI 時に偏差値系出力から除外するクラス名（新馬戦）
HENSACHI_SKIP_CLASSES = {"新馬"}

# =========================================================
# 共通ユーティリティ
# =========================================================
def _ensure_dirs():
    if not os.path.isdir(CSV_DIR):
        raise FileNotFoundError(f"CSV_DIR not found: {CSV_DIR}")
    os.makedirs(OUT_DIR, exist_ok=True)

def _normalize_name(s: str) -> str:
    # 全角/半角のゆれ対策（NFKCで正規化）
    return unicodedata.normalize("NFKC", s or "")

def _strip_spaces(df: pd.DataFrame) -> pd.DataFrame:
    """全角/半角スペースを文字列列から除去"""
    obj_cols = df.select_dtypes(include="object").columns
    if len(obj_cols) > 0:
        df[obj_cols] = df[obj_cols].apply(
            lambda col: col.str.replace(r"[ \u3000]+", "", regex=True)
        )
    return df

def _inner_outer_col(df: pd.DataFrame):
    """芝(内・外) / 芝(内外) の列ゆれに対応"""
    if '芝(内・外)' in df.columns:
        return '芝(内・外)'
    if '芝(内外)' in df.columns:
        return '芝(内外)'
    return None

def _to_int_str_series(s: pd.Series) -> pd.Series:
    """数値列を .0 が出ない整数文字列に整形（全て数値化できる場合のみ）"""
    n = pd.to_numeric(s, errors='coerce')
    if n.notna().all():
        return n.astype('Int64').astype(str)
    return s.astype(str)

def _detect_metric_from_filename(filename: str) -> str:
    """
    ファイル名: 'xxxx_指数名.csv' の末尾（拡張子前）を指数名として推定。
    例）'abc_タイムM.csv' -> 'タイムM'
    '_' がない場合はベース名全体を返す。
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    return base.split('_')[-1] if '_' in base else base

def is_lower_better_by_filename(filename: str) -> bool:
    """ファイル名にキーワードが含まれていれば“低いほど良い”扱い（部分一致）"""
    base = _normalize_name(os.path.splitext(os.path.basename(filename))[0])
    return any(_normalize_name(k) in base for k in LOWER_IS_BETTER_KEYWORDS)

def is_zi_file(filename: str) -> bool:
    """ファイル名に 'ZI' が含まれていれば True（contains 固定）"""
    base = _normalize_name(os.path.splitext(os.path.basename(filename))[0])
    return ZI_DETECT_TOKEN in base

# =========================================================
# 置換ロジック（※ 馬場状態は処理・出力とも使用しない／頭数は出力しない）
# =========================================================
def process_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # 元の頭数（数値）を保持（フィルタや枠番グループに使用）
    orig_head = pd.to_numeric(df.get('頭数', pd.Series(index=df.index)), errors='coerce')

    # 余分なスペース除去（先にやる）
    df = _strip_spaces(df)

    # --- 距離 は先に文字列化（レンジ文字を代入しても dtype 警告が出ないように）
    if '距離' in df.columns:
        df['距離'] = df['距離'].astype(str)

    # 1. 重量種別
    if '重量種別' in df.columns:
        df['重量種別'] = df['重量種別'].replace(['別定', 'ハンデ'], '別ハ')

    # 2. クラス名
    class_map = {
        '500万':'1・2_勝','1000万':'1・2_勝','1勝':'1・2_勝','2勝':'1・2_勝',
        '1600万':'準OP・OP','3勝':'準OP・OP','ｵｰﾌﾟﾝ':'準OP・OP','OP(L)':'準OP・OP',
        'Ｇ２':'重賞','Ｇ３':'重賞'
    }
    if 'クラス名' in df.columns:
        df['クラス名'] = df['クラス名'].replace(class_map)

    # 3. 「馬齢」かつ クラス名が 1・2_勝 / 準OP・OP → 「1勝・OP」
    if {'重量種別','クラス名'} <= set(df.columns):
        mask = (df['重量種別'] == '馬齢') & (df['クラス名'].isin(['1・2_勝','準OP・OP']))
        df.loc[mask, 'クラス名'] = '1勝・OP'

    # 4. 頭数をグループ化（少頭数/多頭数）。※ “一桁除外”判定は orig_head を使用
    # if '頭数' in df.columns:
    #     head_map = {**{str(n):'少頭数' for n in range(6,12)},
    #                 **{str(n):'多頭数' for n in range(12,19)}}
    #     df['頭数'] = df['頭数'].astype(str).map(lambda x: head_map.get(x, x))
    # 頭数グループを復活させた場合、build_joined_output() 内の全ての parts += [...] の中に「,_col('頭数')」を追加する必要あり
    #    一桁除外や枠番判定には orig_head を使うため _頭数数値 は保持

    # 5. 馬場状態は使わない（置換しない・出力に含めない）

    # 6. 開催場所×芝ダ×距離（場所別ルール）
    inner_outer = _inner_outer_col(df)

    def set_range(place, turf, dists, newval, need_io=False, io_val=None):
        if not {'場所','芝・ダ','距離'} <= set(df.columns):
            return
        base = (df['場所']==place) & (df['芝・ダ']==turf) & (df['距離'].astype(str).isin(dists))
        if need_io and inner_outer is not None:
            base &= (df[inner_outer]==io_val)
        df.loc[base, '距離'] = newval  # 距離は文字列化済み

    # 6-1 札幌
    # set_range('札幌','芝',['1500','1800'],'15-18')
    # 6-2 福島
    set_range('福島','芝',['1700','1800'],'17-18')
    # 6-3 新潟（内外）
    if inner_outer:
        for d in ['1200','1400']: set_range('新潟','芝',[d],'12-14',need_io=True,io_val='内')
        for d in ['1600','1800']: set_range('新潟','芝',[d],'16-18',need_io=True,io_val='外')
        for d in ['2000','2200','2400']: set_range('新潟','芝',[d],'20-24',need_io=True,io_val='内')
        for d in ['3000','3200']: set_range('新潟','芝',[d],'30-32',need_io=True,io_val='外')
    # 6-4 中山
    set_range('中山','ダ',['2400','2500'],'24-25')
    # 6-5 東京
    set_range('東京','芝',['1600','1800'],'16-18')
    set_range('東京','芝',['2300','2400'],'23-24')
    set_range('東京','ダ',['1300','1400'],'13-14')
    # 6-6 中京
    set_range('中京','芝',['1200','1400'],'12-14')
    set_range('中京','ダ',['1800','1900'],'18-19')
    # 6-7 京都（内外）
    if inner_outer:
        for d in ['1400','1600']: set_range('京都','芝',[d],'14-16',need_io=True,io_val='内')
        for d in ['1400','1600','1800']: set_range('京都','芝',[d],'14-18',need_io=True,io_val='外')
        for io in ['内','外']: set_range('京都','芝',['2000'],'20-24',need_io=True,io_val=io)
        set_range('京都','芝',['2200','2400'],'22-24',need_io=True,io_val='外')
        set_range('京都','芝',['3000','3200'],'30-32',need_io=True,io_val='外')
        set_range('京都','ダ',['1800','1900'],'18-19')
    # 6-8 阪神
    set_range('阪神','芝',['1600','1800'],'16-18')
    set_range('阪神','芝',['2400','2600'],'24-26')
    set_range('阪神','芝',['3000','3200'],'30-32')
    # 6-9 小倉
    set_range('小倉','芝',['1700','1800'],'17-18')

    # 7. コース区分 A/B/C/D → 空
    if 'コース区分' in df.columns:
        df['コース区分'] = df['コース区分'].replace(
            ['A','B','C','D','，A','，B','，C','，D',',A',',B',',C',',D'], ''
        )

    # 欠損は空に（“nan”文字が混入しないように）
    df = df.fillna("")

    # 整数見えさせたい列は文字列化（.0回避）
    for col in ['指数順位']:
        if col in df.columns:
            df[col] = _to_int_str_series(df[col])

    # 内部用：数値頭数を保持（フィルタや枠番グループに使用）
    df['_頭数数値'] = orig_head

    return df

# =========================================================
# 偏差値の付与（指数をレースID(新/馬番無)単位で）
#  追加要件：
#   - “低いほど良い”指数はファイル名にキーワードが含まれていれば採用（部分一致）→ 値に -1 を掛けて正規化
#   - ファイル名に ZI が含まれていれば（contains）：
#       * クラス名「新馬」は偏差値系の出力から除外
#       * 指数が 0 は同レース内の「0以外平均」に置換（≒偏差値50寄せ）
#   - 合計値などマイナスを含む指数でもそのまま偏差値化（問題なし）
# =========================================================
def _hensachi_bucket(h):
    try:
        h = float(h)
    except Exception:
        return ""
    if h >= 71:
        return "71以上"
    elif 66 <= h <= 70:
        return "66~70"
    elif 61 <= h <= 65:
        return "61~65"
    elif 51 <= h <= 60:
        return "51~60"
    elif 41 <= h <= 50:
        return "41~50"
    else:
        return "40以下"

def add_hensachi_columns(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    df = df.copy()
    # デフォルトでスキップフラグは False
    df['_skip_hensachi'] = False

    if not {'レースID(新/馬番無)', '指数'}.issubset(df.columns):
        df['偏差値'] = ""
        df['偏差値帯'] = ""
        return df

    lower_is_better = is_lower_better_by_filename(filename)
    is_zi = is_zi_file(filename)

    # 数値化
    df['__指数_num__'] = pd.to_numeric(df['指数'], errors='coerce')

    # ZI: 新馬は偏差値系から除外
    if is_zi and 'クラス名' in df.columns:
        mask_shinba = df['クラス名'].isin(HENSACHI_SKIP_CLASSES)
        df.loc[mask_shinba, '_skip_hensachi'] = True

    # ZI: 0 を同レース内の「0以外平均」で置換
    if is_zi:
        g = df.groupby('レースID(新/馬番無)', dropna=False)
        nonzero_mean = g['__指数_num__'].transform(
            lambda s: s[s != 0].mean() if (s != 0).any() else np.nan
        )
        df['__指数_num__'] = df['__指数_num__'].where(df['__指数_num__'] != 0, nonzero_mean)

    # 低いほど良い指数は符号反転して「高いほど良い」に正規化
    adj = df['__指数_num__'] * (-1 if lower_is_better else 1)

    # グループ平均・母標準偏差（ddof=0）を transform で算出
    g = df.groupby('レースID(新/馬番無)', dropna=False)
    mean = g['__指数_num__'].transform(lambda s: (s * (-1 if lower_is_better else 1)).mean())
    std = g['__指数_num__'].transform(
        lambda s: float(np.nanstd((s * (-1 if lower_is_better else 1)).values, ddof=0))
                  if not s.isna().all() else np.nan
    )

    # 偏差値計算（std==0/NaN は 50）
    z = 50 + 10 * (adj - mean) / std
    z = z.where(~(std.isna() | (std == 0)), 50).round(0)

    df['偏差値'] = z
    df['偏差値帯'] = df['偏差値'].map(_hensachi_bucket)

    # 後片付け
    df.drop(columns=['__指数_num__'], inplace=True)
    return df

# =========================================================
# 枠番グループの付与（頭数に応じた内/中/外）※頭数は出力しないが判定のため保持
# =========================================================
def add_wakuban_group(df: pd.DataFrame) -> pd.DataFrame:
    if not {'枠番', '_頭数数値'}.issubset(df.columns):
        df['枠番グループ'] = ""
        return df

    waku = pd.to_numeric(df['枠番'], errors='coerce')
    head = pd.to_numeric(df['_頭数数値'], errors='coerce')

    grp = pd.Series("", index=df.index, dtype="object")

    # 10〜13頭
    mask = head.between(10, 13, inclusive="both")
    grp.loc[mask & waku.between(1, 5)] = "内枠"
    grp.loc[mask & waku.between(6, 8)] = "外枠"

    # 14〜16頭
    mask = head.between(14, 16, inclusive="both")
    grp.loc[mask & waku.between(1, 4)] = "内枠"
    grp.loc[mask & waku.between(5, 8)] = "外枠"

    # 17〜18頭
    mask = head.between(17, 18, inclusive="both")
    grp.loc[mask & waku.between(1, 3)] = "内枠"
    grp.loc[mask & waku.between(4, 6)] = "中枠"
    grp.loc[mask & waku.between(7, 8)] = "外枠"

    df['枠番グループ'] = grp
    return df

# =========================================================
# 出力の“×”結合（全モード共通：馬場状態も頭数も出力に含めない）
# =========================================================
def build_joined_output(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """
    mode:
      既存（頭数は出力しない）:
        'normal'    → 場所×重量種別×クラス名×(内外)×芝・ダ×距離×指数順位
        'hensachiA' → 場所×重量種別×クラス名×(内外)×芝・ダ×距離×偏差値帯
        'hensachiB' → 場所×(内外)×芝・ダ×距離×枠番グループ×偏差値帯
        'rankB'     → 場所×(内外)×芝・ダ×距離×枠番グループ×指数順位
      騎手を含む6種（頭数は出力しない）:
        'rankJ'      → 場所×(内外)×芝・ダ×距離×騎手×指数順位
        'hensachiJ'  → 場所×(内外)×芝・ダ×距離×騎手×偏差値帯
        'rankBJ'     → 場所×(内外)×芝・ダ×距離×枠番グループ×騎手×指数順位
        'hensachiBJ' → 場所×(内外)×芝・ダ×距離×枠番グループ×騎手×偏差値帯
        'normalJ'    → 場所×重量種別×クラス名×(内外)×芝・ダ×距離×騎手×指数順位
        'hensachiAJ' → 場所×重量種別×クラス名×(内外)×芝・ダ×距離×騎手×偏差値帯

    共通:
      - 馬場状態は使わない
      - 頭数が一桁（1〜9）は除外（_頭数数値 を使用）
      - 偏差値系モードでは _skip_hensachi==True の行を除外（ZI×新馬）
    """
    df = _strip_spaces(df).fillna("")
    inner_outer = _inner_outer_col(df)

    # 頭数が一桁（1〜9）を除外（内部保持の _頭数数値 優先）
    head_num = pd.to_numeric(df.get('_頭数数値', pd.Series(index=df.index)), errors='coerce')
    df = df[~head_num.lt(10)]

    # 偏差値系モードなら、ZI×新馬にマークされた行を除外
    if mode in {'hensachiA', 'hensachiB', 'hensachiJ', 'hensachiBJ', 'hensachiAJ'}:
        if '_skip_hensachi' in df.columns:
            df = df[~df['_skip_hensachi']]

    def _col(name):
        return df[name] if name in df.columns else ""

    parts = []
    if mode == 'normal':
        parts += [_col('場所'), _col('重量種別'), _col('クラス名')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('指数順位')]

    elif mode == 'hensachiA':
        parts += [_col('場所'), _col('重量種別'), _col('クラス名')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('偏差値帯')]

    elif mode == 'hensachiB':
        parts += [_col('場所')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('枠番グループ'), _col('偏差値帯')]

    elif mode == 'rankB':
        parts += [_col('場所')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('枠番グループ'), _col('指数順位')]

    # --- 騎手を含む6モード（頭数は出力しない） ---
    elif mode == 'rankJ':
        parts += [_col('場所')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('騎手'), _col('指数順位')]

    elif mode == 'hensachiJ':
        parts += [_col('場所')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('騎手'), _col('偏差値帯')]

    elif mode == 'rankBJ':
        parts += [_col('場所')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('枠番グループ'), _col('騎手'), _col('指数順位')]

    elif mode == 'hensachiBJ':
        parts += [_col('場所')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('枠番グループ'), _col('騎手'), _col('偏差値帯')]

    elif mode == 'normalJ':
        parts += [_col('場所'), _col('重量種別'), _col('クラス名')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('騎手'), _col('指数順位')]

    elif mode == 'hensachiAJ':
        parts += [_col('場所'), _col('重量種別'), _col('クラス名')]
        if inner_outer: parts.append(_col(inner_outer))
        parts += [_col('芝・ダ'), _col('距離'), _col('騎手'), _col('偏差値帯')]

    else:
        raise ValueError(f"unknown mode: {mode}")

    row_join = pd.DataFrame({f'c{i}': p for i, p in enumerate(parts)})
    joined = (
        row_join.astype(str)
        .agg('×'.join, axis=1)
        .str.replace('×+', '×', regex=True)   # 連続×を1つに圧縮
        .str.rstrip('×')                       # 末尾×を除去
        .str.replace('nan', '')                # “nan”文字を除去（保険）
    )

    out = pd.DataFrame({
        'レースID(新)': _col('レースID(新)'),
        '結合データ': joined
    })

    # 念のため：最後の2列が両方空の行は削除（保険）
    tail2 = out.iloc[:, -2:].replace(np.nan, "")
    keep = ~((tail2.iloc[:, 0] == "") & (tail2.iloc[:, 1] == ""))
    out = out[keep]

    return out

# =========================================================
# メイン
# =========================================================
def main():
    _ensure_dirs()

    files = [f for f in os.listdir(CSV_DIR) if f.lower().endswith(".csv")]
    if not files:
        print("No CSV files found.")
        return

    for filename in files:
        in_path = os.path.join(CSV_DIR, filename)
        try:
            print(f"[START] {filename}")
            df = pd.read_csv(in_path, encoding="shift_jis")

            # 置換（馬場状態は使わない / 頭数は出力しない）
            df_proc = process_dataframe(df.copy())

            # 偏差値列の付与（指数をレース単位で / “低いほど良い”をファイル名の部分一致で判定 / ZI特例）
            df_h = add_hensachi_columns(df_proc.copy(), filename)

            # 枠番グループ（偏差値B/ rankB / 騎手BJ系のため）
            df_h = add_wakuban_group(df_h.copy())
            df_rank = add_wakuban_group(df_proc.copy())  # 指数順位＋枠番用

            # ----------------- 既存4出力（頭数は出さない版） -----------------
            # 1) 通常（指数順位）
            out_normal = build_joined_output(df_proc.copy(), mode='normal')
            out_normal.to_csv(os.path.join(OUT_DIR, f"processed_{filename}"),
                              index=False, encoding="shift_jis")

            # 2) 偏差値A（枠番なし）
            out_hA = build_joined_output(df_h.copy(), mode='hensachiA')
            out_hA.to_csv(os.path.join(OUT_DIR, f"processed_hensachiA_{filename}"),
                          index=False, encoding="shift_jis")

            # 3) 偏差値B（枠番あり）
            out_hB = build_joined_output(df_h.copy(), mode='hensachiB')
            out_hB.to_csv(os.path.join(OUT_DIR, f"processed_hensachiB_{filename}"),
                          index=False, encoding="shift_jis")

            # 4) rankB（指数順位 × 枠番）
            out_rankB = build_joined_output(df_rank.copy(), mode='rankB')
            out_rankB.to_csv(os.path.join(OUT_DIR, f"processed_rankB_{filename}"),
                             index=False, encoding="shift_jis")

            # -------------- 騎手を含む 6 出力（頭数は出さない） --------------
            # 5) rankJ（指数順位・騎手）
            out_rankJ = build_joined_output(df_proc.copy(), mode='rankJ')
            out_rankJ.to_csv(os.path.join(OUT_DIR, f"processed_rankJ_{filename}"),
                             index=False, encoding="shift_jis")

            # 6) hensachiJ（偏差値帯・騎手）
            out_hensachiJ = build_joined_output(df_h.copy(), mode='hensachiJ')
            out_hensachiJ.to_csv(os.path.join(OUT_DIR, f"processed_hensachiJ_{filename}"),
                                 index=False, encoding="shift_jis")

            # 7) rankBJ（指数順位 × 枠番 × 騎手）
            out_rankBJ = build_joined_output(df_rank.copy(), mode='rankBJ')
            out_rankBJ.to_csv(os.path.join(OUT_DIR, f"processed_rankBJ_{filename}"),
                              index=False, encoding="shift_jis")

            # 8) hensachiBJ（偏差値帯 × 枠番 × 騎手）
            out_hensachiBJ = build_joined_output(df_h.copy(), mode='hensachiBJ')
            out_hensachiBJ.to_csv(os.path.join(OUT_DIR, f"processed_hensachiBJ_{filename}"),
                                  index=False, encoding="shift_jis")

            # 9) normalJ（指数順位・重量/クラス・騎手）
            out_normalJ = build_joined_output(df_proc.copy(), mode='normalJ')
            out_normalJ.to_csv(os.path.join(OUT_DIR, f"processed_normalJ_{filename}"),
                               index=False, encoding="shift_jis")

            # 10) hensachiAJ（偏差値帯・重量/クラス・騎手）
            out_hensachiAJ = build_joined_output(df_h.copy(), mode='hensachiAJ')
            out_hensachiAJ.to_csv(os.path.join(OUT_DIR, f"processed_hensachiAJ_{filename}"),
                                  index=False, encoding="shift_jis")

            print(f"[DONE ] {filename}")

        except Exception as e:
            print(f"[ERROR] {filename}: {e}")

if __name__ == "__main__":
    main()