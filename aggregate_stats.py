# -*- coding: utf-8 -*-
"""
aggregate_stats_ver2.py
- foragg_*（素材）を読み、グループ集計＋Jeffreys ベイズ（Beta(0.5,0.5)）を併記
- 単勝回収値・複勝回収値・単勝適正回収値（ここでは=単勝回収値の例）も出力
- サンプル数 < 5 のグループは除外
- 集計後、同名の foragg_* を上書き（= 最終成果物）
- 追加：単勝オッズが 3.0 未満を分母から除いた“オッズ版”も別ファイルに出力（foragg_odds3p_*）
- 警告対応：str.contains 非捕捉グループ、groupby.apply は include_groups=False を指定
"""

import os
import glob
import numpy as np
import pandas as pd

BASE_DIR = "/app"
IN_DIR   = os.path.join(BASE_DIR, "foragg_csv")
OUT_DIR  = os.path.join(BASE_DIR, "foragg_csv")

KEY_COL     = "結合データ"
RACEID_COL  = "レースID(新)"

# メタ（コース4列）— foragg_* に含まれている想定
META_COLS = ["場所", "芝・ダ", "距離", "芝(内・外)", "芝(内外)"]

# 列候補
RANK_CANDS       = ["着順","順位","着順（数値）"]
WIN_PAY_CANDS    = ["単勝配当","単勝払戻金","単勝払戻","単勝"]
PLACE_PAY_CANDS  = ["複勝配当","複勝払戻金","複勝払戻","複勝"]
WIN_ODDS_CANDS   = ["単勝オッズ","オッズ","単勝ｵｯｽﾞ"]

# 取消・除外は “分母から除外”
EXCLUDE_RANK_PAT = r'^(?:取|取消|消|除|除外|外)$'   # 一文字表記も含める
# 失格・中止は “分母に含め最下位扱い”
DQDNF_RANK_PAT   = r'^(?:失|中|止)$'                # 一文字表記を網羅

MIN_SAMPLE = 5

def _pick_col(df: pd.DataFrame, cands):
    for c in cands:
        if c in df.columns:
            return c
    return None

def _parse_rank_series(s: pd.Series):
    raw = s.astype(str)
    ex = raw.str.contains(EXCLUDE_RANK_PAT, regex=True, na=False)
    dq = raw.str.contains(DQDNF_RANK_PAT, regex=True, na=False)
    # 先頭の整数を抽出（index維持）
    num = raw.str.extract(r'(\d+)', expand=False)
    rank_num = pd.to_numeric(num, errors="coerce")
    # 失格/中止は最下位相当の大きな値（index維持）
    rank_num = rank_num.mask(dq, 9999)
    # 分母に含める（取消/除外は除外）
    use = ~ex
    return rank_num, use, dq

def _fmt_pct(x: float) -> str:
    return f"{x:.2f}%"

def _fmt_pair(freq_pct: float, bayes_pct: float) -> str:
    return f"{_fmt_pct(freq_pct)}（{_fmt_pct(bayes_pct)}）"

