"""SP指数・ST指数の「3着内基準値」（shisu/data/index_baseline_SP.csv・index_baseline_ST.csv）の生成・更新・参照と、
そこから作る条件（前走SP基準比・前走ST基準比・過去SP今走基準比・過去ST今走基準比）。

基準値 = その条件のレースで3着に入った馬の結果指数（SP指数・ST指数）の水準。セルは3階層:
  L3  芝・ダ × クラス × 馬場                      （親 = 全体平均）
  L2  芝・ダ × クラス × 馬場 × 距離帯              （親 = L3）
  L1  芝・ダ × クラス × 馬場 × 場所区分 × 距離帯    （親 = L2）
部品:
  芝・ダ      芝 / ダ（平地だけ。障害は入れない）
  クラス      年齢限定で分けた13区分: 2歳未勝利・3歳未勝利／2歳1勝・3歳1勝・古馬1勝／2勝／3勝／2歳OP・3歳OP・古馬OP／
              2歳重賞・3歳重賞・古馬重賞（3上・4上 は古馬）。新馬と G1 は集計に入れない。
              引くときは G1 → 同じ世代の重賞、新馬 → 基準値なし
  馬場        芝: 良 / 良以外（稍重・重・不良）。ダ: 良稍（良・稍重）/ 重不良（重・不良）
  場所区分    主場（東京・中山・京都・阪神・中京）/ ローカル（函館・札幌・福島・新潟・小倉）
  距離帯      芝: 〜1400 芝短距離 / 1401〜1800 芝マイル / 1801〜2400 芝中距離 / 2401〜 芝長距離
              ダ: 〜1300 ダ短距離 / 1301〜1600 ダマイル / 1601〜2000 ダ中距離 / 2001〜 ダ長距離
セルの値（build_rows）:
  1. セルの3着馬の指数 n 件を小さい順に並べ、上位 n // 3 件を除く（端数は残す側）
  2. 残した値の、年度の重みつき平均を round() で整数に = index_raw
     重み w = decay ** (データの最新年 − その走の年)。既定 decay = 0.65。decay = 1.0 なら等重み（従来の値と同じ）
  3. 親の確定値 base に縮約: index_mean = round((n * index_raw + K_SHRINK * base) / (n + K_SHRINK))（K_SHRINK = 30）
     全体平均（カットなしの重みつき平均）→ L3 → L2 → L1 の順に確定する
  丸めは Python の round()（ちょうど .5 は偶数へ。受け取った index_baseline_*.csv の 467 セルがこの丸めで全部再現できた）
引き方（Baseline.lookup）: L1 → L2 → L3 の順に、最初にあるセルの index_mean。どれも無ければ None。

更新: python -m shisu.baselines --update（基本CSV csv_files/ と結果指数 result_indices/ から作り直す）。
replace_csv_ver3.py も、結果指数があれば実行のたびに更新する（サンプルが少なければ更新しない）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import sys
import unicodedata
from pathlib import Path
from typing import Callable, Iterable, Mapping

import pandas as pd

from . import config

Logger = Callable[[str], None]

KINDS = ("SP", "ST")
FILES = {"SP": "index_baseline_SP.csv", "ST": "index_baseline_ST.csv"}
# replace が master を作ったときの基準値の写し（master_csv/）。出馬表側（build_db）はこれを先に読む
MASTER_FILES = {"SP": "__index_baseline_SP__.csv", "ST": "__index_baseline_ST__.csv"}
MASTER_META = "__index_baseline__.json"
COLUMNS = ["level", "key", "n", "index_mean", "reliability", "index_raw", "index_mean_全体", "n_kept", "n_cut",
           "index_std", "p25", "median", "p75", "parent"]
K_SHRINK = 30
DEFAULT_DECAY = 0.65
MIN_SAMPLES = 2000               # 自動更新に要る3着馬の件数（少ないデータで基準値を作り直さない）
ROOT_LABEL = "（全体平均）"
KEY_SEP = " × "
SURFACES = ("芝", "ダ")
CLASSES = ("2歳未勝利", "3歳未勝利", "2歳1勝", "3歳1勝", "古馬1勝", "2勝", "3勝", "2歳OP", "3歳OP", "古馬OP",
           "2歳重賞", "3歳重賞", "古馬重賞")
TRACKS = {"芝": ("良", "良以外"), "ダ": ("良稍", "重不良")}
MAIN_PLACES = ("東京", "中山", "京都", "阪神", "中京")
LOCAL_PLACES = ("函館", "札幌", "福島", "新潟", "小倉")
PGROUPS = ("主場", "ローカル")
BANDS = {"芝": ((1400, "芝短距離"), (1800, "芝マイル"), (2400, "芝中距離"), (None, "芝長距離")),
         "ダ": ((1300, "ダ短距離"), (1600, "ダマイル"), (2000, "ダ中距離"), (None, "ダ長距離"))}
LEVELS = ("L3", "L2", "L1")

# 条件の列（conditions.CONDITION_COLUMNS に入る）
PREV_COLUMNS = {"SP": "前走SP基準比", "ST": "前走ST基準比"}
NOW_COLUMNS = {"SP": "過去SP今走基準比", "ST": "過去ST今走基準比"}
RANK_COLUMNS = {"SP": "過去SP3走順", "ST": "過去ST3走順"}
CONDITION_NAMES = (PREV_COLUMNS["SP"], PREV_COLUMNS["ST"], NOW_COLUMNS["SP"], NOW_COLUMNS["ST"],
                   RANK_COLUMNS["SP"], RANK_COLUMNS["ST"])
# 前走の基準比の区切り: (突破の下限, 大幅未達の上限)。差 d = 前走の指数 − 前走の条件の基準値
#   SP: d ≥ +3 突破 / 0 ≤ d < +3 クリア / −6 < d < 0 未達 / d ≤ −6 大幅未達
#   ST: d ≥ +2 突破 / 0 ≤ d < +2 クリア / −4 < d < 0 未達 / d ≤ −4 大幅未達
PREV_RULES = {"SP": (3, -6), "ST": (2, -4)}
RANK_INDICATOR = {"SP": "過去SP_3走平均", "ST": "過去ST_3走平均"}      # 過去SP3走順・過去ST3走順 の元の指数
NOW_INDICATORS = {k: (f"過去{k}_3走平均", f"過去{k}_5走平均", f"過去{k}_3走最高", f"過去{k}_5走最高") for k in KINDS}
BABA_AUTO = "auto"


# ---------------------------------------------------------------------------
# セルの部品
# ---------------------------------------------------------------------------
def _n(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(v)))


_AGE_OPEN = re.compile(r"[34]歳?(?:以上|上)|古馬")
_AGE_YOUNG = re.compile(r"([23])歳(?!以上|上)")
_CLASS_EXACT = {"オープン": "OP", "OP": "OP", "OP(L)": "OP", "L": "OP", "(L)": "OP", "リステッド": "OP",
                "G2": "重賞", "G3": "重賞", "GII": "重賞", "GIII": "重賞", "重賞": "重賞", "G1": "G1", "GI": "G1"}
_CLASS_WINS = ((re.compile(r"500万|(?<!\d)1勝"), "1勝"), (re.compile(r"1000万|(?<!\d)2勝"), "2勝"),
               (re.compile(r"1600万|(?<!\d)3勝"), "3勝"))


def generation(age, cls="", race_name="") -> str:
    """世代: 2歳 / 3歳 / 古馬（3上・4上・3歳以上・4歳以上）。年齢限定（年限定）の列に値があればそれで、
    空・読めない値なら クラス名・レース名 の「2歳」「3歳」「3歳以上」から。分からなければ ""。"""
    a = _n(age)
    if a:
        if _AGE_OPEN.search(a) or "上" in a:
            return "古馬"
        m = _AGE_YOUNG.search(a)
        if m:
            return f"{m.group(1)}歳"
        if a in ("2", "3"):
            return f"{a}歳"
    text = _n(cls) + " " + _n(race_name)
    if _AGE_OPEN.search(text):
        return "古馬"
    m = _AGE_YOUNG.search(text)
    return f"{m.group(1)}歳" if m else ""


def class_token(cls) -> str:
    """クラス名 → 新馬 / 未勝利 / 1勝 / 2勝 / 3勝 / OP / 重賞 / G1。障害（J…）・分からない値は ""。"""
    t = _n(cls)
    if not t or t.upper().startswith("J") or "障害" in t:
        return ""
    if "新馬" in t:
        return "新馬"
    if "未勝利" in t:
        return "未勝利"
    tok = _CLASS_EXACT.get(t.upper()) or _CLASS_EXACT.get(t)
    if tok:
        return tok
    for rx, name in _CLASS_WINS:
        if rx.search(t):
            return name
    return ""


def race_class(cls, age="", race_name="") -> str:
    """基準値のクラス。13区分（CLASSES）のどれか、"新馬"、"{世代}G1"（2歳G1・3歳G1・古馬G1）。分からなければ ""。
    2勝・3勝 は世代で分けない。未勝利は 2歳・3歳 だけ。"""
    tok = class_token(cls)
    if tok in ("", "新馬", "2勝", "3勝"):
        return tok
    gen = generation(age, cls, race_name)
    if not gen or (tok == "未勝利" and gen == "古馬"):
        return ""
    return f"{gen}{tok}"


def lookup_class(race_cls: str) -> str | None:
    """基準値を引くときのクラス: G1 は同じ世代の重賞、新馬・不明は None（基準値なし）。"""
    if race_cls.endswith("G1"):
        race_cls = race_cls[:-2] + "重賞"
    return race_cls if race_cls in CLASSES else None


_BABA4 = {"良": "良", "稍": "稍", "稍重": "稍", "重": "重", "不": "不", "不良": "不", "稍・重": "稍・重"}


def baba4(v) -> str:
    """馬場状態 → 良 / 稍 / 重 / 不（想定の指定「稍・重」はそのまま）。空・分からない値は ""。"""
    return _BABA4.get(_n(v), "")


def norm_baba_choice(v) -> str:
    """想定の馬場状態の指定: auto / 良 / 稍 / 重 / 不 / 稍・重。知らない値は ValueError。"""
    s = _n(v)
    if s in ("", BABA_AUTO, "自動", "指定なし"):
        return BABA_AUTO
    b = baba4(s)
    if not b:
        raise ValueError(f"想定の馬場状態は auto / 良 / 稍 / 重 / 不 / 稍・重 のどれかです: {v!r}")
    return b


def track_of(surface: str, baba: str, strict: bool = False) -> str:
    """基準値の馬場区分。芝: 良 / 良以外。ダ: 良稍 / 重不良。
    馬場が分からないときは 良（芝）・良稍（ダ）として引く（strict=True なら ""。集計に入れるかの判定用）。
    想定の指定「稍・重」は、芝なら 良以外、ダは 良稍（稍重 と 重 を分けられないので、頻度の高い 稍重 の側）。"""
    if surface not in TRACKS:
        return ""
    b = baba4(baba)
    if strict and b not in ("良", "稍", "重", "不"):
        return ""
    if surface == "芝":
        return "良" if b in ("良", "") else "良以外"
    return "重不良" if b in ("重", "不") else "良稍"


def place_group(place) -> str:
    p = _n(place)
    if p in MAIN_PLACES:
        return "主場"
    return "ローカル" if p in LOCAL_PLACES else ""


def dist_band(surface: str, dist) -> str:
    if surface not in BANDS:
        return ""
    try:
        d = float(dist)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(d) or d <= 0:
        return ""
    for hi, label in BANDS[surface]:
        if hi is None or d <= hi:
            return label
    return ""


def cell_keys(surface: str, cls: str, track: str, pgroup: str = "", band: str = "") -> dict[str, str]:
    """階層 → セルのキー（作れる階層だけ）。"""
    out = {}
    if not (surface and cls and track):
        return out
    out["L3"] = KEY_SEP.join((surface, cls, track))
    if band:
        out["L2"] = KEY_SEP.join((surface, cls, track, band))
        if pgroup:
            out["L1"] = KEY_SEP.join((surface, cls, track, pgroup, band))
    return out


# ---------------------------------------------------------------------------
# 参照
# ---------------------------------------------------------------------------
class Baseline:
    """基準値の表（1種類ぶん）。cells: セルのキー → index_mean。"""

    def __init__(self, kind: str, cells: Mapping[str, int], source: str = ""):
        self.kind = kind
        self.cells = dict(cells)
        self.source = source

    def __len__(self) -> int:
        return len(self.cells)

    def lookup(self, surface: str, race_cls: str, track: str, pgroup: str = "", band: str = "") -> int | None:
        """L1 → L2 → L3 の順に、最初にあるセルの値。race_cls は race_class() の値（G1 は重賞に読み替える）。"""
        cls = lookup_class(race_cls or "")
        if cls is None:
            return None
        keys = cell_keys(surface, cls, track, pgroup, band)
        for level in ("L1", "L2", "L3"):
            v = self.cells.get(keys.get(level, ""))
            if v is not None:
                return v
        return None


def parse_csv(text: str, kind: str = "", source: str = "") -> Baseline:
    """index_baseline_*.csv の中身 → Baseline。level・key・index_mean の列が要る。形が違えば ValueError。"""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows or any(c not in rows[0] for c in ("level", "key", "index_mean")):
        raise ValueError("基準値の表に level・key・index_mean の列がありません")
    cells: dict[str, int] = {}
    for r in rows:
        key = KEY_SEP.join(p.strip() for p in str(r["key"]).split("×"))
        try:
            cells[key] = int(round(float(r["index_mean"])))
        except (TypeError, ValueError):
            continue
    if not cells:
        raise ValueError("基準値の表に読める行がありません")
    return Baseline(kind, cells, source)


def read_file(path: Path | str, kind: str = "") -> Baseline:
    p = Path(path)
    data = p.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp932")
    return parse_csv(text, kind, str(p))


def data_dir(path: Path | str | None = None) -> Path:
    return Path(path) if path is not None else config.baseline_dir()


def load(kind: str, directory: Path | str | None = None, log: Logger | None = None) -> Baseline | None:
    """基準値マスタ（shisu/data/index_baseline_{kind}.csv）。無い・読めないなら None。"""
    p = data_dir(directory) / FILES[kind]
    if not p.exists():
        return None
    try:
        return read_file(p, kind)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        if log:
            log(f"[WARN] 基準値: {p.name} を読めません（{type(e).__name__}: {e}）")
        return None


def load_tables(directory: Path | str | None = None, log: Logger | None = None) -> dict[str, Baseline]:
    return {k: b for k in KINDS if (b := load(k, directory, log)) is not None}


def snapshot(master_dir: Path | str, directory: Path | str | None = None, meta: Mapping | None = None) -> dict[str, Baseline]:
    """今の基準値マスタを master_csv/ に写す（replace が呼ぶ）。master を作ったときの基準値を、出馬表側（build_db）も
    使うため（あとから基準値だけ更新しても、master と出馬表で違う基準値を見ない）。戻り値は写した表。"""
    d = Path(master_dir)
    d.mkdir(parents=True, exist_ok=True)
    src = data_dir(directory)
    out: dict[str, Baseline] = {}
    for kind in KINDS:
        dst = d / MASTER_FILES[kind]
        b = load(kind, src)
        if b is None:
            dst.unlink(missing_ok=True)
            continue
        tmp = d / (MASTER_FILES[kind] + ".part")
        tmp.write_bytes((src / FILES[kind]).read_bytes())
        os.replace(tmp, dst)
        out[kind] = b
    save_meta(d, meta)
    return out


def save_meta(master_dir: Path | str, meta: Mapping | None = None) -> None:
    """replace が基準値をどう使ったかの記録（master_csv/__index_baseline__.json）。一時ファイルから置き換える。"""
    d = Path(master_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (MASTER_META + ".part")
    tmp.write_text(json.dumps({"version": 1, **dict(meta or {})}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / MASTER_META)


def load_meta(master_dir: Path | str | None = None) -> dict:
    p = (Path(master_dir) if master_dir is not None else config.master_dir()) / MASTER_META
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def load_for_master(master_dir: Path | str | None = None, directory: Path | str | None = None,
                    log: Logger | None = None) -> dict[str, Baseline]:
    """master と同じ基準値。

    replace の記録（master_csv/__index_baseline__.json）があれば、master_csv/ の写しだけを使う（写しの無い・読めない種類は
    付けない。master を作ったときに使えなかった基準値を、出馬表側だけで使わない）。記録が無ければ（前の版の master・
    master_csv の無い場所）基準値マスタ（shisu/data/）。
    """
    d = Path(master_dir) if master_dir is not None else config.master_dir()
    out: dict[str, Baseline] = {}
    recorded = bool(load_meta(d))
    for kind in KINDS:
        if recorded:
            p = d / MASTER_FILES[kind]
            if not p.exists():
                continue
            try:
                out[kind] = read_file(p, kind)
            except (OSError, ValueError, UnicodeDecodeError) as e:
                if log:
                    log(f"[WARN] 基準値: {p.name} を読めません（{type(e).__name__}: {e}）。{kind} の基準比の条件は付けません")
            continue
        b = load(kind, directory, log)
        if b is not None:
            out[kind] = b
    return out


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
def _reliability(n: int) -> str:
    return "高" if n >= 100 else ("中" if n >= 30 else "低")


def _wmean(items: list[tuple[float, float]]) -> float:
    return math.fsum(v * w for v, w in items) / math.fsum(w for _v, w in items)


def _cell_stat(items: list[tuple[float, float]]) -> dict:
    """items = (値, 重み) のリスト → 上位1/3カットの重みつき平均など。
    同じ値が切れ目にまたがるときは、重みの小さい（古い）ほうから除く（並びに依らない。decay = 1.0 なら重みは同じ）。"""
    items = sorted(items, key=lambda t: (t[0], -t[1]))
    n = len(items)
    cut = n // 3
    kept = items[: n - cut]
    q = lambda p: float(items[min(n - 1, int(n * p))][0])
    return {"n": n, "index_raw": round(_wmean(kept)), "index_mean_全体": round(_wmean(items)), "n_kept": len(kept),
            "n_cut": cut, "index_std": round(statistics.pstdev([v for v, _w in kept]), 2),
            "p25": q(0.25), "median": q(0.5), "p75": q(0.75)}


def _shrink(n: int, m: int, base: int) -> int:
    return round((n * m + K_SHRINK * base) / (n + K_SHRINK))


def _order(level: str, key: str) -> tuple:
    p = key.split(KEY_SEP)
    surface, cls, track = p[0], p[1], p[2]
    pg = p[3] if level == "L1" else ""
    band = p[-1] if level != "L3" else ""
    labels = [b for _hi, b in BANDS.get(surface, ())]
    return (SURFACES.index(surface), CLASSES.index(cls), TRACKS[surface].index(track),
            PGROUPS.index(pg) if pg else -1, labels.index(band) if band else -1)


def build_rows(samples: Iterable[tuple], decay: float = DEFAULT_DECAY) -> tuple[list[dict], dict]:
    """samples = (芝・ダ, クラス, 馬場区分, 場所区分, 距離帯, 年, 値) → (表の行（COLUMNS）, 情報 {root, n, year_max, decay})。
    クラスは CLASSES のどれか（新馬・G1 は渡さない）。"""
    if not (0 < decay <= 1):
        raise ValueError(f"decay は 0 より大きく 1 以下: {decay!r}")
    samples = list(samples)
    if not samples:
        raise ValueError("基準値を作るサンプルがありません")
    year_max = max(int(s[5]) for s in samples)
    groups: dict[str, dict[str, list[tuple[float, float]]]] = {lv: {} for lv in LEVELS}
    everything: list[tuple[float, float]] = []
    for surface, cls, track, pgroup, band, year, value in samples:
        item = (float(value), float(decay) ** (year_max - int(year)))
        everything.append(item)
        for level, key in cell_keys(surface, cls, track, pgroup, band).items():
            groups[level].setdefault(key, []).append(item)
    root = round(_wmean(everything))
    final: dict[str, int] = {}
    rows: list[dict] = []
    for level in LEVELS:                          # 粗い階層から確定する
        for key in sorted(groups[level], key=lambda k: _order(level, k)):
            parent = ROOT_LABEL if level == "L3" else KEY_SEP.join(
                key.split(KEY_SEP)[:3] if level == "L2" else key.split(KEY_SEP)[:3] + key.split(KEY_SEP)[4:])
            base = root if level == "L3" else final[parent]
            st = _cell_stat(groups[level][key])
            mean = _shrink(st["n"], st["index_raw"], base)
            final[key] = mean
            rows.append({"level": level, "key": key, "n": st["n"], "index_mean": mean,
                         "reliability": _reliability(st["n"]), "index_raw": st["index_raw"],
                         "index_mean_全体": st["index_mean_全体"], "n_kept": st["n_kept"], "n_cut": st["n_cut"],
                         "index_std": st["index_std"], "p25": st["p25"], "median": st["median"], "p75": st["p75"],
                         "parent": parent})
    return rows, {"root": root, "n": len(samples), "year_max": year_max, "decay": float(decay)}


def to_csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)          # 行末は \r\n（受け取ったファイルと同じ）
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def samples_from_history(frame: pd.DataFrame, kind: str) -> list[tuple]:
    """過去走の履歴（past_perf_indices の HISTORY_COLUMNS）→ 基準値のサンプル。
    3着（着順 = 3）・その指数の値が 0 でない・クラスが13区分（新馬・G1 を除く）・芝/ダ・馬場と場所区分と距離が分かる走。"""
    need = ("着順", kind, "芝・ダ", "距離", "場所", "日付", "クラス", "馬場4")
    if any(c not in frame.columns for c in need):
        return []
    sub = frame[(frame["着順"].astype(str) == "3") & (frame[kind].astype(str) != "")]
    out = []
    for surface, dist, place, date, cls, baba, value in zip(sub["芝・ダ"], sub["距離"], sub["場所"], sub["日付"],
                                                             sub["クラス"], sub["馬場4"], sub[kind]):
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        track, pg, band = track_of(str(surface), str(baba), strict=True), place_group(place), dist_band(str(surface), dist)
        year = str(date)[:4]
        if v == 0 or not math.isfinite(v) or cls not in CLASSES or not (track and pg and band) or not year.isdigit():
            continue
        out.append((str(surface), cls, track, pg, band, int(year), v))
    return out


def update_from_history(frame: pd.DataFrame, directory: Path | str | None = None, decay: float = DEFAULT_DECAY,
                        min_samples: int = MIN_SAMPLES, kinds: Iterable[str] = KINDS,
                        log: Logger | None = None) -> dict[str, dict]:
    """過去走の履歴から基準値マスタを作り直して書く。戻り値: 種類 → {status: updated / same / skipped, n, …}。
    3着馬のサンプルが min_samples 件に満たない種類は書かない（少ないデータで今の基準値を置き換えない）。"""
    log = log or (lambda m: None)
    d = data_dir(directory)
    out: dict[str, dict] = {}
    for kind in [k for k in KINDS if k in set(kinds)]:
        samples = samples_from_history(frame, kind)
        if len(samples) < max(1, int(min_samples)):
            out[kind] = {"status": "skipped", "n": len(samples)}
            log(f"[INFO] 基準値（{kind}）: 3着馬のサンプルが {len(samples)} 件で、{int(min_samples)} 件に満たないので更新しません"
                f"（今の {FILES[kind]} のまま）")
            continue
        rows, info = build_rows(samples, decay)
        text = to_csv_text(rows)
        path = d / FILES[kind]
        same = path.exists() and path.read_bytes() == text.encode("utf-8")
        if not same:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / (FILES[kind] + ".part")
            tmp.write_bytes(text.encode("utf-8"))
            os.replace(tmp, path)
        out[kind] = {"status": "same" if same else "updated", "cells": len(rows), **info}
        log(f"[INFO] 基準値（{kind}）: 3着馬 {info['n']} 件（最新年 {info['year_max']}・decay {info['decay']:g}・全体平均 {info['root']}）"
            f"→ {len(rows)} セル" + ("（変更なし）" if same else f" を {path} に書きました"))
    return out


# ---------------------------------------------------------------------------
# 条件（前走の基準比・今走の基準比）
# ---------------------------------------------------------------------------
def prev_label(kind: str, diff) -> str:
    """前走の指数 − 前走の条件の基準値 → 前走{kind}基準突破 / クリア / 未達 / 大幅未達。"""
    hi, lo = PREV_RULES[kind]
    head = f"前走{kind}基準"
    if diff >= hi:
        return head + "突破"
    if diff >= 0:
        return head + "クリア"
    return head + ("未達" if diff > lo else "大幅未達")


def now_label(kind: str, avg3, avg5, max3, max5, base) -> str:
    """今走の 過去{kind} の4つの値（None = 値なし）と今走の条件の基準値 → {kind}平均基準超 / 3走最高基準超 / 5走最高基準超 /
    今走基準未達。基準値が無い（新馬・条件が分からない）・値が1つも無ければ ""。"""
    if base is None or all(v is None for v in (avg3, avg5, max3, max5)):
        return ""
    if (avg3 is not None and avg3 >= base) or (avg5 is not None and avg5 >= base):
        return f"{kind}平均基準超"
    if max3 is not None and max3 >= base:
        return f"{kind}3走最高基準超"
    if max5 is not None and max5 >= base:
        return f"{kind}5走最高基準超"
    return f"{kind}今走基準未達"


def _value(v) -> float | None:
    """過去SP・ST の値。空・数でない・0（対象の走なしの印）は None。"""
    s = _n(v)
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) and f != 0 else None


def _rank(v) -> str:
    s = _n(v)
    try:
        f = float(s) if s else None
    except ValueError:
        return ""
    return str(int(f)) if f is not None and math.isfinite(f) and f >= 1 and f == int(f) else ""


def today_parts(raw: pd.DataFrame) -> pd.DataFrame:
    """基本CSV・出馬表の列 → 今走の基準値を引く部品と馬の見分け（index は raw のまま）:
    芝・ダ・距離（数）・クラス（race_class）・馬場（baba4。列が無ければ ""）・場所・馬ID・馬名。"""
    from . import past_perf_indices as PP
    from .normalize import distance_series, split_surface_distance
    out = pd.DataFrame(index=raw.index)
    surface, dist_raw = split_surface_distance(PP._col(raw, "芝・ダ", "芝ダ"), PP._col(raw, "距離"))
    out["芝・ダ"] = surface
    out["距離"] = pd.to_numeric(distance_series(dist_raw), errors="coerce")
    out["クラス"] = [race_class(c, a, n) for c, a, n in zip(PP._col(raw, "クラス名", "クラス"),
                                                              PP._col(raw, "年齢限定", "年限定"), PP._col(raw, "レース名"))]
    out["馬場"] = PP._col(raw, "馬場状態", "馬場").map(baba4)
    out["場所"] = PP._col(raw, "場所").map(_n)
    out["馬ID"] = PP._col(raw, "血統登録番号").map(PP.norm_horse_id)
    out["馬名"] = PP._col(raw, "馬名", "馬名S", "馬名1").map(PP.norm_horse_name)
    return out


def conditions(parts: pd.DataFrame, prev_rid16: pd.Series | None, history, tables: Mapping[str, Baseline] | None,
               values: Mapping[str, pd.Series] | None = None, ranks: Mapping[str, pd.Series] | None = None,
               baba_turf: str = BABA_AUTO, baba_dirt: str = BABA_AUTO) -> pd.DataFrame:
    """基準比の4列と 過去SP3走順・過去ST3走順（CONDITION_NAMES。index は parts と同じ。分からなければ ""）。
    過去側（replace）と出馬表側（build_db）で共通。

    parts: today_parts() の結果。prev_rid16: 前走のレースID(新)先頭16桁（conditions の 前走RID16）。
    history: 馬ごとの過去走の履歴（前走の指数と前走の条件を引く）。tables: 種類 → Baseline。
    values: 指数名（過去SP_3走平均 など8つ）→ 値。ranks: 種類 → 過去{kind}_3走平均 のレース内順位。
    baba_turf・baba_dirt: 今走の馬場の想定（auto なら parts の 馬場。出馬表側で使う）。"""
    from . import past_perf_indices as PP
    n = len(parts)
    out = {c: [""] * n for c in CONDITION_NAMES}
    tables = tables or {}
    values = values or {}
    ranks = ranks or {}
    choice = {"芝": norm_baba_choice(baba_turf), "ダ": norm_baba_choice(baba_dirt)}
    surfaces = parts["芝・ダ"].tolist()
    dists = parts["距離"].tolist()
    classes = parts["クラス"].tolist()
    babas = parts["馬場"].tolist()
    places = parts["場所"].tolist()
    ids, names = parts["馬ID"].tolist(), parts["馬名"].tolist()
    prev = prev_rid16.reindex(parts.index).fillna("").astype(str).tolist() if prev_rid16 is not None else [""] * n
    now_base: dict[str, list] = {}
    for kind, table in tables.items():
        col = []
        for i in range(n):
            s = surfaces[i]
            b = choice[s] if s in choice and choice[s] != BABA_AUTO else babas[i]
            col.append(table.lookup(s, classes[i], track_of(s, b), place_group(places[i]), dist_band(s, dists[i])))
        now_base[kind] = col
    for kind in KINDS:
        table = tables.get(kind)
        # 過去{kind}3走順: 過去{kind}_3走平均 のレース内順位（そのレースでその指数が使えないとき・値の無い馬は ""）
        if kind in ranks:
            out[RANK_COLUMNS[kind]] = [_rank(v) for v in ranks[kind].reindex(parts.index).tolist()]
        if table is None:
            continue
        # 過去{kind}今走基準比
        cols = [values.get(name) for name in NOW_INDICATORS[kind]]
        if any(c is not None for c in cols):
            vals = [([_value(v) for v in c.reindex(parts.index).tolist()] if c is not None else [None] * n) for c in cols]
            base = now_base[kind]
            out[NOW_COLUMNS[kind]] = [now_label(kind, vals[0][i], vals[1][i], vals[2][i], vals[3][i], base[i])
                                      for i in range(n)]
        # 前走{kind}基準比: この馬の、前走RID16 のレースの走を履歴から引く
        if history is None:
            continue
        col = out[PREV_COLUMNS[kind]]
        f_val = PP._F_SP if kind == "SP" else PP._F_ST
        for i in range(n):
            if not prev[i]:
                continue
            run = history.run_at(ids[i], names[i], prev[i])
            if run is None or run[f_val] is None or run[f_val] == 0:
                continue
            s = run[2]
            base = table.lookup(s, run[PP._F_CLASS], track_of(s, run[PP._F_BABA4]), place_group(run[PP._F_PLACE]),
                                dist_band(s, run[3]))
            if base is not None:
                col[i] = prev_label(kind, float(run[f_val]) - base)
    return pd.DataFrame(out, index=parts.index, dtype=object)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def describe(directory: Path | str | None = None) -> list[str]:
    lines = []
    for kind in KINDS:
        p = data_dir(directory) / FILES[kind]
        b = load(kind, directory)
        lines.append(f"{kind}: {p} — " + ("ありません" if b is None else f"{len(b)} セル"))
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SP指数・ST指数の3着内基準値（shisu/data/index_baseline_SP.csv・_ST.csv）の更新")
    ap.add_argument("--update", action="store_true", help="基本CSV（csv_files）と結果指数（result_indices）から作り直す")
    ap.add_argument("--decay", type=float, default=DEFAULT_DECAY,
                    help=f"年度の減衰（最新年から1年古いごとに掛ける重み。既定 {DEFAULT_DECAY}。1.0 で等重み）")
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES,
                    help=f"3着馬のサンプルがこの件数に満たない種類は更新しない（既定 {MIN_SAMPLES}）")
    ap.add_argument("--kinds", default=",".join(KINDS), help="更新する種類（SP,ST）")
    ap.add_argument("--csv-dir", default=None, help="基本CSVのフォルダ（既定 csv_files）")
    ap.add_argument("--result-dir", default=None, help="結果指数のフォルダ（既定 result_indices）")
    ap.add_argument("--master-dir", default=None, help="過去走の履歴の置き場所（既定 master_csv）")
    ap.add_argument("--out-dir", default=None, help="基準値マスタの置き場所（既定 shisu/data。SHISU_BASELINE_DIR）")
    args = ap.parse_args(argv)
    if not args.update:
        for line in describe(args.out_dir):
            print(line)
        print("更新するには: python -m shisu.baselines --update")
        return 0
    kinds = [k.strip().upper() for k in args.kinds.split(",") if k.strip()]
    if not kinds or any(k not in KINDS for k in kinds):
        print(f"[ERROR] --kinds は {','.join(KINDS)} から: {args.kinds!r}", file=sys.stderr)
        return 2
    if not (0 < args.decay <= 1):
        print(f"[ERROR] --decay は 0 より大きく 1 以下: {args.decay!r}", file=sys.stderr)
        return 2
    from . import past_perf_indices as PP
    try:
        frame = PP.history_frame(args.csv_dir, args.result_dir, args.master_dir, log=print)
    except Exception as e:
        print(f"[ERROR] 過去走の履歴を作れません: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if frame is None:
        print("[ERROR] 基本CSV（csv_files の {地域}_タイムM.csv / {地域}_対戦M.csv）がありません", file=sys.stderr)
        return 1
    res = update_from_history(frame, args.out_dir, args.decay, args.min_samples, kinds, log=print)
    if all(r["status"] == "skipped" for r in res.values()):
        print("[WARN] どの基準値も更新していません（結果指数 result_indices と基本CSVの期間を確認してください）", file=sys.stderr)
        return 1
    print("[INFO] 成績表・出馬表に効かせるには: replace_csv_ver3.py → aggregate_stats_ver2.py → build_db.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
