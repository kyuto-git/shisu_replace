"""⑥ レースラップ指数（旧 lastXF-calculate の process_csv.py）。スクレイピングなし・手元で計算する。

入力: TARGET のレース検索（★5タブ）でワーク1にレースのラップタイムを読み込んで出力したCSV
  列: レースID(新), 日付S, 場所, 芝・ダ, 距離, コース区分, 馬場状態, クラス名, 年齢限定, 着順, 着差, 補正,
      レース印２, 1角, 2角, 3角, 4角, 人気, 単勝オッズ, ワーク1（例 " 7.1-11.0-11.7-12.0-12.2-12.3-12.4"）
前処理: 着順の全角数字を半角に → 着順が 消・止・外 の行、着順・着差が数でない行を除く
出力（日付S ごと。列名も旧版と同じ）:
  2F/{日付S}_2F.csv            末尾2ハロンの合計（2着以下は +着差）
  1F/{日付S}_1F.csv            末尾1ハロン + クラス補正（3着以内かつ 補正85以上だけ）、2歳 −0.2・3歳 −0.1
  4F/{日付S}_4F.csv            末尾4ハロンの合計（2着以下は +着差）、レース印２（不能 +3.5 / 数値は −値÷2）、
                               基準タイムとの差 → 48.0 − (基準 − 計算結果)
  33rap/{日付S}_33rap.csv      前半3F − 後半3F（ラップ6本以上。1150m は前半を ×6/5）、5本（1000m）は (2+3本目)−(4+5本目)
  rabbit_2F/{日付S}_rabbit_2F.csv  テン2F（1150m ×8/7、1300・1500・…・2500m ×4/3、四捨五入）
  rabbit_1F/{日付S}_rabbit_1F.csv  テン2F ÷ 2
  pace/{実行日}_pace_of_racecoment.csv  「逃げ馬2F：x、逃げ馬1F：y」のレースコメント
丸めは旧版と同じ Python の round（小数1桁）。テン2F だけ Decimal の四捨五入（ROUND_HALF_UP）。
旧版との違い: ラップの文字が数にならない行は落ちずに空欄にする（旧版はそこで止まった）。
"""
from __future__ import annotations

import datetime as _dt
import math
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Iterable

import pandas as pd

from ..csvio import read_csv_any, write_csv
from .common import DATE, RID, WORK, Logger, Outputs, default_log, ymd_text

NAME = "lap"
LABEL = "ラップ6指数"

CLASS_ADJ = {"1勝": 0.1, "2勝": 0.2, "3勝": 0.3, "ｵｰﾌﾟﾝ": 0.4, "OP(L)": 0.4, "Ｇ３": 0.5, "Ｇ２": 0.6, "Ｇ１": 0.7}
_CLASS_ADJ_N = {unicodedata.normalize("NFKC", k): v for k, v in CLASS_ADJ.items()}   # 全角・半角どちらでも
AGE_ADJ = {"2歳": -0.2, "3歳": -0.1}
DISTANCE_BASE = {
    "芝": {1000: 43.8, 1200: 45.8, 1400: 46.8, 1500: 47.7, 1600: 47.6, 1800: 48.8, 2000: 48.8, 2200: 49.2,
           2300: 49.9, 2400: 49.9, 2500: 49.6, 2600: 50.1, 3000: 50.4, 3200: 50.0, 3400: 50.5, 3600: 50.5},
    "ダ": {1000: 47.5, 1150: 48.1, 1200: 48.8, 1300: 49.3, 1400: 49.4, 1600: 49.9, 1700: 50.5, 1800: 50.8,
           1900: 50.7, 2000: 50.6, 2100: 51.1, 2400: 51.4, 2500: 51.1},
}
FOUR_THIRDS = (1300, 1500, 1700, 1900, 2100, 2300, 2500)
DROP_FINISH = ("消", "止", "外")
NAN = float("nan")


def _num(v) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return NAN


def _laps(v) -> list[float] | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return [float(x) for x in s.split("-")]
    except ValueError:
        return None


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """旧版の前処理。着順・着差・距離・補正を数にする（列は文字列で読んだもの）。"""
    df = df.copy()
    fin = df["着順"].astype(str).str.strip().map(lambda s: s.translate(str.maketrans("１２３４５６７８９０", "1234567890")))
    df = df[~fin.isin(DROP_FINISH)]
    df["着順"] = pd.to_numeric(fin[df.index], errors="coerce")
    df = df.dropna(subset=["着順"])
    df["着差"] = pd.to_numeric(df["着差"].astype(str).str.strip(), errors="coerce")
    df = df.dropna(subset=["着差"])
    df["距離"] = pd.to_numeric(df["距離"].astype(str).str.strip(), errors="coerce")
    df["補正"] = pd.to_numeric(df.get("補正", pd.Series("", index=df.index)).astype(str).str.strip(), errors="coerce")
    df[DATE] = df[DATE].map(ymd_text) if DATE in df.columns else df[RID].astype(str).str.slice(0, 8)
    return df


def calc_2f(laps, fin, diff) -> float:
    if laps is None or len(laps) < 2:
        return NAN
    w = round(sum(laps[-2:]), 1)
    return round(w + diff, 1) if fin >= 2 else w


def calc_1f(laps, fin, hosei, cls, age) -> float:
    if laps is None or not laps:
        return NAN
    w = round(float(laps[-1]), 1)
    if not (fin <= 3 and hosei >= 85):          # 補正が欠損（NaN）なら対象外（旧版と同じ）
        return NAN
    r = round(w + _CLASS_ADJ_N.get(unicodedata.normalize("NFKC", str(cls or "").strip()), 0), 1)
    adj = AGE_ADJ.get(unicodedata.normalize("NFKC", str(age or "").strip()))
    return round(r + adj, 1) if adj is not None else r


