# -*- coding: utf-8 -*-
# aggregate_stats_ver2.py (dedup + proper finish handling + odds-weighted return) FIXED
import os, glob
import pandas as pd
import numpy as np

BASE_DIR = "/app"
IN_DIR   = os.path.join(BASE_DIR, "foragg_csv")

RID_COL         = "レースID(新)"
KEY_COL         = "結合データ"
FIN_COL         = "着順"
ODDS_WIN_COL    = "単勝オッズ"
PAY_WIN_COL     = "単勝配当"
PAY_PLC_COL     = "複勝配当"
HEAD_COLS       = ["頭数", "頭"]   # どちらかがあれば採用
BAND_COL        = "指数順位帯"     # あれば重複排除キーに加える

# === ユーザー調整可能なパラメータ ===
MIN_SAMPLE = 5            # N<MIN_SAMPLE のグループは出力しない
MIN_ODDS_FOR_WEIGHT = 0   # 0以下やNaNのオッズは重み無効（適正回収値の分母から除外）

# === 保守的（下振れ）推定の設定 ===
AGG_PROP_MODE = os.getenv("AGG_PROP_MODE", "lower").lower()  # "lower" or "mean"
AGG_LOWER_Q = float(os.getenv("AGG_LOWER_Q", "0.10"))        # 下側q（例: 0.10=片側90%下限）
AGG_UPPER_Q = float(os.getenv("AGG_UPPER_Q", "0.90"))        # 上側q（例: 0.90=片側90%上限）
PRIOR_ALPHA = float(os.getenv("AGG_PRIOR_ALPHA", "0.5"))     # Jeffreys既定
PRIOR_BETA  = float(os.getenv("AGG_PRIOR_BETA",  "0.5"))

# 複勝率だけ、頻度がこの閾値%以下なら“上限”を使う
PLC_SWITCH_THRESHOLD_PCT = float(os.getenv("PLC_SWITCH_THRESHOLD_PCT", "20.0"))

# === rankband スキーム選別（未指定なら全件） ===
# 例: export AGG_RANKBAND_SCHEMES="s1"    → rankband_s1 のみ集計
#     export AGG_RANKBAND_SCHEMES="s2"    → rankband_s2 のみ集計
#     export AGG_RANKBAND_SCHEMES="s1,s2" → s1/s2 のみ集計（非bandは常に対象）
AGG_RANKBAND_SCHEMES = os.getenv("AGG_RANKBAND_SCHEMES", "").strip().lower()

# === 互換用オプション：古い foragg_* で KEY に帯が入ってない場合の保険 ===
# 1 にすると、指数順位帯があれば KEY(=結合データ) に "×指数順位帯" を追記してから集計
AGG_FORCE_APPEND_BAND_TO_KEY = os.getenv("AGG_FORCE_APPEND_BAND_TO_KEY", "0") == "1"

def beta_lower_ppf_series(successes: pd.Series, n: pd.Series,
                          q: float = AGG_LOWER_Q,
                          a0: float = PRIOR_ALPHA, b0: float = PRIOR_BETA) -> pd.Series:
    import numpy as np
    from statistics import NormalDist
    a = successes.astype(float) + a0
    b = (n.astype(float) - successes.astype(float)) + b0
    try:
        from scipy.stats import beta as beta_dist
        return pd.Series(beta_dist.ppf(q, a, b), index=n.index)
    except Exception:
        mean = a / (a + b)
        var  = (a * b) / (((a + b) ** 2) * (a + b + 1.0))
        z = NormalDist().inv_cdf(q)
        lb = mean + z * np.sqrt(var)
        return pd.Series(np.clip(lb, 0.0, 1.0), index=n.index)

def beta_upper_ppf_series(successes: pd.Series, n: pd.Series,
                          q: float = AGG_UPPER_Q,
                          a0: float = PRIOR_ALPHA, b0: float = PRIOR_BETA) -> pd.Series:
    import numpy as np
    from statistics import NormalDist
    a = successes.astype(float) + a0
    b = (n.astype(float) - successes.astype(float)) + b0
    try:
        from scipy.stats import beta as beta_dist
        return pd.Series(beta_dist.ppf(q, a, b), index=n.index)
    except Exception:
        mean = a / (a + b)
        var  = (a * b) / (((a + b) ** 2) * (a + b + 1.0))
        z = NormalDist().inv_cdf(q)
        ub = mean + z * np.sqrt(var)
        return pd.Series(np.clip(ub, 0.0, 1.0), index=n.index)

# Jeffreys: Beta(0.5, 0.5) の事後平均
def jeffreys_mean(k, n):
    k = float(k); n = float(n)
    if n <= 0:
        return np.nan
    return (k + 0.5) / (n + 1.0)

