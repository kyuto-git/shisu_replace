"""① Ω指数（競馬ラボ keibalab.jp の出馬表）。過去走・今週の出馬表の両方。

URL:  https://www.keibalab.jp/db/race/{rid12}/syutsuba.html
      rid12 = レースID(新) から 11〜14桁目（回・日）と 17〜18桁目（馬番）を除いた12桁（YYYYMMDD + 場所 + R）
解析: <th> に「Ω指数」を含む <table>。各行の td[0]=枠, td[1]=馬番, td[2]=馬名, td[Ω指数の列]=値
照合: rid12 + 枠番 + 馬番（旧 docker-keiba-omega と同じ）
出力: omega/{YYYYMMDD}_omega.csv ほか（common.write_outputs）
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from .common import (RID, Fetcher, Logger, Outputs, default_log, int_text, lookup, norm_cell, rid12,
                     series_of, to_number_text, unique_in_order, write_outputs)

NAME = "omega"
LABEL = "Ω指数（競馬ラボ）"
DEFAULT_INTERVAL = 3.0
URL = "https://www.keibalab.jp/db/race/{rid12}/syutsuba.html"


def race_url(r12: str) -> str:
    return URL.format(rid12=r12)


def parse(html: str) -> list[dict]:
    """[{枠, 馬番, 馬名, 値}]（枠・馬番は整数の文字列、値は数の文字列）。"""
    soup = BeautifulSoup(html, "lxml")
    for tbl in soup.find_all("table"):
        ths = [norm_cell(th.get_text()) for th in tbl.find_all("th")]
        idx = next((i for i, h in enumerate(ths) if "Ω指数" in h), None)
        if idx is None:
            continue
        rows = []
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) <= idx:
                continue
            txt = [norm_cell(td.get_text()) for td in tds]
            if "Ω指数" in "".join(txt):
                continue
            waku, uma, val = int_text(txt[0]), int_text(txt[1]), to_number_text(txt[idx])
            if uma and val:
                rows.append({"枠": waku, "馬番": uma, "馬名": txt[2] if len(txt) > 2 else "", "値": val})
        return rows
    return []


def base_keys(base: pd.DataFrame) -> pd.Series:
    r12 = base[RID].map(rid12)
    waku = series_of(base, "枠番").map(int_text)
    uma = series_of(base, "馬番").map(int_text)
    return (r12 + "_" + waku + "_" + uma).where((r12 != "") & (uma != ""), "")


def run(base: pd.DataFrame, fetcher: Fetcher, out_dir: Path, log: Logger = default_log) -> Outputs:
    races = unique_in_order(base[RID].map(rid12))
    log(f"[INFO] {LABEL}: {len(races)} レース")
    mapping: dict[str, str] = {}
    for i, r12 in enumerate(races, 1):
        html = fetcher.get(race_url(r12))
        rows = parse(html) if html else []
        for row in rows:
            mapping[f"{r12}_{row['枠']}_{row['馬番']}"] = row["値"]
        log(f"[INFO] {LABEL} {i}/{len(races)} {r12}: {len(rows)} 頭" + ("" if html else "（取得できず）"))
    return write_outputs(base, lookup(base_keys(base), mapping), Path(out_dir), NAME, log)
