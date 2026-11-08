"""③ かよちん指数（kayochinkeiba.com）。2022/9 以降の過去走・今週の出馬表。1開催日×1場所につき1回アクセス。

URL:  https://kayochinkeiba.com/{YYMMDD}{場所の英語名}/   例: 251019niigata
      場所: 01 sapporo, 02 hakodate, 03 fukushima, 04 niigata, 05 tokyo, 06 nakayama,
            07 cyuukyo（中京）, 08 kyouto（京都）, 09 hanshin, 10 kokura
解析: すべての <table> の <tr> を見て、td[0] が「1R」〜「12R」で始まる行を馬番行、その直後の td[0] が「指」の行を指数行とする。
      馬番行の td[1:] の丸数字（①〜⑳）と、指数行の td[1:] の整数を、列の位置で対にする
      （旧版は空のセルを詰めてから対にしていたので、途中に空欄があると馬番と値がずれた）
照合: (YYMMDD+場所, R, 馬番)
出力: kayochin/{YYYYMMDD}_kayochin.csv
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from .common import RID, Fetcher, Logger, Outputs, default_log, lookup, unique_in_order, write_outputs

NAME = "kayochin"
LABEL = "かよちん指数"
DEFAULT_INTERVAL = 3.0
PLACES = {"01": "sapporo", "02": "hakodate", "03": "fukushima", "04": "niigata", "05": "tokyo",
          "06": "nakayama", "07": "cyuukyo", "08": "kyouto", "09": "hanshin", "10": "kokura"}


def page_key(rid: str) -> str:
    place = PLACES.get(rid[8:10]) if len(rid) >= 10 else None
    return f"{rid[2:8]}{place}" if place else ""


def page_url(key: str) -> str:
    return f"https://kayochinkeiba.com/{key}/"


def _horse_no(text: str) -> int | None:
    s = unicodedata.normalize("NFKC", text or "").strip()   # ⑫ → 12
    return int(s) if re.fullmatch(r"\d{1,2}", s) else None


def _value(text: str) -> int | None:
    m = re.search(r"-?\d+", unicodedata.normalize("NFKC", text or ""))
    return int(m.group()) if m else None


def parse(html: str) -> dict[int, list[tuple[int, int]]]:
    """{R: [(馬番, 指数), ...]}"""
    soup = BeautifulSoup(html, "lxml")
    data: dict[int, list[tuple[int, int]]] = {}
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        for i, tr in enumerate(trs):
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            m = re.match(r"(\d{1,2})R", unicodedata.normalize("NFKC", tds[0]).strip()) if tds else None   # 「11R 15:25」なども可（旧版と同じ前方一致）
            if not m or i + 1 >= len(trs):
                continue
            nxt = [td.get_text(strip=True) for td in trs[i + 1].find_all("td")]
            if not nxt or nxt[0] != "指":
                continue
            pairs = []
            for h, v in zip(tds[1:], nxt[1:]):
                hn, iv = _horse_no(h), _value(v)
                if hn is not None and iv is not None:
                    pairs.append((hn, iv))
            if pairs:
                data[int(m.group(1))] = pairs
    return data


def base_keys(base: pd.DataFrame) -> pd.Series:
    def one(rid: str) -> str:
        pk = page_key(rid)
        if not pk or len(rid) < 18:
            return ""
        return f"{pk}|{int(rid[14:16])}|{int(rid[16:18])}"
    return base[RID].map(one)


def run(base: pd.DataFrame, fetcher: Fetcher, out_dir: Path, log: Logger = default_log) -> Outputs:
    pages = unique_in_order(base[RID].map(page_key))
    log(f"[INFO] {LABEL}: {len(pages)} ページ（開催日×場所）")
    mapping: dict[str, int] = {}
    for i, pk in enumerate(pages, 1):
        html = fetcher.get(page_url(pk))
        data = parse(html) if html else {}
        for r, pairs in data.items():
            for hn, iv in pairs:
                mapping[f"{pk}|{r}|{hn}"] = iv
        log(f"[INFO] {LABEL} {i}/{len(pages)} {pk}: {len(data)} レース" + ("" if html else "（取得できず）"))
    return write_outputs(base, lookup(base_keys(base), mapping), Path(out_dir), NAME, log)
