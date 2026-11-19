"""TARGET の外部レース印（5種）→ 前走条件の元。

置き場所: race_marks/（環境変数 RACE_MARKS_DIR。下のサブフォルダ・ZIP の中も探す）。名前（大文字小文字は問わない）で種類を決める:
  member*.csv         メンバーレベル（レースレベル）  A〜E
  time*.csv           タイムレベル                    S・A〜E
  pace*.csv           ペース判定（pace_decision*.csv）HHH・HH・H・M・S・SS・SSS
  cushion*.csv        クッション値（芝だけ。数）
  moistureG*.csv      ゴール前含水率（moisture*.csv も。同じレースが2行続き、1行目 = 芝・2行目 = ダート。
                      moistureC*.csv（4コーナーの含水率）は使わない）
中身: 見出しなしの3列  "開催名", "RX" + 旧8桁のレースID, "2桁の16進カラーコード + 値"
      例: "2026年3回福島2日目12R",RX03263212,"0E Ｂ "      文字コードは BOM → UTF-8 → cp932

レースの照合: レースID(新) の先頭16桁（YYYYMMDD + 場所2 + 回2 + 日2 + R2）→ RX + 場所2 + 年の下2桁 + 回1桁 + 日1文字
（1〜9、10日 = A、11日 = B …）+ R2。例 2026110803030212 → RX03263212（rx_of）。
値: 先頭2文字が16進（カラーコード）なら除き、NFKC・前後の空白を除く。---- ・－ ・- は値なし。

replace_csv_ver3.py が race_marks/ を読んで master_csv/__race_marks__.csv.gz に書く（前走の内外の表 __inner_outer__ と同じ使い方）。
出馬表側（build_db）は race_marks/ にファイルがあればそれを、無ければ master_csv/__race_marks__.csv.gz を読む（load）。
前走条件への変換（区分）は shisu/conditions.py。
"""
from __future__ import annotations

import csv
import gzip
import json
import io
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

import pandas as pd

from . import config

Logger = Callable[[str], None]

KINDS = ("member", "time", "pace", "cushion", "moisture")
KIND_LABELS = {"member": "メンバーレベル", "time": "タイムレベル", "pace": "ペース判定", "cushion": "クッション値",
               "moisture": "ゴール前含水率"}
FIELDS = ("member", "time", "pace", "cushion", "moist_turf", "moist_dirt")      # 1レースぶんの値（文字列。無ければ ""）
KEY = "RX"
COLUMNS = [KEY] + list(FIELDS)
FILE = "__race_marks__.csv.gz"
META = "__race_marks__.json"
_RX_RE = re.compile(r"^RX(\d{2})(\d{2})(\d)([0-9A-Z])(\d{2})$")
_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{2}")
_DASH_RE = re.compile(r"^[-‐-―ー−]+$")


def day_char(day: int) -> str:
    """開催日 → 1文字（1〜9、10 = A、11 = B、12 = C …）。範囲の外は ""。"""
    if 1 <= day <= 9:
        return str(day)
    if 10 <= day <= 35:
        return chr(ord("A") + day - 10)
    return ""


def rx_of(rid16) -> str:
    """レースID(新) の先頭16桁（18桁でもよい）→ "RX" + 旧8桁。読めなければ ""。"""
    s = str(rid16 or "").strip()
    if not re.fullmatch(r"\d{16}(\d{2})?", s):
        return ""
    place, kai, day, race = s[8:10], int(s[10:12]), int(s[12:14]), s[14:16]
    d = day_char(day)
    if not d or not 1 <= kai <= 9:
        return ""
    return f"RX{place}{s[2:4]}{kai}{d}{race}"


def clean_value(raw) -> str:
    """第3列の値: 先頭2文字が16進（カラーコード）なら除き、NFKC・前後の空白を除く。---- ・－ ・- は ""。"""
    s = "" if raw is None else str(raw)
    if _COLOR_RE.match(s):
        s = s[2:]
    s = unicodedata.normalize("NFKC", s).strip()
    return "" if _DASH_RE.match(s) else s