def fmt_pct_pair(freq_pct, bayes_pct):
    """ 'xx.xx%（yy.yy%）' 形式。どちらか無ければある方だけ。 """
    f = None if pd.isna(freq_pct) else f"{freq_pct:.2f}%"
    b = None if pd.isna(bayes_pct) else f"{bayes_pct:.2f}%"
    if f and b:
        return f"{f}（{b}）"
    return f or (b or "")

def smart_read(path):
    for enc in ("shift_jis", "cp932", "utf-8-sig", "utf-8", "latin1"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except Exception:
            continue
    return pd.read_csv(path, dtype=str)

def to_num(s):
    return pd.to_numeric(s, errors="coerce")

def normalize_finish_and_flags(df):
    """
    着順の正規化と、的中/分母用フラグ作成（すべて Series を返す）。
    - 取消/除外: 分母から除外（denom=False）
    - 失格/中止: 分母に含める（denom=True、順位=頭数(無ければ18)）
    - 通常着順: 分母に含める、順位=int
    返す:
      finish_num: 平均着順のための数値（1..頭数）
      win_hit: 単勝的中（1着）
      plc_hit: 複勝的中（1~3着）
      second_hit, third_hit も同様
      board_hit: 掲示板（1~5着）
      denom: 分母に含めるか
    """
    idx = df.index
    s = df.get(FIN_COL, pd.Series("", index=idx)).astype(str)

    # レースごとの頭数（あれば）
    head = None
    for hcol in HEAD_COLS:
        if hcol in df.columns:
            head = to_num(df[hcol])
            break

    # 正規表現のキャプチャ無し（警告回避）
    is_cancel = s.str.contains(r"取消|除外", regex=True, na=False)
    is_dnf    = s.str.contains(r"失格|中止", regex=True, na=False)

    # 基本は数値化（通常着順）
    finish_num = to_num(s)

    # DNF は頭数（無ければ18）で置換（Series を維持するため mask を使用）
    if head is not None:
        fallback_head = head.fillna(18).clip(lower=1, upper=18)
        finish_num = finish_num.mask(is_dnf, fallback_head)
    else:
        finish_num = finish_num.mask(is_dnf, 18)

    # 分母: 取消/除外は除外
    denom = (~is_cancel).astype(bool)

    # 的中フラグ（Series 同士の演算）
    f_series = pd.to_numeric(finish_num, errors="coerce")
    win_hit    = f_series.eq(1) & denom
    second_hit = f_series.eq(2) & denom
    third_hit  = f_series.eq(3) & denom
    plc_hit    = f_series.le(3) & denom
    board_hit  = f_series.le(5) & denom

    return pd.DataFrame({
        "finish_num": f_series,
        "denom": denom,
        "win_hit": win_hit,
        "second_hit": second_hit,
        "third_hit": third_hit,
        "plc_hit": plc_hit,
        "board_hit": board_hit,
    }, index=idx)

def aggregate_one_file(path):
    base = os.path.basename(path)
    df = smart_read(path)

    # # 主要列が無ければスキップ
    # for c in [RID_COL, KEY_COL, FIN_COL]:
    #     if c not in df.columns:
    #         return None, None

    # レースIDはフォールバック許容
    rid_col = None
    if RID_COL in df.columns:
        rid_col = RID_COL
    else:
        for alt in ["レースID(新/馬番無)", "場 R"]:
            if alt in df.columns:
                rid_col = alt
                break
    if rid_col is None or KEY_COL not in df.columns or FIN_COL not in df.columns:
        return None, None


    # 結合キーの軽整形
    # df[KEY_COL] = df[KEY_COL].astype(str).str.replace("××", "×", regex=False).str.rstrip("×")

    # 結合キーの軽整形
    df[KEY_COL] = df[KEY_COL].astype(str).str.replace("××", "×", regex=False).str.rstrip("×")

    # 互換用：古い foragg_* で KEY に帯が含まれていないケースの保険
    # （replace_csv_ver3 の band 出力なら KEY に帯が入っているので通常は不要）
    if AGG_FORCE_APPEND_BAND_TO_KEY and (BAND_COL in df.columns):
        band = df[BAND_COL].astype(str).fillna("")
        df[KEY_COL] = (df[KEY_COL] + "×" + band).str.replace("××", "×", regex=False).str.rstrip("×")

    # --- 1) 重複排除（1頭=1件 を保証） ---
    # subset_keys = [RID_COL, KEY_COL]
    subset_keys = [rid_col, KEY_COL]
    if BAND_COL in df.columns:
        subset_keys.append(BAND_COL)
    # 1頭=1件 を保証（優先: 馬番 → 馬名 → 馬名S）
    has_horse_key = False
    for hk in ["馬番", "馬名", "馬名S"]:
        if hk in df.columns:
            subset_keys.append(hk)
            has_horse_key = True
            break
    if not has_horse_key:
        # ▼フォールバック（存在するものだけを追加）
        #   完全一意ではなくても、過剰な潰れを抑止する“最小限”の保険
        for hk in ["枠番","騎手","着順","単勝オッズ","複勝配当"]:
            if hk in df.columns and hk not in subset_keys:
                subset_keys.append(hk)
    df = df.drop_duplicates(subset=subset_keys, keep="first")

    # --- 2) 着順の正規化＋フラグ ---
    flags = normalize_finish_and_flags(df)

    # 分母に入る行のみを母集団に
    use = flags["denom"].fillna(False)
    if not use.any():
        # 全て取消/除外なら空出力にする
        # out = pd.DataFrame(columns=[
        #     KEY_COL, "件数", "勝率", "連対率", "複勝率", "2着率", "3着率",
        #     "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値",
        #     "提案勝率","提案連対率","提案複勝率","提案2着率","提案3着率",
        #     "場所","芝・ダ","距離","芝(内・外)","芝(内外)"
        # ])
        out = pd.DataFrame(columns=[
            KEY_COL, "件数",
            "勝率_val","勝率","連対率_val","連対率","複勝率_val","複勝率",
            "2着率_val","2着率","3着率_val","3着率",
            "掲示板率_val","掲示板率",
            "平均着順","単勝回収値","複勝回収値","単勝適正回収値",
            "提案勝率","提案連対率","提案複勝率","提案2着率","提案3着率",
            "場所","芝・ダ","距離","芝(内・外)","芝(内外)"
        ])
        return base, out

    df_use    = df.loc[use].copy()
    flags_use = flags.loc[use].copy()

    # 配当・オッズ（数値）
    pay_win  = to_num(df_use.get(PAY_WIN_COL, 0)).fillna(0)
    pay_plc  = to_num(df_use.get(PAY_PLC_COL, 0)).fillna(0)
    odds_win = to_num(df_use.get(ODDS_WIN_COL, np.nan))

    # --- 3) 集計 ---
    gkey = KEY_COL
    # 件数（= 分母）：重複排除後の素カウント
    n = df_use.groupby(gkey, dropna=False).size()

    # 勝/2/3/複 のカウント（flags_use は df_use と同一 index）
    wins   = flags_use["win_hit"].groupby(df_use[gkey], dropna=False).sum()
    sec    = flags_use["second_hit"].groupby(df_use[gkey], dropna=False).sum()
    thr    = flags_use["third_hit"].groupby(df_use[gkey], dropna=False).sum()
    places = flags_use["plc_hit"].groupby(df_use[gkey], dropna=False).sum()
    boards = flags_use["board_hit"].groupby(df_use[gkey], dropna=False).sum()

    # 率（頻度）
    win_pct = (wins / n * 100.0).replace([np.inf, -np.inf], np.nan)
    ren_pct = ((wins + sec) / n * 100.0).replace([np.inf, -np.inf], np.nan)
    plc_pct = (places / n * 100.0).replace([np.inf, -np.inf], np.nan)
    sec_pct = (sec / n * 100.0).replace([np.inf, -np.inf], np.nan)
    thr_pct = (thr / n * 100.0).replace([np.inf, -np.inf], np.nan)
    top5_pct = (boards / n * 100.0).replace([np.inf, -np.inf], np.nan)  # 掲示板率（頻度）

    # 率（Jeffreys ベイズの平均）←従来保持
    win_b = pd.Series([jeffreys_mean(w, nn) for w, nn in zip(wins, n)], index=n.index) * 100.0
    ren_b = pd.Series([jeffreys_mean(w+s, nn) for w, s, nn in zip(wins, sec, n)], index=n.index) * 100.0
    # plc_b = pd.Series([jeffreys_mean(p, nn) for p, nn in zip(places, n)], index=n.index) * 100.0
    sec_b = pd.Series([jeffreys_mean(s, nn) for s, nn in zip(sec, n)], index=n.index) * 100.0
    thr_b = pd.Series([jeffreys_mean(t, nn) for t, nn in zip(thr, n)], index=n.index) * 100.0

    # 率（Jeffreys 事後の片側分位）
    win_lb = beta_lower_ppf_series(wins,       n) * 100.0
    ren_lb = beta_lower_ppf_series(wins + sec, n) * 100.0
    plc_lb = beta_lower_ppf_series(places,     n) * 100.0
    sec_lb = beta_lower_ppf_series(sec,        n) * 100.0
    thr_lb = beta_lower_ppf_series(thr,        n) * 100.0

    # 複勝率用の“上側”も計算
    plc_ub = beta_upper_ppf_series(places, n) * 100.0

    # まずの基本（勝/連/2着/3着）は設定に従って 下限 or 平均
    if AGG_PROP_MODE == "lower":
        est_win, est_ren, est_sec, est_thr = win_lb, ren_lb, sec_lb, thr_lb
    else:
        est_win, est_ren, est_sec, est_thr = win_b,  ren_b,  sec_b,  thr_b

    # ★ 複勝率だけは「頻度が閾値以下なら上側、そうでなければ下側」
    #    閾値判定は“観測頻度（plc_pct）”で行う。NaNは下限扱いのまま。
    use_upper_mask = (plc_pct <= PLC_SWITCH_THRESHOLD_PCT).fillna(False)
    est_plc = plc_lb.copy()
    est_plc[use_upper_mask] = plc_ub[use_upper_mask]

    # 表示用（左=頻度, 括弧=推定）
    win_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(win_pct, est_win)], index=n.index)
    ren_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(ren_pct, est_ren)], index=n.index)
    plc_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(plc_pct, est_plc)], index=n.index)
    sec_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(sec_pct, est_sec)], index=n.index)
    thr_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(thr_pct, est_thr)], index=n.index)
    # 掲示板は頻度のみ（推定は未導入）
    import numpy as _np
    top5_col = pd.Series([fmt_pct_pair(f, _np.nan) for f in top5_pct], index=n.index)

    # 「提案*」は推定値のみ（=上で決めた est_*）
    prop_win = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_win], index=n.index)
    prop_ren = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_ren], index=n.index)
    prop_plc = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_plc], index=n.index)  # ★条件適用済み
    prop_sec = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_sec], index=n.index)
    prop_thr = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_thr], index=n.index)

    # 平均着順：DNF は頭数（無ければ18）として含める
    avg_fin = flags_use.assign(__key=df_use[gkey]).groupby("__key", dropna=False)["finish_num"].mean()

    # 回収値（100円均等買い想定）
    # 単勝
    sum_pay_win = (pay_win.where(flags_use["win_hit"], 0)).groupby(df_use[gkey], dropna=False).sum()
    rtn_win = (sum_pay_win / n).replace([np.inf, -np.inf], np.nan)

    # 複勝
    sum_pay_plc = (pay_plc.where(flags_use["plc_hit"], 0)).groupby(df_use[gkey], dropna=False).sum()
    rtn_plc = (sum_pay_plc / n).replace([np.inf, -np.inf], np.nan)

    # === 単勝適正回収値（オッズ逆数加重・100円スケール） ===
    #   w_i = 1 / 単勝オッズ_i （オッズ<=0やNaNは重み無効）
    #   Return = sum_i 1{win} * w_i * (単勝配当_i / 100)
    #   Invest = sum_i w_i
    #   回収値 = 100 * Return / Invest
    w_raw = pd.Series(np.where(odds_win > MIN_ODDS_FOR_WEIGHT, 1.0 / odds_win, np.nan), index=df_use.index)
    w_sum = w_raw.groupby(df_use[gkey], dropna=False).sum(min_count=1)
    w_ret = (w_raw * (pay_win / 100.0) * flags_use["win_hit"].astype(float)).groupby(df_use[gkey], dropna=False).sum(min_count=1)
    rtn_win_adj = (100.0 * w_ret / w_sum).replace([np.inf, -np.inf], np.nan)

    # # 表示用フォーマット（頻度＋“推定値”）※推定値=下限 or ベイズ平均（設定）
    # win_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(win_pct, est_win)], index=n.index)
    # ren_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(ren_pct, est_ren)], index=n.index)
    # plc_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(plc_pct, est_plc)], index=n.index)
    # sec_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(sec_pct, est_sec)], index=n.index)
    # thr_col = pd.Series([fmt_pct_pair(f, e) for f, e in zip(thr_pct, est_thr)], index=n.index)

    # # 提案* は“推定値”を単独表記
    # prop_win = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_win], index=n.index)
    # prop_ren = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_ren], index=n.index)
    # prop_plc = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_plc], index=n.index)
    # prop_sec = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_sec], index=n.index)
    # prop_thr = pd.Series([f"{b:.2f}%" if not pd.isna(b) else "" for b in est_thr], index=n.index)

    # 参考列（グループ内で最頻 or 最初の非空）
    def first_nonempty(g):
        for v in g:
            if isinstance(v, str) and v.strip() != "":
                return v
        return ""

    ref_cols = {}
    for cname in ["場所","芝・ダ","距離","芝(内・外)","芝(内外)"]:
        if cname in df_use.columns:
            ref_cols[cname] = df_use.groupby(gkey, dropna=False)[cname].agg(first_nonempty)
        else:
            ref_cols[cname] = pd.Series("", index=n.index)

    out = pd.DataFrame({
        KEY_COL: n.index,
        "件数": n.astype(int),
        "勝率_val": win_pct.round(2).values,
        "勝率": win_col.values,
        "連対率_val": ren_pct.round(2).values,
        "連対率": ren_col.values,
        "複勝率_val": plc_pct.round(2).values,
        "複勝率": plc_col.values,
        "2着率_val": sec_pct.round(2).values,
        "掲示板率_val": top5_pct.round(2).values,
        "掲示板率": top5_col.values,
        "2着率": sec_col.values,
        "3着率_val": thr_pct.round(2).values,
        "3着率": thr_col.values,
        "平均着順": avg_fin.round(2).values,
        "単勝回収値": rtn_win.round(2).values,
        "複勝回収値": rtn_plc.round(2).values,
        "単勝適正回収値": rtn_win_adj.round(2).values,
        "提案勝率": prop_win.values,
        "提案連対率": prop_ren.values,
        "提案複勝率": prop_plc.values,
        "提案2着率": prop_sec.values,
        "提案3着率": prop_thr.values,
        "場所": ref_cols["場所"].reindex(n.index).values,
        "芝・ダ": ref_cols["芝・ダ"].reindex(n.index).values,
        "距離": ref_cols["距離"].reindex(n.index).values,
        "芝(内・外)": ref_cols["芝(内・外)"].reindex(n.index).values,
        "芝(内外)": ref_cols["芝(内外)"].reindex(n.index).values,
    })

    # N<MIN_SAMPLE は出力しない
    out = out[out["件数"] >= MIN_SAMPLE].reset_index(drop=True)

    # SJISで保存（元ファイルを上書き）
    # out = out.fillna("")
    string_cols = [
        KEY_COL, "勝率","連対率","複勝率","掲示板率","2着率","3着率",
        "提案勝率","提案連対率","提案複勝率","提案2着率","提案3着率",
        "場所","芝・ダ","距離","芝(内・外)","芝(内外)"
    ]
    for c in string_cols:
        if c in out.columns:
            out[c] = out[c].fillna("")
    # 数値列は NaN のままで OK（CSV では空欄で出力され、型も維持される）

    out_path = path
    try:
        out.to_csv(out_path, index=False, encoding="shift_jis")
    except Exception:
        out.to_csv(out_path, index=False)

    return base, out