def calc_4f(laps, fin, diff, mark, surface, dist) -> float:
    if laps is None or len(laps) < 4:
        return NAN
    w = round(sum(laps[-4:]), 1)
    r = round(w + (diff if fin >= 2 else 0), 1)
    m = str(mark if mark is not None else "").strip()
    if m == "不能":
        r = round(r + 3.5, 1)
    else:
        if m == "":
            return NAN                              # 旧版: 空欄（NaN）を引くと結果も空欄
        try:
            v = float(m.replace("±", "").strip()) / 2
        except ValueError:
            v = 0
        r = round(r - v, 1)
    base = DISTANCE_BASE.get(str(surface or "").strip(), {})
    d = int(dist) if dist == dist and dist is not None and float(dist).is_integer() else None
    adjusted = round(base[d] - r, 1) if d in base else r
    return round(48.0 - adjusted, 1)


def calc_33(laps, dist) -> float:
    if laps is None:
        return NAN
    n = len(laps)
    if n >= 6:
        c1 = round(sum(laps[-3:]), 1)
        c2 = round(sum(laps[-6:-3]) * (6 / 5), 1) if dist == 1150 else round(sum(laps[-6:-3]), 1)
        return round(c2 - c1, 1)
    if n == 5:
        return round((laps[1] + laps[2]) - (laps[3] + laps[4]), 1)
    return NAN


def calc_rabbit(laps, dist) -> tuple[float, float, str | None]:
    if laps is None or len(laps) < 2:
        return NAN, NAN, None
    s = Decimal(str(laps[0] + laps[1]))
    if dist == 1150:
        s = s * Decimal("8") / Decimal("7")
    elif dist in FOUR_THIRDS:
        s = s * Decimal("4") / Decimal("3")
    two = float(s.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    one = float((s / Decimal("2")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return two, one, f"逃げ馬2F：{two:.1f}、逃げ馬1F：{one:.1f}"


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """前処理済みの DataFrame → 6指数とコメントの列を足したもの。"""
    out = {k: [] for k in ("2F", "1F", "4F", "33rap", "逃げ馬2F指数", "逃げ馬1F指数", "pace")}
    bad = 0
    for _, row in df.iterrows():
        laps = _laps(row.get(WORK))
        if laps is None and str(row.get(WORK) or "").strip():
            bad += 1
        fin, diff, dist = row["着順"], row["着差"], row["距離"]
        dist = None if dist != dist else dist
        out["2F"].append(calc_2f(laps, fin, diff))
        out["1F"].append(calc_1f(laps, fin, row["補正"], row.get("クラス名"), row.get("年齢限定")))
        out["4F"].append(calc_4f(laps, fin, diff, row.get("レース印２"), row.get("芝・ダ"), dist))
        out["33rap"].append(calc_33(laps, dist))
        two, one, comment = calc_rabbit(laps, dist)
        out["逃げ馬2F指数"].append(two)
        out["逃げ馬1F指数"].append(one)
        out["pace"].append(comment)
    res = df.copy()
    for k, v in out.items():
        res[k] = pd.Series(v, index=df.index, dtype=object if k == "pace" else float)
    res.attrs["bad_laps"] = bad
    return res


def run(paths: Iterable[Path | str], out_dir: Path, log: Logger = default_log,
        today: str | None = None) -> Outputs:
    frames = []
    for p in paths:
        raw = read_csv_any(p)
        missing = [c for c in (RID, "着順", "着差", "距離", WORK) if c not in raw.columns]
        if missing:
            log(f"[WARN] {Path(p).name}: 列 {missing} がありません（スキップ）")
            continue
        frames.append(raw)
        log(f"[INFO] ラップCSV {Path(p).name}: {len(raw)} 行")
    res = Outputs()
    if not frames:
        log("[WARN] ラップCSVがありません")
        return res
    raw = pd.concat(frames, ignore_index=True)
    df = compute(preprocess(raw))
    res.total = len(raw)
    res.matched = len(df)
    if df.attrs.get("bad_laps"):
        log(f"[WARN] ワーク1 のラップが数として読めない行: {df.attrs['bad_laps']}（空欄にしました）")
    out_dir = Path(out_dir)
    plan = (("2F", "2F", "計算結果"), ("1F", "1F", "計算結果"), ("4F", "4F", "計算結果"),
            ("33rap", "33rap", "計算結果"), ("rabbit_2F", "逃げ馬2F指数", "逃げ馬2F指数"),
            ("rabbit_1F", "逃げ馬1F指数", "逃げ馬1F指数"))
    rabbit = df[df["pace"].notna()]
    for folder, col, header in plan:
        src = rabbit if folder.startswith("rabbit") else df     # 旧版: テン2F はコメントが作れた行だけ
        for day, g in src.groupby(DATE, sort=True):
            if not day:
                continue
            path = out_dir / folder / f"{day}_{folder}.csv"
            write_csv(pd.DataFrame({RID: g[RID].values, header: g[col].values}), path)
            res.files.append(path)
    stamp = today or _dt.date.today().strftime("%Y%m%d")
    path = out_dir / "pace" / f"{stamp}_pace_of_racecoment.csv"
    write_csv(pd.DataFrame({RID: rabbit[RID].values, "計算結果": rabbit["pace"].values}), path)
    res.files.append(path)
    log(f"[INFO] {LABEL}: 入力 {len(raw)} 行 → 計算対象 {len(df)} 行（着順・着差が数でない行を除く）")
    return res
