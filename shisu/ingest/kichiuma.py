"""② 吉馬6指数（kichiuma.net）。過去走・今週の出馬表の両方。1レース1回アクセス。

URL:  https://kichiuma.net/php/search.php?race_id={YYYYMMDD}{R2}{id}&date={YYYY%2FM%2FD}&no={R}&id={id}&p=fp
      id = 場所コードの1桁目を 0→7, 1→8 に変えた2桁（中山06→76、小倉10→80）、no = R（先頭0なし）
解析: div#fp_form（無ければページ全体）の表のうち、ヘッダ行（上から4行以内）に「SP能力値」と「先行力」があるもの。
      colspan=2 の見出し（SP信頼・SP調整・SP最大・末脚力）は2列に展開し、右側（*_R）の数値を取る。
      馬 = 馬番、競走馬名 = 馬名
照合: レースID(新) から 11〜14桁目を除いた14桁（YYYYMMDD + 場所 + R + 馬番）
出力: 指数ごとのフォルダ kichiuma_{key}/{YYYYMMDD}_kichiuma_{key}.csv と {元ファイル名}_{key}_merged.csv（旧版と同じ名前）
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from .common import (RID, Fetcher, Logger, Outputs, default_log, int_text, key14, lookup, norm_cell,
                     to_number_text, unique_in_order, write_outputs)

NAME = "kichiuma"
LABEL = "吉馬6指数"
DEFAULT_INTERVAL = 2.0
# フォルダ・ファイル名のキー → (ページの見出し, 台帳の正式名)
INDEXES = {
    "sp_ability": ("SP能力値", "吉馬_SP能力値"),
    "senkou": ("先行力", "吉馬_先行力"),
    "sp_trust": ("SP信頼", "吉馬_SP信頼"),
    "sp_control": ("SP調整", "吉馬_SP調整"),
    "sp_max": ("SP最大", "吉馬_SP最大"),
    "sueashi": ("末脚力", "吉馬_末脚力"),
}


def race_url(rid: str) -> str:
    y, m, d = rid[0:4], int(rid[4:6]), int(rid[6:8])
    ninth = {"0": "7", "1": "8"}.get(rid[8], rid[8])
    id_param = f"{ninth}{rid[9]}"
    r2 = rid[14:16]
    return ("https://kichiuma.net/php/search.php?"
            f"race_id={rid[0:8]}{r2}{id_param}&date={y}%2F{m}%2F{d}&no={int(r2)}&id={id_param}&p=fp")


def parse(html: str) -> list[dict]:
    """[{馬番, 馬名, sp_ability, senkou, sp_trust, sp_control, sp_max, sueashi}]（値は数の文字列。無ければ ''）。"""
    soup = BeautifulSoup(html, "lxml")
    fp = soup.find("div", id="fp_form")
    tables = fp.find_all("table") if fp else soup.find_all("table")
    target, header = None, None
    for tbl in tables:
        trs = tbl.find_all("tr")
        if len(trs) < 2:
            continue
        for tr in trs[:4]:
            text = "".join(norm_cell(c.get_text()) for c in tr.find_all(["th", "td"]))
            if "SP能力値" in text and "先行力" in text:
                target, header = tbl, tr
                break
        if target is not None:
            break
    if target is None:
        return []

    expanded, bases = [], []
    for cell in header.find_all(["th", "td"]):
        title = norm_cell(cell.get_text())
        try:
            span = max(1, int(cell.get("colspan", "1") or "1"))
        except ValueError:
            span = 1
        if span == 1:
            expanded.append(title)
            bases.append(title)
        else:
            expanded += [f"{title}_L", f"{title}_R"] + [f"{title}_{k}" for k in range(2, span)]
            bases += [title] * span

    def find(name: str) -> int | None:
        for suffix in ("_R", "", "_L"):            # 右列 → 単独列 → 左列
            for i, (h, b) in enumerate(zip(expanded, bases)):
                if b == name and h == name + suffix:
                    return i
        return None

    idx = {"馬": find("馬"), "馬名": find("競走馬名")}
    for key, (label, _) in INDEXES.items():
        idx[key] = find(label)
    if any(v is None for v in idx.values()):
        return []

    rows, seen_header = [], False
    for tr in target.find_all("tr"):
        if tr is header:
            seen_header = True
            continue
        if not seen_header or tr.find("th"):
            continue
        tds = tr.find_all("td")
        if len(tds) < len(expanded):
            continue
        cell = lambda i: tds[i].get_text(strip=True)
        rec = {"馬番": int_text(cell(idx["馬"])), "馬名": cell(idx["馬名"])}
        for key in INDEXES:
            rec[key] = to_number_text(cell(idx[key]))
        if rec["馬番"] and rec["馬名"]:
            rows.append(rec)
    return rows


def run(base: pd.DataFrame, fetcher: Fetcher, out_dir: Path, log: Logger = default_log) -> Outputs:
    rids = [r for r in base[RID] if len(r) == 18]
    by_race: dict[str, str] = {}
    for r in sorted(rids):
        by_race.setdefault(r[:16], r)
    races = unique_in_order(r[:16] for r in rids)
    log(f"[INFO] {LABEL}: {len(races)} レース")
    maps: dict[str, dict[str, str]] = {k: {} for k in INDEXES}
    for i, race in enumerate(races, 1):
        sample = by_race[race]
        html = fetcher.get(race_url(sample))
        rows = parse(html) if html else []
        for row in rows:
            k = sample[:10] + sample[14:16] + f"{int(row['馬番']):02d}"
            for key in INDEXES:
                if row[key] != "":
                    maps[key][k] = row[key]
        log(f"[INFO] {LABEL} {i}/{len(races)} {race}: {len(rows)} 頭" + ("" if html else "（取得できず）"))
    keys = base[RID].map(key14)
    res = Outputs()
    for key in INDEXES:
        res.extend(write_outputs(base, lookup(keys, maps[key]), Path(out_dir) / f"kichiuma_{key}",
                                 f"kichiuma_{key}", log, merged_suffix=key))
    return res
