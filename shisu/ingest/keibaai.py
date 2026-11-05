"""④ keibaAI指数（muryou-keiba-ai.jp）。連番ID（開始〜終了）を画面・引数で指定する。

URL:  https://muryou-keiba-ai.jp/predict/2021/01/06/{連番}/   （日付の部分はダミー。連番が正しければ転送される）
解析: <meta name="description">（無ければ <title>）から「YYYY年M月D日」・開催場（10場）・「(\\d{1,2})R」を取る。
      パターンA（旧表）: <th> に「AI」と「予想」を含む列。td[0]=馬番、その列の div.predict の数値
      パターンB（新表）: table.race_table tbody tr の .umaban_wrap（馬番）と .predict_wrap .predict（値）
照合: YYYYMMDD + 場所コード + R + 馬番 の14桁を、レースID(新) から 11〜14桁目を除いた14桁と突き合わせる
出力: keibaAI/{YYYYMMDD}_keibaAI.csv（同じ レースID(新) は1行。旧版の dedupe_keibaAI.py の処理も含む）
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from .common import (PLACE_CODES, RID, Fetcher, Logger, Outputs, default_log, key14, lookup, norm_cell,
                     to_number_text, write_outputs)

NAME = "keibaAI"
LABEL = "keibaAI指数"
DEFAULT_INTERVAL = 2.0
URL = "https://muryou-keiba-ai.jp/predict/2021/01/06/{seq}/"
_PLACE_RE = re.compile("(" + "|".join(PLACE_CODES) + ")")


def page_url(seq: int) -> str:
    return URL.format(seq=int(seq))


def parse(html: str) -> dict | None:
    """{date, place, race, rows: [(馬番2桁, 値)]}。開催情報か値が取れなければ None。"""
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "description"})
    text = meta.get("content", "") if meta is not None else ""
    if not text and soup.title is not None:
        text = soup.title.get_text(strip=True)
    md = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    mp = _PLACE_RE.search(text)
    mr = re.search(r"(\d{1,2})R", text)
    if not (md and mp and mr):
        return None
    date = f"{int(md.group(1)):04d}{int(md.group(2)):02d}{int(md.group(3)):02d}"
    rows = _pattern_a(soup) or _pattern_b(soup)
    if not rows:
        return None
    return {"date": date, "place": PLACE_CODES[mp.group(1)], "race": f"{int(mr.group(1)):02d}", "rows": rows}


def _pattern_a(soup) -> list[tuple[str, str]]:
    table = soup.find("table")
    if table is None:
        return []
    heads = [norm_cell(th.get_text()) for th in table.find_all("th")]
    col = next((i for i, h in enumerate(heads) if "AI" in h and "予想" in h), None)
    if col is None:
        return []
    out = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) <= col:
            continue
        div = tds[col].find("div", class_="predict")
        uma, val = norm_cell(tds[0].get_text()), to_number_text(div.get_text() if div else "")
        if uma.isdigit() and val:
            out.append((f"{int(uma):02d}", val))
    return out


def _pattern_b(soup) -> list[tuple[str, str]]:
    table = soup.select_one("table.race_table")
    if table is None:
        return []
    out = []
    for tr in table.select("tbody tr"):
        u, p = tr.select_one(".umaban_wrap"), tr.select_one(".predict_wrap .predict")
        if u is None or p is None:
            continue
        uma, val = norm_cell(u.get_text()), to_number_text(p.get_text())
        if uma.isdigit() and val:
            out.append((f"{int(uma):02d}", val))
    return out


def run(base: pd.DataFrame, fetcher: Fetcher, out_dir: Path, start_id: int, end_id: int,
        log: Logger = default_log) -> Outputs:
    start_id, end_id = int(start_id), int(end_id)
    if end_id < start_id:
        raise ValueError(f"keibaAI の終了ID（{end_id}）が開始ID（{start_id}）より小さい")
    total = end_id - start_id + 1
    log(f"[INFO] {LABEL}: ID {start_id}〜{end_id}（{total} ページ）")
    mapping: dict[str, str] = {}
    for i, seq in enumerate(range(start_id, end_id + 1), 1):
        html = fetcher.get(page_url(seq))
        info = parse(html) if html else None
        if info:
            for uma, val in info["rows"]:
                mapping[f"{info['date']}{info['place']}{info['race']}{uma}"] = val
            log(f"[INFO] {LABEL} {i}/{total} ID={seq}: {info['date']} {info['place']} {info['race']}R "
                f"{len(info['rows'])} 頭")
        else:
            log(f"[WARN] {LABEL} {i}/{total} ID={seq}: 値が取れませんでした")
    return write_outputs(base, lookup(base[RID].map(key14), mapping), Path(out_dir), NAME, log)
