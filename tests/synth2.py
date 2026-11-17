"""第2弾（外部レース印の前走条件・近5走の要約条件・条件のみの family）のテスト用の合成データ。

同じ馬が何度も走り、各行の前走の列（前走レースID(新) など）はその馬の実際の前走を指す（レース印を前走RID16 で引けるように）。
  site_data()        基本CSV（★6タブの形。指数 = タイムM）・レースごとの印・出馬表（次の開催日）
  write_site(root)   csv_files/全場_タイムM.csv・race_marks/・entries_in/ を書く
"""
from __future__ import annotations

import datetime as dt
import random
import zipfile
from pathlib import Path

import pandas as pd

RID = "レースID(新)"
HORSES = [(f"20201{n:05d}", f"ホース{n}") for n in range(1, 61)]
JOCKEYS = ["川田将雅", "Ｃ．ルメール", "横山武史", "戸崎圭太", "松山弘平", "坂井瑠星"]
COURSES = [("東京", "05", "芝", 1600), ("東京", "05", "芝", 2000), ("東京", "05", "ダ", 1400), ("東京", "05", "ダ", 1600),
           ("中山", "06", "芝", 1800), ("中山", "06", "ダ", 1200)]
PLACE_CHAR = {"東京": "東", "中山": "中"}
STYLES = (("逃げ", "逃"), ("先行", "先"), ("中団", "中"), ("後方", "後"))
PACES = ("HHH", "HH", "H", "M", "S", "SS", "SSS")
ENTRY_DATE = "20260530"


def _fw(s: str) -> str:
    return s.translate(str.maketrans("0123456789ABCDEMHS", "０１２３４５６７８９ＡＢＣＤＥＭＨＳ"))