def _calc(group: pd.DataFrame, rank_col: str, win_col: str, plc_col: str, odds_col: str) -> pd.Series:
    # 返す列の定義（スキーマ固定）
    schema = {
        "件数": 0,
        "着別度数": "0-0-0-0",
        "勝率":   "0.00%（0.00%）",
        "連対率": "0.00%（0.00%）",
        "複勝率": "0.00%（0.00%）",
        "2着率": "0.00%（0.00%）",
        "3着率": "0.00%（0.00%）",
        "平均着順": "0.00",
        "単勝回収値": "0.0",
        "複勝回収値": "0.0",
        "単勝適正回収値": "0.0",
        "提案勝率": "0.00%",
        "提案連対率": "0.00%",
        "提案複勝率": "0.00%",
        "提案2着率": "0.00%",
        "提案3着率": "0.00%",
    }

    rnum, use_mask, dq_mask = _parse_rank_series(group[rank_col])

    # 分母対象（取消/除外を落とす）
    g  = group[use_mask]
    rn = rnum[use_mask]

    n = len(g)
    if n == 0:
        return pd.Series(schema)

    # --- 着度数 ---
    win_cnt = (rn == 1).sum()
    sec_cnt = (rn == 2).sum()
    thd_cnt = (rn == 3).sum()
    others  = n - (win_cnt + sec_cnt + thd_cnt)

    # --- 率（頻度） ---
    def _rate(p: int, n: int) -> float:
        return 0.0 if n <= 0 else 100.0 * (p / n)

    win_rate = _rate(win_cnt, n)
    ren_rate = _rate(win_cnt + sec_cnt, n)
    fku_rate = _rate(win_cnt + sec_cnt + thd_cnt, n)
    sec_rate = _rate(sec_cnt, n)
    thd_rate = _rate(thd_cnt, n)

    # --- ベイズ（Jeffreys: Beta(0.5,0.5)） ---
    def _bayes_mean(success: int, n: int) -> float:
        return 100.0 * (success + 0.5) / (n + 1.0) if n > 0 else 0.0

    win_b = _bayes_mean(win_cnt, n)
    ren_b = _bayes_mean(win_cnt + sec_cnt, n)
    fku_b = _bayes_mean(win_cnt + sec_cnt + thd_cnt, n)
    sec_b = _bayes_mean(sec_cnt, n)
    thd_b = _bayes_mean(thd_cnt, n)

    def _fmt_pct(x: float) -> str:
        return f"{x:.2f}%"

    def _fmt_pair(freq_pct: float, bayes_pct: float) -> str:
        return f"{_fmt_pct(freq_pct)}（{_fmt_pct(bayes_pct)}）"

    # --- 平均着順（失格/中止は 9999 として含まれている） ---
    avg_rank = float(np.nanmean(rn))

    # --- 回収値（1頭=100円） ---
    if win_col:
        win_pay = pd.to_numeric(g[win_col], errors="coerce").fillna(0.0)
    else:
        win_pay = pd.Series(0.0, index=g.index)

    if plc_col:
        plc_pay = pd.to_numeric(g[plc_col], errors="coerce").fillna(0.0)
    else:
        plc_pay = pd.Series(0.0, index=g.index)

    win_pay_real = win_pay.where(rn == 1, 0.0)
    plc_pay_real = plc_pay.where(rn <= 3, 0.0)

    tan_return  = float(win_pay_real.sum()) / n if n > 0 else 0.0
    fuku_return = float(plc_pay_real.sum()) / n if n > 0 else 0.0
    tan_teki    = tan_return

    # --- 提案（Laplace / Beta(1,1)） ---
    # 既存：提案勝率
    prop_win  = (win_cnt + 1.0) / (n + 2.0) * 100.0
    # 新規：提案連対率・提案複勝率・提案2着率・提案3着率
    prop_ren  = (win_cnt + sec_cnt + 1.0) / (n + 2.0) * 100.0
    prop_fku  = (win_cnt + sec_cnt + thd_cnt + 1.0) / (n + 2.0) * 100.0
    prop_sec  = (sec_cnt + 1.0) / (n + 2.0) * 100.0
    prop_thd  = (thd_cnt + 1.0) / (n + 2.0) * 100.0

    return pd.Series({
        "件数": int(n),
        "着別度数": f"{int(win_cnt)}-{int(sec_cnt)}-{int(thd_cnt)}-{int(others)}",
        "勝率":   _fmt_pair(win_rate, win_b),
        "連対率": _fmt_pair(ren_rate, ren_b),
        "複勝率": _fmt_pair(fku_rate, fku_b),
        "2着率":  _fmt_pair(sec_rate, sec_b),
        "3着率":  _fmt_pair(thd_rate, thd_b),
        "平均着順": f"{avg_rank:.2f}",
        "単勝回収値": f"{tan_return:.1f}",
        "複勝回収値": f"{fuku_return:.1f}",
        "単勝適正回収値": f"{tan_teki:.1f}",
        "提案勝率":   _fmt_pct(prop_win),
        "提案連対率": _fmt_pct(prop_ren),
        "提案複勝率": _fmt_pct(prop_fku),
        "提案2着率": _fmt_pct(prop_sec),
        "提案3着率": _fmt_pct(prop_thd),
    })