def kind_of(name: str) -> str | None:
    """ファイルの名前 → 種類（member / time / pace / cushion / moisture）。CSV でない・どれでもなければ None。"""
    n = name.casefold()
    if not n.endswith(".csv"):
        return None
    if n.startswith("moisture"):
        rest = n[len("moisture"):]
        # moistureG = ゴール前。moistureC など（4コーナー）は使わない。moisture_2026.csv・moisture2026.csv はゴール前として読む
        return "moisture" if rest.startswith("g") or not rest[:1].isalpha() else None
    for kind in ("member", "time", "pace", "cushion"):
        if n.startswith(kind):
            return kind
    return None


@dataclass(frozen=True)
class MarkFile:
    kind: str
    path: Path               # CSV か ZIP
    member: str = ""         # ZIP の中のファイル名（CSV そのものなら ""）

    @property
    def label(self) -> str:
        return f"{self.path.name}:{self.member}" if self.member else self.path.name


def _walk(root: Path) -> Iterator[Path]:
    """root の下のファイルを、相対パス（/ 区切り）の名前順で。「.」で始まるもの・__MACOSX・__pycache__ は見ない。"""
    found: list[Path] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.casefold() not in ("__macosx", "__pycache__")]
        found.extend(Path(dirpath) / f for f in files if not f.startswith("."))
    yield from sorted(found, key=lambda p: p.relative_to(root).as_posix())


def find_files(root: Path | str | None = None, log: Logger | None = None) -> list[MarkFile]:
    """race_marks/ の下のレース印のファイル（相対パスの順。ZIP の中は名前の順）。フォルダが無ければ空。"""
    root = Path(root) if root is not None else config.race_marks_dir()
    log = log or (lambda m: None)
    out: list[MarkFile] = []
    if not root.is_dir():
        return out
    for p in _walk(root):
        kind = kind_of(p.name)
        if kind:
            out.append(MarkFile(kind, p))
            continue
        if not p.name.casefold().endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(p) as z:
                names = sorted(n for n in z.namelist() if not n.endswith("/"))
        except Exception as e:                               # 壊れた ZIP 1本で全体を止めない
            log(f"[WARN] レース印: {p.name} を ZIP として読めません（{type(e).__name__}: {e}）")
            continue
        for n in names:
            parts = n.replace("\\", "/").split("/")
            if any(x.startswith(".") or x.casefold() == "__macosx" for x in parts):
                continue
            kind = kind_of(parts[-1])
            if kind:
                out.append(MarkFile(kind, p, n))
    return out


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp932", errors="replace")


def _read_bytes(mf: MarkFile) -> bytes:
    if not mf.member:
        return mf.path.read_bytes()
    with zipfile.ZipFile(mf.path) as z:
        return z.read(mf.member)


def parse_text(text: str) -> tuple[list[tuple[str, str]], int, list[str]]:
    """「開催名, RX…, カラーコード+値」の行 → ([(RX, 値), …]（行の順のまま）, 読めなかった行数, その例)。
    2列目が RX + 8文字でない行は読めなかった行に数える（1行目の見出しは数えない）。"""
    rows: list[tuple[str, str]] = []
    bad, examples = 0, []
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if not row or not any(c.strip() for c in row):
            continue
        rx = unicodedata.normalize("NFKC", row[1]).strip().upper() if len(row) > 1 else ""
        if not _RX_RE.match(rx):
            if i == 0 and not re.search(r"\d{4,}", rx):               # 見出しの行
                continue
            bad += 1
            if len(examples) < 3:
                examples.append(f"{i + 1}行目 {rx[:16]!r}")
            continue
        rows.append((rx, clean_value(row[2] if len(row) > 2 else "")))
    return rows, bad, examples


