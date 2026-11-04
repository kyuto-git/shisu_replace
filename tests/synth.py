"""テスト用の合成データ（本番 CSV と同じ列名・cp932）。

旧版で問題になった要素を意図的に含める:
  - ダートで 芝(内・外) が空欄（旧版: 'nan' 混入 / 空キー）
  - 騎手「髙杉吏麒」（旧版: shift_jis で「髙」が消えて出馬表と不一致）
  - クラス「Ｇ１」（旧版: 過去側だけ NFKC で G1 になり不一致）
  - 全頭0の指数レース・欠損2頭のレース（除外されるべき）
"""
from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

JOCKEYS = ["髙杉吏麒", "Ｃ．ルメール", "川田将雅", "横山武史", "戸崎圭太", "松山弘平", "岩田望来", "坂井瑠星",
           "武豊", "西村淳也", "菅原明良", "佐々木大輔", "丹内祐次", "鮫島克駿", "団野大成", "北村友一"]

# (場所, 芝・ダ, 距離, 芝(内・外), 重量種別, クラス名)
CONDITIONS = [
    ("東京", "ダ", "1600", "", "定量", "1勝"),
    ("東京", "芝", "1800", "", "別定", "ｵｰﾌﾟﾝ"),
    ("京都", "芝", "1600", "外", "定量", "2勝"),
    ("東京", "芝", "2400", "", "定量", "Ｇ１"),
]
VENUE_CODE = {"東京": "05", "京都": "08"}


def _race_rows(rng: random.Random, date: str, venue: str, race_no: int, cond, heads: int, index_mode: str):
    place, surface, dist, inner, weight, cls = cond
    rid16 = f"{date}{VENUE_CODE[venue]}0101{race_no:02d}"
    idx = [rng.randint(40, 120) for _ in range(heads)]
    if index_mode == "allzero":
        idx = [0] * heads
    order = sorted(range(heads), key=lambda i: -idx[i] + rng.gauss(0, 15))
    finish = [0] * heads
    for pos, i in enumerate(order, 1):
        finish[i] = pos
    ranks = {i: r for r, i in enumerate(sorted(range(heads), key=lambda i: -idx[i]), 1)}
    rows = []
    for i in range(heads):
        uma = i + 1
        waku = min(8, (i * 8) // heads + 1)
        odds = round(rng.uniform(1.5, 120.0), 1)
        fin = str(finish[i])
        if i == heads - 1 and race_no % 7 == 0:
            fin = "取消"
        rows.append({
            "レースID(新)": f"{rid16}{uma:02d}", "場所": place, "芝・ダ": surface, "距離": dist,
            "芝(内・外)": inner, "重量種別": weight, "クラス名": cls, "頭数": str(heads), "枠番": str(waku),
            "馬番": str(uma), "馬名": f"ウマ{date}{race_no}{uma}", "騎手": JOCKEYS[i % len(JOCKEYS)],
            "指数": "" if (index_mode == "missing2" and i < 2) else str(idx[i]),
            "指数順位": str(ranks[i]), "着順": fin, "単勝オッズ": f"{odds}",
            "単勝配当": str(int(odds * 100)) if fin == "1" else "0",
            "複勝配当": str(int(100 + odds * 20)) if fin in ("1", "2", "3") else "0",
        })
    return rows


def history_frame(seed: int = 1, years=(2022, 2023, 2024, 2025), races_per_year: int = 12) -> pd.DataFrame:
    rng = random.Random(seed)
    rows = []
    race_no = 0
    for y in years:
        for k in range(races_per_year):
            cond = CONDITIONS[k % len(CONDITIONS)]
            race_no = race_no % 12 + 1
            date = f"{y}{(k % 12) + 1:02d}{(k % 27) + 1:02d}"
            mode = "allzero" if k == 5 else ("missing2" if k == 6 else "normal")
            rows += _race_rows(rng, date, cond[0], race_no, cond, 16, mode)
    return pd.DataFrame(rows)


def write_cp932(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(df.to_csv(index=False).encode("cp932"))


def make_history(base: Path, indicators=("カツラキ", "ZI", "先行")) -> None:
    for n, ind in enumerate(indicators):
        df = history_frame(seed=10 + n)
        write_cp932(df, base / "csv_files" / f"主５場（中山、東京、中京、京都、阪神）_{ind}.csv")


def entries_frames(seed: int = 99) -> tuple[pd.DataFrame, pd.DataFrame]:
    """出馬表2ファイル（_1: カツラキ・ZI、_2: 先行力・マイニング値）。同じ馬・同じレース。"""
    rng = random.Random(seed)
    rows1, rows2 = [], []
    races = [
        ("東京", "ダ", "1600", "", "1勝", "１勝ｸﾗｽ", "４歳以上", 1),
        ("東京", "芝", "2400", "", "Ｇ１", "東京優駿G1", "４歳以上", 11),
        ("京都", "芝", "1600", "外", "2勝", "鴨川特別･2勝", "４歳以上", 9),
    ]
    for place, sd, dist, gai, cls, name, age, rno in races:
        heads = 16
        rid16 = f"20260531{VENUE_CODE[place]}0212{rno:02d}"
        k = [rng.randint(40, 120) for _ in range(heads)]
        z = [rng.randint(80, 130) for _ in range(heads)]
        s = [rng.randint(30, 90) for _ in range(heads)]
        rank = lambda v: {i: r for r, i in enumerate(sorted(range(heads), key=lambda i: -v[i]), 1)}
        rk, rz, rs = rank(k), rank(z), rank(s)
        for i in range(heads):
            common = {"レースID(新)": f"{rid16}{i + 1:02d}", "場 R": f"{place[0]}{rno}", "Ｒ": str(rno),
                      "場所": place, "芝ダ": sd, "距離": dist, "外回り": gai, "クラス": cls, "レース名": name,
                      "年限定": age, "頭": str(heads), "枠番": str(min(8, (i * 8) // heads + 1)), "馬番": str(i + 1),
                      "馬名S": f"エントリ{rno}_{i + 1}", "騎手": JOCKEYS[i % len(JOCKEYS)]}
            rows1.append({**common, "カツラキ": str(k[i]), "指数順位1": str(rk[i]), "ZI": str(z[i]), "指数順位2": str(rz[i])})
            rows2.append({**common, "先行力": str(s[i]), "指数順位1": str(rs[i]), "マイニング値": "", "マイニング順位": ""})
    return pd.DataFrame(rows1), pd.DataFrame(rows2)


def make_entries(base: Path) -> None:
    e1, e2 = entries_frames()
    write_cp932(e1, base / "entries_in" / "shisu_20260531_1.csv")
    write_cp932(e2, base / "entries_in" / "shisu_20260531_2.csv")