def _aggregate_one(df: pd.DataFrame, *, min_sample: int,
                   key_col: str, meta_cols: list,
                   rank_cands, winpay_cands, plcpay_cands, winodds_cands):
    """1本分の foragg_* DataFrame を集計して結果 DataFrame を返す"""
    rank_col = _pick_col(df, rank_cands)
    win_col  = _pick_col(df, winpay_cands)
    plc_col  = _pick_col(df, plcpay_cands)
    odds_col = _pick_col(df, winodds_cands)
    if (key_col not in df.columns) or (rank_col is None):
        return None, None, None, None

    # 集計（include_groups=False で将来仕様に備える）
    res = (
        df.groupby(key_col, dropna=False, sort=False)
          .apply(lambda g: _calc(g, rank_col, win_col, plc_col, odds_col), include_groups=False)
    )
    res_df = res.reset_index()  # key を列として戻す

    # メタ列を KEY ごとに付与（merge で安全）
    meta_exist = [c for c in meta_cols if c in df.columns]
    if meta_exist:
        meta = (
            df[[key_col] + meta_exist]
            .drop_duplicates(subset=[key_col])
        )
        res_df = meta.merge(res_df, on=key_col, how="right")

    # サンプル数フィルタ
    if "件数" in res_df.columns:
        res_df = res_df[res_df["件数"].fillna(0).astype(int) >= min_sample]

    return res_df, odds_col, win_col, plc_col

def _filtered_by_odds(df: pd.DataFrame, odds_col: str, threshold: float) -> pd.DataFrame:
    """単勝オッズが threshold 以上の行だけ残す（NaNは除外）"""
    if not odds_col or odds_col not in df.columns:
        return None
    o = pd.to_numeric(df[odds_col], errors="coerce")
    mask = o >= float(threshold)
    return df[mask].copy()

def _make_odds3p_name(path: str) -> str:
    """foragg_*.csv -> foragg_odds3p_*.csv に変換"""
    d, name = os.path.split(path)
    prefix = "foragg_"
    if name.startswith(prefix):
        return os.path.join(d, "foragg_odds3p_" + name[len(prefix):])
    # 念のためのフォールバック
    root, ext = os.path.splitext(name)
    return os.path.join(d, f"{root}_odds3p{ext}")

def main():
    files = sorted(glob.glob(os.path.join(IN_DIR, "foragg_*.csv")))
    if not files:
        print("no foragg_* found")
        return

    for path in files:
        try:
            df = pd.read_csv(path, encoding="shift_jis")

            # --- 通常集計（上書き） ---
            res_df, odds_col, win_col, plc_col = _aggregate_one(
                df,
                min_sample=MIN_SAMPLE,
                key_col=KEY_COL, meta_cols=META_COLS,
                rank_cands=RANK_CANDS, winpay_cands=WIN_PAY_CANDS,
                plcpay_cands=PLACE_PAY_CANDS, winodds_cands=WIN_ODDS_CANDS
            )
            if res_df is None:
                print(f"skip (missing columns): {os.path.basename(path)}")
            else:
                res_df.to_csv(path, index=False, encoding="shift_jis")
                print(f"  wrote: {os.path.basename(path)}")

            # --- オッズ版（単勝オッズ >= 3.0 のみ集計、別名で保存。オッズの変更はここで行う） ---
            if odds_col and odds_col in df.columns:
                df3 = _filtered_by_odds(df, odds_col, threshold=3.0)
                if df3 is not None and len(df3) > 0:
                    res3_df, *_ = _aggregate_one(
                        df3,
                        min_sample=MIN_SAMPLE,
                        key_col=KEY_COL, meta_cols=META_COLS,
                        rank_cands=RANK_CANDS, winpay_cands=WIN_PAY_CANDS,
                        plcpay_cands=PLACE_PAY_CANDS, winodds_cands=WIN_ODDS_CANDS
                    )
                    if res3_df is not None:
                        out3 = _make_odds3p_name(path)
                        res3_df.to_csv(out3, index=False, encoding="shift_jis")
                        print(f"  wrote (odds>=3.0): {os.path.basename(out3)}")
                else:
                    print(f"  skip odds>=3.0 (no rows): {os.path.basename(path)}")
            else:
                print(f"  skip odds>=3.0 (no odds col): {os.path.basename(path)}")

        except Exception as e:
            print(f"[ERROR] {os.path.basename(path)} : {e}")

if __name__ == "__main__":
    main()