class RaceMarks:
    """レース（RX…）→ 5種の印の値。get(レースID(新)の先頭16桁) で引く。"""

    def __init__(self, table: dict[str, dict[str, str]] | None = None):
        self.table: dict[str, dict[str, str]] = table or {}

    def __len__(self) -> int:
        return len(self.table)

    def get(self, rid16) -> dict[str, str]:
        """前走のレースID(新)先頭16桁 → {member, time, pace, cushion, moist_turf, moist_dirt}（無ければ全部 ""）。"""
        return self.table.get(rx_of(rid16)) or _EMPTY

    def counts(self) -> dict[str, int]:
        return {f: sum(1 for v in self.table.values() if v.get(f)) for f in FIELDS}

    def to_frame(self) -> pd.DataFrame:
        keys = sorted(self.table)
        data = {KEY: keys}
        for f in FIELDS:
            data[f] = [self.table[k].get(f, "") for k in keys]
        return pd.DataFrame(data, columns=COLUMNS, dtype=object)

    @staticmethod
    def from_frame(df: pd.DataFrame) -> "RaceMarks":
        table = {}
        cols = [df[c].tolist() if c in df.columns else [""] * len(df) for c in COLUMNS]
        for rx, *vals in zip(*cols):
            table[str(rx)] = {f: ("" if v is None or v != v else str(v)) for f, v in zip(FIELDS, vals)}
        return RaceMarks(table)


_EMPTY = {f: "" for f in FIELDS}


def read_files(files: Iterable[MarkFile], log: Logger | None = None) -> RaceMarks:
    """レース印のファイルを読む。同じレースの値が複数のファイルにあれば、後のファイルの値（空の値では上書きしない）。
    含水率は1つのファイルの中で同じレースの1行目 = 芝・2行目 = ダート（3行目からは使わない）。"""
    log = log or (lambda m: None)
    table: dict[str, dict[str, str]] = {}
    n_files = {k: 0 for k in KINDS}
    for mf in files:
        try:
            rows, bad, examples = parse_text(_decode(_read_bytes(mf)))
        except Exception as e:      # 読めないファイル（パスワードつきの ZIP・途中で切れた ZIP など）は、その1本だけ飛ばす
            log(f"[WARN] レース印（{KIND_LABELS[mf.kind]}）: {mf.label} を読めません（{type(e).__name__}: {e}）")
            continue
        if bad:
            log(f"[WARN] レース印（{KIND_LABELS[mf.kind]}）: {mf.label} の「開催名, RX+8桁, 値」として読めない行 {bad} 件を"
                f"飛ばしました（{', '.join(examples)}）")
        n_files[mf.kind] += 1
        if mf.kind == "moisture":
            seen: dict[str, int] = {}
            extra = 0
            for rx, v in rows:
                k = seen.get(rx, 0)
                seen[rx] = k + 1
                if k >= 2:
                    extra += 1
                    continue
                if k == 0:                                   # このファイルに出てきたレースは、芝・ダの組ごと入れ替える
                    rec = table.setdefault(rx, dict(_EMPTY))
                    keep = (rec["moist_turf"], rec["moist_dirt"])
                    rec["_old_moist"] = keep                 # 2行とも空なら前の値を残すために覚えておく
                    rec["moist_turf"], rec["moist_dirt"] = v, ""
                else:
                    table[rx]["moist_dirt"] = v
            for rx in seen:
                rec = table[rx]
                old = rec.pop("_old_moist")
                if not rec["moist_turf"] and not rec["moist_dirt"]:
                    rec["moist_turf"], rec["moist_dirt"] = old
            if extra:
                log(f"[WARN] レース印（{KIND_LABELS[mf.kind]}）: {mf.label} に同じレースの3行目以降が {extra} 行あります"
                    "（1行目 = 芝・2行目 = ダート だけを使います）")
            continue
        for rx, v in rows:
            if v:
                table.setdefault(rx, dict(_EMPTY))[mf.kind] = v
    marks = RaceMarks({k: v for k, v in table.items() if any(v.values())})
    if any(n_files.values()):
        c = marks.counts()
        log("[INFO] レース印: " + "・".join(f"{KIND_LABELS[k]} {n_files[k]} 本" for k in KINDS if n_files[k])
            + f" → {len(marks)} レース（メンバー {c['member']}・タイム {c['time']}・ペース {c['pace']}・クッション {c['cushion']}・"
            f"含水率 芝 {c['moist_turf']}／ダ {c['moist_dirt']}）")
    return marks


def load_dir(root: Path | str | None = None, log: Logger | None = None) -> RaceMarks:
    return read_files(find_files(root, log), log)