def site_data(seed: int = 5, days: int = 44):
    """(基本CSV の DataFrame, {レースID16: 印}, 出馬表の DataFrame, {レースID16: 開催名})。"""
    rng = random.Random(seed)
    rows: list[dict] = []
    marks: dict[str, dict] = {}
    names: dict[str, str] = {}
    last: dict[str, dict] = {}              # 馬ID → その馬のいちばん新しい走（前走の列の元）
    day0 = dt.date(2024, 3, 2)

    def prev_cols(hid: str, date: str) -> dict:
        p = last.get(hid)
        if p is None:
            return {"前走レースID(新)": "", "前走場所": "", "前芝・ダ": "", "前距離": "", "前走着順": "", "前走人気": "",
                    "前走上り3F順": "", "前走脚質": "", "前走レース印３": "", "前走斤量": "", "間隔": ""}
        weeks = max(1, (dt.date(int(date[:4]), int(date[4:6]), int(date[6:])) - p["day"]).days // 7)
        return {"前走レースID(新)": p["rid18"], "前走場所": p["place"], "前芝・ダ": p["sd"], "前距離": str(p["dist"]),
                "前走着順": p["fin"], "前走人気": str(p["pop"]), "前走上り3F順": str(p["agari"]), "前走脚質": p["style"][0],
                "前走レース印３": p["r3"], "前走斤量": "56", "間隔": str(weeks)}

    for d in range(days):
        day = day0 + dt.timedelta(days=13 * d)
        date = day.strftime("%Y%m%d")
        kai, nichi = 1 + d % 5, 1 + d % 12                      # 日 10・11・12 は RX の A・B・C
        for r in range(1, 4):
            place, code, sd, dist = rng.choice(COURSES)
            rid16 = f"{date}{code}{kai:02d}{nichi:02d}{r:02d}"
            names[rid16] = f"{date[:4]}年{kai}回{place}{nichi}日目{r:02d}R"
            pace = rng.choice(PACES)
            marks[rid16] = {
                "member": rng.choice(["A", "B", "C", "D", "E", "－"]), "time": rng.choice(["S", "A", "B", "C", "D", "E", "－"]),
                "pace": pace, "cushion": f"{rng.uniform(7.5, 11.5):.1f}" if sd == "芝" else "----",
                "moist_turf": f"{rng.uniform(6.0, 19.0):.1f}", "moist_dirt": f"{rng.uniform(1.5, 14.0):.1f}"}
            horses = rng.sample(HORSES, 12)
            order = list(range(12))
            rng.shuffle(order)
            pops = list(range(1, 13))
            rng.shuffle(pops)
            idx = [rng.randint(40, 110) for _ in range(12)]
            rank = {i: k for k, i in enumerate(sorted(range(12), key=lambda i: -idx[i]), 1)}
            for i, (hid, name) in enumerate(horses):
                uma = i + 1
                x = rng.random()
                fin = "取消" if x < 0.03 else ("中止" if x < 0.05 else str(order[i] + 1))
                odds = round(rng.uniform(1.5, 90.0), 1)
                # 基本CSVの 前走レース印３: 半分は空（ペース判定の印で補う）。値があるときは、印と違う値でも基本CSVが優先
                r3_shown = "" if rng.random() < 0.5 else rng.choice(["HH", "M", "S"])
                row = {RID: f"{rid16}{uma:02d}", "場所": place, "距離": f"{sd}{dist}", "馬場状態": rng.choice(["良", "稍", "重"]),
                       "頭数": "12", "枠番": str(min(8, (uma + 1) // 2)), "馬番": str(uma), "馬名": name, "血統登録番号": hid,
                       "騎手": JOCKEYS[(i + d) % len(JOCKEYS)], "指数": str(idx[i]), "指数順位": str(rank[i]), "着順": fin,
                       "単勝オッズ": f"{odds}", "単勝配当": str(int(odds * 100)) if fin == "1" else "0",
                       "複勝配当": str(int(100 + odds * 20)) if fin in ("1", "2", "3") else "0", "クラス名": "1勝",
                       "重量種別": "定量", "年齢限定": "3上", "レース名": "", "所属": "(美)", "休み明け～戦目": "2", "斤量": "56",
                       **prev_cols(hid, date)}
                rows.append(row)
                if fin != "取消":            # 取消の走は前走にならない
                    last[hid] = {"rid18": row[RID], "day": day, "place": place, "sd": sd, "dist": dist,
                                 "fin": fin if fin.isdigit() else "", "pop": pops[i], "agari": rng.randint(1, 12),
                                 "style": rng.choice(STYLES), "r3": r3_shown}
    base = pd.DataFrame(rows)

    ent_rows = []
    for r, (place, code, sd, dist) in enumerate([COURSES[0], COURSES[3]], 1):
        rid16 = f"{ENTRY_DATE}{code}0301{r:02d}"
        for uma, (hid, name) in enumerate(rng.sample(HORSES, 12), 1):
            p = last.get(hid)
            e = {RID: f"{rid16}{uma:02d}", "場 R": f"{place[0]}{r}", "Ｒ": str(r), "場所": place, "芝ダ": sd, "距離": str(dist),
                 "外回り": "", "クラス": "1勝", "レース名": f"テスト{r}", "年限定": "3上", "頭": "12",
                 "枠番": str(min(8, (uma + 1) // 2)), "馬番": str(uma), "馬名S": name, "血統登録番号": hid,
                 "騎手": JOCKEYS[uma % len(JOCKEYS)], "所属": "美浦", "間隔": "3", "休明戦目": "2", "斤量": "56", "性": "牡",
                 "マイニング値": str(rng.randint(40, 110)), "マイニング順位": str(uma)}
            if p is not None:
                k, n, rr = int(p["rid18"][10:12]), int(p["rid18"][12:14]), int(p["rid18"][14:16])
                e.update({"前場所": p["place"], "前芝ダ": p["sd"], "前距離": str(p["dist"]),
                          "前走開催": f"{p['rid18'][2:8]} {k}{PLACE_CHAR[p['place']]}{n} {rr:>2}R",
                          "前着順": f"{p['fin']}着" if p["fin"] else "", "前人気": str(p["pop"]), "前3F順": str(p["agari"]),
                          "前脚質": p["style"][1], "前レース印3": p["r3"], "前斤量": "56"})
            ent_rows.append(e)
    return base, marks, pd.DataFrame(ent_rows).fillna(""), names


def rx(rid16: str) -> str:
    """テスト用の RX の作り方（shisu.race_marks.rx_of とは別に書いたもの）。"""
    day = int(rid16[12:14])
    return "RX" + rid16[8:10] + rid16[2:4] + str(int(rid16[10:12])) + (str(day) if day < 10 else "ABCDEFG"[day - 10]) + rid16[14:16]


def mark_lines(marks: dict, names: dict, kind: str, rng: random.Random) -> list[str]:
    """レース印のファイルの行（TARGET の形。カラーコード2桁 + 値。全角・空白つき）。"""
    out = []
    for rid16 in sorted(marks):
        m, head = marks[rid16], f'"{names[rid16]}"'
        code = rng.choice(["00", "03", "05", "09", "0D", "0E"])
        if kind == "moisture":
            out.append(f'{head},"{rx(rid16)}","{code}{m["moist_turf"]:>4}"')
            out.append(f'{head},"{rx(rid16)}","0D{m["moist_dirt"]:>4}"')
        elif kind == "cushion":
            v = m["cushion"]
            out.append(f'{head},"{rx(rid16)}","{"09----" if v == "----" else code + f"{v:>4}"}"')
        elif kind == "pace":
            out.append(f'{head},{rx(rid16)},"{code}{m["pace"]:<3} "')
        else:
            out.append(f'{head},{rx(rid16)},"{code} {_fw(m[kind])} "')
    return out


def write_site(root: Path, seed: int = 5, days: int = 44, marks_as: str = "files"):
    """csv_files/全場_タイムM.csv・race_marks/（5種。1つは ZIP の中、1つはサブフォルダ・UTF-8）・entries_in/ を書く。"""
    base, marks, ent, names = site_data(seed, days)
    root = Path(root)
    (root / "csv_files").mkdir(parents=True, exist_ok=True)
    base.to_csv(root / "csv_files" / "全場_タイムM.csv", index=False, encoding="cp932")
    (root / "entries_in").mkdir(exist_ok=True)
    ent.to_csv(root / "entries_in" / f"shisu_{ENTRY_DATE}_1.csv", index=False, encoding="cp932")
    if marks_as == "files":
        write_marks(root / "race_marks", marks, names)
    return base, marks, ent, names


def write_marks(d: Path, marks: dict, names: dict, seed: int = 1) -> None:
    rng = random.Random(seed)
    d = Path(d)
    (d / "sub").mkdir(parents=True, exist_ok=True)
    text = lambda kind: "\r\n".join(mark_lines(marks, names, kind, rng)) + "\r\n"
    (d / "member2026.csv").write_bytes(text("member").encode("cp932"))
    (d / "sub" / "TIME2026.CSV").write_bytes(text("time").encode("utf-8"))
    (d / "pace_decision2026.csv").write_bytes(text("pace").encode("cp932"))
    with zipfile.ZipFile(d / "marks.zip", "w") as z:
        z.writestr("in/cushion2026.csv", text("cushion").encode("cp932"))
    (d / "moistureG_2026.csv").write_bytes(text("moisture").encode("cp932"))