# def main():
#     files = sorted(glob.glob(os.path.join(IN_DIR, "foragg_*.csv")))
#     if not files:
#         print("no foragg_*.csv found.")
#         return

#     for p in files:
#         try:
#             base, out = aggregate_one_file(p)
#             if out is None:
#                 print(f"[SKIP] {os.path.basename(p)}: required columns missing")
#             else:
#                 print(f"[OK] {base}: {len(out)} groups")
#         except Exception as e:
#             print(f"[ERROR] {os.path.basename(p)}: {e}")

def main():
    files = sorted(glob.glob(os.path.join(IN_DIR, "foragg_*.csv")))
    if not files:
        print("no foragg_*.csv found.")
        return

    # rankband スキームの選別（未指定なら全件）
    schemes = {s.strip() for s in AGG_RANKBAND_SCHEMES.split(",") if s.strip()}
    if schemes:
        def _take(path: str) -> bool:
            base = os.path.basename(path).lower()
            is_s1 = "rankband_s1_" in base
            is_s2 = "rankband_s2_" in base
            # 非bandは常に対象
            if (not is_s1) and (not is_s2):
                return True
            # band は指定されたスキームのみ対象
            if is_s1 and ("s1" in schemes): return True
            if is_s2 and ("s2" in schemes): return True
            return False
        files = [p for p in files if _take(p)]
        print(f"[INFO] AGG_RANKBAND_SCHEMES={AGG_RANKBAND_SCHEMES} → targets={len(files)} files")

    for p in files:
        try:
            base, out = aggregate_one_file(p)
            if out is None:
                print(f"[SKIP] {os.path.basename(p)}: required columns missing")
            else:
                print(f"[OK] {base}: {len(out)} groups")
        except Exception as e:
            print(f"[ERROR] {os.path.basename(p)}: {e}")


if __name__ == "__main__":
    main()