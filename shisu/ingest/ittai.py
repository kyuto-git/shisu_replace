"""⑤ 一隊（ittai.net）結果分析スピード指数。過去走用。

URL:  https://ittai.net/RK/race_index.php?type=result&id={レースID(新)の先頭16桁}&mode=1（Shift-JIS のページ）
解析: <th> が「指数」の <table>（無ければ「指数」を含む <th> の表）。td[1]=馬番、td[3]=馬名、td[-1] の <strong> の数値
照合: 先頭16桁 + 馬番2桁 = レースID(新)（18桁の完全一致）
出力: ittai/{YYYYMMDD}_ittai.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from .common import RID, Fetcher, Logger, Outputs, default_log, lookup, norm_cell, to_number_text, write_outputs

NAME = "ittai"
LABEL = "一隊（結果分析スピード指数）"
DEFAULT_INTERVAL = 2.0
ENCODING = "cp932"          # Shift-JIS（cp932 は Shift-JIS の上位互換。①や髙も読める）


def race_url(b16: str) -> str:
    return f"https://ittai.net/RK/race_index.php?type=result&id={b16}&mode=1"


def parse(html: str) -> dict[str, tuple[str, str]]:
    """{馬番2桁: (馬名, 値)}"""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    target = None
    for exact in (True, False):
        for tbl in tables:
            ths = [norm_cell(th.get_text()) for th in tbl.find_all("th")]
            if (exact and "指数" in ths) or (not exact and any("指数" in h for h in ths)):
                target = tbl
                break
        if target is not None:
            break
    if target is None:
        return {}
    out = {}
    for tr in target.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        num = norm_cell(tds[1].get_text())
        if not num.isdigit():
            continue
        a = tds[3].find("a")
        name = (a or tds[3]).get_text(strip=True)
        strong = tds[-1].find("strong")
        val = to_number_text(strong.get_text()) if strong is not None else ""
        if val:
            out[f"{int(num):02d}"] = (name, val)
    return out


def run(base: pd.DataFrame, fetcher: Fetcher, out_dir: Path, log: Logger = default_log) -> Outputs:
    races = sorted({r[:16] for r in base[RID] if len(r) == 18})
    log(f"[INFO] {LABEL}: {len(races)} レース")
    mapping: dict[str, str] = {}
    for i, b16 in enumerate(races, 1):
        html = fetcher.get(race_url(b16), encoding=ENCODING)
        rows = parse(html) if html else {}
        for uma, (_name, val) in rows.items():
            mapping[b16 + uma] = val
        log(f"[INFO] {LABEL} {i}/{len(races)} {b16}: {len(rows)} 頭" + ("" if html else "（取得できず）"))
    return write_outputs(base, lookup(base[RID], mapping), Path(out_dir), NAME, log)