def save(marks: RaceMarks, master_dir: Path | str | None = None) -> Path:
    """master_csv/__race_marks__.csv.gz に書く（UTF-8・一時ファイルから置き換える。時刻は入れない）。"""
    d = Path(master_dir) if master_dir is not None else config.master_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / FILE
    tmp = d / (FILE + ".part")
    tmp.write_bytes(gzip.compress(marks.to_frame().to_csv(index=False).encode("utf-8"), compresslevel=6, mtime=0))
    os.replace(tmp, path)
    return path


def save_meta(master_dir: Path | str | None, enabled: bool, race_dir: Path | str | None = None) -> Path:
    """replace が外部レース印をどう読んだか（読まない・既定でないフォルダ）を master_csv/__race_marks__.json に残す。
    出馬表側（build_db）が同じ読み方をして、master と出馬表で違う印を見ないようにするため。
    フォルダは、既定（config.race_marks_dir()）と同じなら空（出馬表側もその場所の既定を使う）。"""
    d = Path(master_dir) if master_dir is not None else config.master_dir()
    d.mkdir(parents=True, exist_ok=True)
    where = ""
    if race_dir is not None:
        try:
            if Path(race_dir).resolve() != config.race_marks_dir().resolve():
                where = str(Path(race_dir).resolve())
        except OSError:
            where = str(race_dir)
    path = d / META
    tmp = d / (META + ".part")
    tmp.write_text(json.dumps({"version": 1, "enabled": bool(enabled), "dir": where}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_meta(master_dir: Path | str | None = None) -> dict | None:
    """master_csv/__race_marks__.json（{"enabled": bool, "dir": str}）。無い・読めない・形が違えば None。"""
    p = (Path(master_dir) if master_dir is not None else config.master_dir()) / META
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("enabled"), bool):
        return None
    where = d.get("dir")
    return {"enabled": d["enabled"], "dir": where if isinstance(where, str) else ""}


def load_cache(master_dir: Path | str | None = None) -> RaceMarks | None:
    """master_csv/__race_marks__.csv.gz。無い・読めない・形が違えば None。"""
    p = (Path(master_dir) if master_dir is not None else config.master_dir()) / FILE
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, encoding="utf-8", dtype=str, keep_default_na=False)
    except Exception:
        return None
    if any(c not in df.columns for c in COLUMNS):
        return None
    return RaceMarks.from_frame(df)


def load(race_dir: Path | str | None = None, master_dir: Path | str | None = None, log: Logger | None = None,
         enabled: bool | None = None) -> RaceMarks:
    """出馬表側（build_db）用: race_marks/ にファイルがあればそれを読み（前走が先週のレースでも引けるように、新しいほう）、
    無ければ master_csv/__race_marks__.csv.gz（replace が書いたもの）。どちらも無ければ空。
    race_dir・enabled を指定しなければ、replace の読み方（__race_marks__.json）に合わせる: replace が印を読まなかった
    （--no-race-marks）なら出馬表側も読まず、別のフォルダ（--marks-dir）を読んだならそのフォルダを読む。
    race_dir を指定すれば、そのフォルダを読む（enabled=False なら読まない）。"""
    log = log or (lambda m: None)
    meta = load_meta(master_dir) if (race_dir is None and enabled is None) else None
    if enabled is None:
        enabled = meta["enabled"] if meta else True
        if not enabled:
            log("[INFO] レース印: replace が外部レース印を読まずに master を作っているので（--no-race-marks）、出馬表側も"
                "使いません（使うなら replace をやり直すか、build_db に --marks-dir を付ける）")
    if not enabled:
        return RaceMarks()
    if race_dir is None and meta and meta["dir"]:
        race_dir = meta["dir"]
        log(f"[INFO] レース印: replace と同じフォルダ {race_dir} を読みます")
    files = find_files(race_dir, log)
    if files:
        return read_files(files, log)
    cached = load_cache(master_dir)
    if cached is not None:
        log(f"[INFO] レース印: race_marks のファイルが無いので、{FILE}（{len(cached)} レース）を使います")
        return cached
    return RaceMarks()


__all__ = ["KINDS", "FIELDS", "FILE", "RaceMarks", "MarkFile", "rx_of", "day_char", "clean_value", "kind_of", "find_files",
           "parse_text", "read_files", "load_dir", "save", "load_cache", "load", "META", "save_meta", "load_meta"]
