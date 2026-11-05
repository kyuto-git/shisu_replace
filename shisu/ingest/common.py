"""指数取得（スクレイピング・ラップ計算・安井Excel）の共通部品。

ベースCSV（TARGET の「レース一覧★画面イメージ」/ 出馬表の CSV）:
  レースID(新)（18桁: 1-8=YYYYMMDD, 9-10=場所コード, 11-12=回, 13-14=日, 15-16=R, 17-18=馬番）, 日付S, 枠番, 馬番, ワーク1
出力（各取得元で共通）:
  (a) マージ完全版   {出力先}/{元ファイル名}_merged.csv   … ベースCSVの全列。ワーク1 に取得値（取れなければ空欄）
  (b) TARGET 取込用  {出力先}/{YYYYMMDD}_{名前}.csv       … 日付S ごとに レースID(新), ワーク1 の2列（cp932）

値はベースCSVの1行に対して辞書（キー → 値）で引く（DataFrame の merge はしない）。
取得側に同じキーが2回出ても、ベースの行が増えることはない（1頭1行を保つ）。
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

import pandas as pd

from ..csvio import norm_series, norm_text, read_csv_any, write_csv

RID = "レースID(新)"
DATE = "日付S"
WORK = "ワーク1"
SOURCE_FILE = "_source_file"

PLACE_CODES = {"札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
               "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10"}
CODE_TO_PLACE = {v: k for k, v in PLACE_CODES.items()}

USER_AGENT = "Mozilla/5.0 (compatible; shisu-ingest/1.0)"

Logger = Callable[[str], None]


class Cancelled(Exception):
    """画面の「中止」で止めた。"""


def default_log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# レースID
# ---------------------------------------------------------------------------
def clean_rid(v) -> str:
    """レースID(新) を数字だけの文字列に（'2025…01.0' や全角も受ける）。"""
    s = norm_text(v)
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return re.sub(r"\D", "", s)


def rid12(rid: str) -> str:
    """11〜14桁目（回・日）と17〜18桁目（馬番）を除いた12桁 = YYYYMMDD + 場所 + R。"""
    return rid[:10] + rid[14:16] if len(rid) >= 16 else ""


def key14(rid: str) -> str:
    """11〜14桁目を除いた14桁 = YYYYMMDD + 場所 + R + 馬番。"""
    return rid[:10] + rid[14:18] if len(rid) >= 18 else ""


def bid16(rid: str) -> str:
    return rid[:16] if len(rid) >= 16 else ""


def int_text(v) -> str:
    """'01' / '1' / '１' / '1.0' → '1'。数でなければ ''。"""
    s = norm_text(v)
    try:
        f = float(s)
    except ValueError:
        return ""
    return str(int(f)) if f == f and f == int(f) else ""


def ymd_text(v) -> str:
    """日付S（20251101 / 2025/11/01 / 2025-11-1 / 20251101.0）→ 'YYYYMMDD'。読めなければ ''。"""
    s = norm_text(v)
    if re.fullmatch(r"\d{8}(\.0+)?", s):
        return s[:8]
    m = re.fullmatch(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return f"{y:04d}{mo:02d}{d:02d}"
    return ""


# ---------------------------------------------------------------------------
# ベースCSV
# ---------------------------------------------------------------------------
def read_base(paths: Iterable[Path | str], log: Logger = default_log) -> pd.DataFrame:
    """ベースCSV（複数可）を縦に結合する。列は文字列のまま。各行に _source_file（元ファイル名）を付ける。

    レースID(新) は数字だけにそろえる。日付S が無い・空欄の行は レースID(新) の先頭8桁で補う。
    """
    frames = []
    for p in paths:
        p = Path(p)
        df = read_csv_any(p)
        if RID not in df.columns:
            log(f"[WARN] {p.name}: 列 {RID} がありません（スキップ）")
            continue
        df = df.copy()
        df[RID] = df[RID].map(clean_rid)
        bad = (df[RID].str.len() != 18).sum()
        if bad:
            log(f"[WARN] {p.name}: {RID} が18桁でない行が {bad} 行あります（照合されません）")
        if DATE not in df.columns:
            df[DATE] = ""
        date = df[DATE].map(ymd_text)
        df[DATE] = date.where(date != "", df[RID].str.slice(0, 8))
        if WORK not in df.columns:
            df[WORK] = ""
        df[SOURCE_FILE] = p.name
        frames.append(df)
        log(f"[INFO] ベースCSV {p.name}: {len(df)} 行")
    if not frames:
        return pd.DataFrame(columns=[RID, DATE, WORK, SOURCE_FILE])
    return pd.concat(frames, ignore_index=True)


def lookup(keys: pd.Series, mapping: Mapping[str, object]) -> pd.Series:
    """キーの列 → 値の列（文字列。見つからなければ ''）。行数・並びはそのまま。"""
    return keys.map(lambda k: "" if k not in mapping else format_value(mapping[k])).astype(object)


def format_value(v) -> str:
    """取得値の表記をそろえる（'74.9' / '-17' / '48'。整数になる小数は整数で書く）。"""
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return ""
        return str(int(v)) if v == int(v) else f"{v:.10g}"
    return norm_text(v)


@dataclass
class Outputs:
    files: list[Path] = field(default_factory=list)
    matched: int = 0
    total: int = 0

    def extend(self, other: "Outputs") -> None:
        self.files += other.files
        self.matched += other.matched
        self.total += other.total


def write_outputs(base: pd.DataFrame, values: pd.Series, out_dir: Path, name: str,
                  log: Logger = default_log, merged: bool = True) -> Outputs:
    """(a) マージ完全版（元ファイルごと）と (b) TARGET 取込用（日付S ごとの2列）を書く。

    values はベースと同じ index の文字列（取れなかった行は ''）。
    (b) は同じ レースID(新) が複数あれば、値のある行を優先して1行にする（複数のベースCSVが重なっても重複しない）。
    """
    out_dir = Path(out_dir)
    res = Outputs(total=len(base), matched=int((values != "").sum()))
    full = base.copy()
    full[WORK] = values.reindex(full.index).fillna("")
    if merged:
        for src, g in full.groupby(SOURCE_FILE, sort=True):
            path = out_dir / f"{Path(str(src)).stem}_merged.csv"
            write_csv(g.drop(columns=[SOURCE_FILE]), path)
            res.files.append(path)
    small = full[[DATE, RID, WORK]].copy()
    small = small[small[RID] != ""]
    small["_has"] = small[WORK] != ""
    small = small.sort_values("_has", ascending=False, kind="stable").drop_duplicates(subset=[RID], keep="first")
    for day, g in small.groupby(DATE, sort=True):
        if not day:
            continue
        g = g.sort_index()
        path = out_dir / f"{day}_{name}.csv"
        write_csv(g[[RID, WORK]], path)
        res.files.append(path)
    log(f"[INFO] {name}: 取得値あり {res.matched}/{res.total} 行 → {out_dir}")
    return res


# ---------------------------------------------------------------------------
# HTTP（アクセス間隔・リトライ・中止）
# ---------------------------------------------------------------------------
class Fetcher:
    """前回のアクセス開始から interval 秒あけて GET する。失敗は retries 回まで（待ちを伸ばして）やり直す。

    404 はやり直さない（ページが無い）。stop（threading.Event）が立ったら Cancelled を投げる。
    テストでは session（.get(url, headers=, timeout=) を持つもの）と sleep・clock を差し替える。
    """

    def __init__(self, interval: float = 2.0, timeout: float = 20.0, retries: int = 2, session=None,
                 log: Logger = default_log, stop: threading.Event | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.interval = max(0.0, float(interval))
        self.timeout = timeout
        self.retries = retries
        self.log = log
        self.stop = stop
        self.sleep = sleep
        self.clock = clock
        self._last: float | None = None
        self.requests = 0

    def _check_stop(self) -> None:
        if self.stop is not None and self.stop.is_set():
            raise Cancelled()

    def _wait(self, extra: float = 0.0) -> None:
        if self._last is None:
            return
        remain = self.interval + extra - (self.clock() - self._last)
        while remain > 0:
            self._check_stop()
            step = min(remain, 0.5)
            self.sleep(step)
            remain -= step

    def get(self, url: str, encoding: str | None = None) -> str | None:
        for attempt in range(self.retries + 1):
            self._check_stop()
            self._wait(extra=self.interval * attempt)
            self._last = self.clock()
            self.requests += 1
            try:
                r = self.session.get(url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout)
            except Exception as e:  # requests.RequestException など
                self.log(f"[WARN] GET 失敗 ({attempt + 1}/{self.retries + 1}): {url}: {type(e).__name__}: {e}")
                continue
            status = getattr(r, "status_code", 0)
            if status == 200:
                return _decode(r, encoding)
            self.log(f"[WARN] HTTP {status}: {url}")
            if status == 404:
                return None
        return None


def _decode(r, encoding: str | None) -> str:
    content = getattr(r, "content", None)
    if encoding and content is not None:
        return content.decode(encoding, errors="replace")
    text = getattr(r, "text", None)
    if text is not None and content is None:
        return text
    enc = getattr(r, "encoding", None) or getattr(r, "apparent_encoding", None) or "utf-8"
    if enc.lower() in ("iso-8859-1",):        # requests の既定（charset 無し）はページの meta に任せる
        enc = getattr(r, "apparent_encoding", None) or "utf-8"
    return content.decode(enc, errors="replace") if content is not None else (text or "")


def to_number_text(s) -> str:
    """セルの文字から最初の数（-12 / 74.9）を取り出す。無ければ ''。"""
    m = re.search(r"-?\d+(?:\.\d+)?", norm_text(s).replace(",", ""))
    return m.group(0) if m else ""


def unique_in_order(values: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def norm_cell(s) -> str:
    return re.sub(r"\s+", "", norm_text(s))


def series_of(df: pd.DataFrame, col: str) -> pd.Series:
    return norm_series(df[col]) if col in df.columns else pd.Series("", index=df.index, dtype=object)
