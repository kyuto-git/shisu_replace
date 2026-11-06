"""外部指数ファイル（ext_indices/ 以下）を見つけて読み、過去レースのベースCSVに値を付ける。

ext_indices/ の下（階層は自由）に置いたファイルを、フォルダ名・ファイル名で指数に振り分ける。
ファイルの形（A〜D）は指数ごとに決まっている。

  形式A 馬単位・新18桁ID   `レースID(新), 指数値`（見出しは有っても無くても良い。ingest の TARGET 取込用CSV）
         オメガ指数・かよちん指数・keibaAI指数・吉馬6指数・安井8指数
  形式B 馬単位・旧10桁ID   `旧ID, 指数値, 順位`（見出しなし）  StridePlus/ の12指数（ファイル名の接頭辞で決める。st_se は廃止）
  形式C レース単位・12桁ID `ID12, 指数1, 順位1, 指数2, 順位2, …`（馬番1から順。見出しなし）  Gold
  形式D レース単位・旧8桁ID `ID8, 指数1, 指数2, …`（値だけ。見出しなし）  BC（先頭に RX）・ZI

ID の形:
  新18桁 = YYYYMMDD + 場所2 + 回2 + 日2 + R2 + 馬番2          例 202610250404041206
  旧10桁 = 場所2 + 年下2桁 + 回1 + 日1（10=A, 11=B, 12=C）+ R2 + 馬番2   例 0426441206
  12桁   = YYYYMMDD + 場所2 + R2
  旧8桁  = 旧10桁の先頭8文字（レース単位）
  旧ID は先頭の 0 が落ちていることがある（9文字・7文字）ので、0 を補って10文字・8文字にする。

照合のキーは1本の文字列にそろえる（ExtLookup.values のキー）:
  A = 新18桁、B = 旧10桁、C = 12桁 + 馬番2桁、D = 旧8桁 + 馬番2桁（= 旧10桁）

使い方:
  sources, skipped = discover("ext_indices")
  for name, src in sources.items():
      lookup = load_source(src)
      values, ranks = attach(base_df, lookup)   # ranks は形式B・C だけ
"""
from __future__ import annotations

import codecs
import csv
import itertools
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import pandas as pd

from .csvio import norm_text
from .indicators import canonical_indicator

Logger = Callable[[str], None]

RID = "レースID(新)"
OLD_RID = "レースID(旧)"
UMA = "馬番"

# ---------------------------------------------------------------------------
# 対応する指数と形式
# ---------------------------------------------------------------------------
YOSHIUMA_KEYS: dict[str, str] = {
    "sp_ability": "吉馬_SP能力値", "senkou": "吉馬_先行力", "sp_trust": "吉馬_SP信頼",
    "sp_control": "吉馬_SP調整", "sp_max": "吉馬_SP最大", "sueashi": "吉馬_末脚力",
}
YASUI_KEYS: dict[str, str] = {
    "rank": "安井_ランク", "speed": "安井_スピード", "stamina": "安井_スタミナ", "power": "安井_パワー",
    "konjo": "安井_根性", "kashikosa": "安井_賢さ", "tairyoku": "安井_体力", "yaruki": "安井_やる気",
}
# StridePlus のファイル名の接頭辞 → 指数（None は廃止。照合は長い接頭辞から）
STRIDE_PREFIXES: dict[str, str | None] = {
    "sav": "SAV", "st": "ST指数", "gokei": "合計値", "tenkai": "展開", "siagari": "仕上り",
    "senko": "先行", "tsuiso": "追走", "jikyu": "持久", "jizoku": "持続", "shunpatsu": "瞬発",
    "mae_stime": "想定前半", "ato_stime": "想定後半",
    "st_se": None,  # 成績ST指数（廃止）。'st' より先に見る
}
_STRIDE_ORDER = sorted(STRIDE_PREFIXES.items(), key=lambda kv: -len(kv[0]))

PATTERN_OF: dict[str, str] = {
    "オメガ指数": "A", "かよちん指数": "A", "keibaAI指数": "A",
    **{n: "A" for n in YOSHIUMA_KEYS.values()},
    **{n: "A" for n in YASUI_KEYS.values()},
    **{n: "B" for n in STRIDE_PREFIXES.values() if n},
    "Gold": "C", "BC": "D", "ZI": "D",
}
SUPPORTED: tuple[str, ...] = tuple(PATTERN_OF)

_PATTERN_LABEL = {"A": "新18桁のレースID(新)", "B": "旧10桁ID", "C": "12桁のレースID", "D": "旧8桁のレースID"}


@dataclass
class ExtSource:
    """1つの指数のファイル群（discover の結果）。files は相対パスの順で、後のファイルが優先。"""
    indicator: str
    pattern: str            # 'A' | 'B' | 'C' | 'D'
    files: list[Path]


@dataclass
class ExtLookup:
    """1つの指数の値（load_source の結果）。キーは形式ごとに1本の文字列（モジュールの説明を参照）。"""
    indicator: str
    pattern: str
    values: dict[str, str]
    ranks: dict[str, str] | None    # 形式B・C だけ（順位が無ければ ''）
    files: list[Path] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.values)


# ---------------------------------------------------------------------------
# ID の変換
# ---------------------------------------------------------------------------
_CODE_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 回・日の1文字（10=A, 11=B, 12=C …）
_TWO_TO_CHAR = {f"{i:02d}": c for i, c in enumerate(_CODE_CHARS)}
_OLD10_PAT = r"\d{4}[0-9A-Z]{2}\d{4}"
_OLD8_PAT = r"\d{4}[0-9A-Z]{2}\d{2}"
_OLD10_RE = re.compile(_OLD10_PAT)
_OLD8_RE = re.compile(_OLD8_PAT)
_FLOAT0_RE = re.compile(r"(\d+)\.0+")
_NON_DIGIT = re.compile(r"\D")
_NULL = frozenset({"nan", "NaN", "NAN", "None", "none", "NULL", "null", "<NA>", "NaT"})


def _cell(v) -> str:
    """セル → 文字列（前後の空白を除く）。ASCII でなければ NFKC・空白除去（全角数字・全角空白）。"""
    if not isinstance(v, str):
        return norm_text(v)
    s = v.strip()
    if not s.isascii():
        return norm_text(s)
    return "" if s in _NULL else s


def _digits(v, size: int) -> str:
    """数字だけの ID（'…06.0'・空白・全角も受ける）。桁数が size でなければ ''。"""
    s = v.strip() if isinstance(v, str) else norm_text(v)
    if len(s) == size and s.isascii() and s.isdigit():
        return s
    s = norm_text(s).lstrip("﻿")
    m = _FLOAT0_RE.fullmatch(s)
    if m:
        s = m.group(1)
    s = _NON_DIGIT.sub("", s)
    return s if len(s) == size else ""


def _alnum_id(v, size: int, regex: re.Pattern, strip_rx: bool = False) -> str:
    """旧ID（英数字）を大文字にし、先頭の 0 が1つ落ちていれば補う。形が違えば ''。"""
    s = _cell(v).lstrip("﻿").replace(" ", "").upper()
    if strip_rx and s.startswith("RX"):
        s = s[2:]
    if len(s) != size:
        m = _FLOAT0_RE.fullmatch(s)
        if m:
            s = m.group(1)
        if len(s) == size - 1:
            s = "0" + s
    return s if regex.fullmatch(s) else ""


def normalize_old_id(s) -> str:
    """旧10桁ID（馬単位）。'520380101' → '0520380101'、'05264b0101' → '05264B0101'。形が違えば ''。"""
    return _alnum_id(s, 10, _OLD10_RE)


def normalize_old_race_id(s) -> str:
    """旧8桁のレースID。先頭の 'RX' を除き、'4261801' → '04261801'。形が違えば ''。"""
    return _alnum_id(s, 8, _OLD8_RE, strip_rx=True)


def old_id_from_new(rid18) -> str:
    """新18桁 → 旧10桁。202610250404041206 → 0426441206、202611010504110101 → 05264B0101。"""
    r = _digits(rid18, 18)
    if not r:
        return ""
    kai, nichi = _TWO_TO_CHAR.get(r[10:12]), _TWO_TO_CHAR.get(r[12:14])
    if kai is None or nichi is None:
        return ""
    return r[8:10] + r[2:4] + kai + nichi + r[14:18]


def _is_rid_header(v) -> bool:
    return norm_text(v).lstrip("﻿") in ("レースID(新)", "レースID")


# ---------------------------------------------------------------------------
# ファイルの読み方（1行ずつ。大きなファイルでも DataFrame は作らない）
# ---------------------------------------------------------------------------
def _encoding(path: Path, sample: int = 1 << 16) -> str:
    """先頭 64KB で符号化を決める。BOM → utf-8-sig、UTF-8 として読めれば utf-8、ほかは cp932。

    リポジトリの他の読込（cp932 → utf-8 の順）と逆にしているのは、BOM の無い UTF-8 の日本語
    （見出しの「レースID(新)」など）が cp932 としてもエラー無く読めて文字化けすることがあるため。
    ID と数値だけの行はどちらで読んでも同じ。
    """
    with open(path, "rb") as f:
        head = f.read(sample)
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    for enc in ("utf-8", "cp932"):
        try:
            codecs.getincrementaldecoder(enc)().decode(head, final=False)
            return enc
        except UnicodeDecodeError:
            pass
    return "cp932"


def _rows(path: Path) -> Iterator[tuple[int, list[str]]]:
    """(行番号, セル) を1行ずつ。空行・カンマだけの行は飛ばす。読めない文字は置き換える。"""
    with open(path, "r", encoding=_encoding(path), errors="replace", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            if not row[0].strip() and not any(c.strip() for c in row):
                continue
            yield reader.line_num, row


def _short(v, n: int = 24) -> str:
    """ログ用に短く（制御文字は ? に）。"""
    s = "".join(c if c.isprintable() else "?" for c in str(v).strip()[:n + 1])
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------------------
# 名前での振り分け
# ---------------------------------------------------------------------------
def _nk(name: str) -> str:
    """名前の比較用: NFKC・空白除去・小文字・'-' を '_' に。"""
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(name))).casefold()
    return s.replace("-", "_")


_KICHIUMA_RE = re.compile(r"(?:^|_)kichiuma_(" + "|".join(YOSHIUMA_KEYS) + r")(?![a-z])")
_YASUI_RE = re.compile(r"(?:^|_)yasui_(" + "|".join(YASUI_KEYS) + r")(?![a-z])")
_NAME_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?:^|[^a-z])omega(?![a-z])"), "オメガ指数"),
    (re.compile(r"(?:^|[^a-z])kayochin(?![a-z])"), "かよちん指数"),
    (re.compile(r"(?:^|[^a-z])keibaai(?![a-z])"), "keibaAI指数"),
    (re.compile(r"^gold"), "Gold"),                       # gold*, GoldAirplane*
    (re.compile(r"^bc(?:_?index)?(?![a-z])"), "BC"),      # BC2024, BCindex
    (re.compile(r"^zi(?:_?data)?(?![a-z])"), "ZI"),       # ZI2024, ZI_DATA
)

_DISCONTINUED_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("カツラキ", "katsuraki", "katuraki"), "カツラキ"),
    (("hprtv5c",), "HPRTV5C"),
    (("成績st",), "成績ST指数"),
    (("ittai", "一隊", "結果分析"), "結果分析スピード指数（一隊）"),
    (("33rap",), "33rap"),
)


def _name_indicator(raw: str) -> str | None:
    """フォルダ名・ファイル名（拡張子なし）→ 指数。決められなければ None。"""
    nk = _nk(raw)
    m = _KICHIUMA_RE.search(nk)
    if m:
        return YOSHIUMA_KEYS[m.group(1)]
    m = _YASUI_RE.search(nk)
    if m:
        return YASUI_KEYS[m.group(1)]
    for regex, name in _NAME_RULES:
        if regex.search(nk):
            return name
    canon = canonical_indicator(raw)          # 'SAV'・'オメガ指数'・'GoldAirplane' など台帳の名前
    return canon if canon in PATTERN_OF else None


def _stride_prefix(raw: str) -> tuple[bool, str | None]:
    """StridePlus の接頭辞。(当たったか, 指数)。長い接頭辞から見る（'st_se' を 'st' より先に）。

    3文字以下の短い接頭辞（st・sav）は、直後が英字なら別の語とみなす（'stride' を ST指数にしない）。
    長い接頭辞は続きがあっても当てる（'senkou_2020' → 先行）。
    """
    nk = _nk(raw)
    for prefix, name in _STRIDE_ORDER:
        if not nk.startswith(prefix):
            continue
        if len(prefix) <= 3 and re.match(r"[a-z]", nk[len(prefix):len(prefix) + 1]):
            continue
        return True, name
    return False, None


def _discontinued(raw: str) -> str | None:
    """廃止された指数の名前なら、その指数名。"""
    nk = _nk(raw)
    for words, name in _DISCONTINUED_WORDS:
        if any(w in nk for w in words):
            return name
    if nk.startswith("st_se"):
        return "成績ST指数"
    toks = nk.split("_")
    for n in ("1", "2"):
        if f"上がり{n}f" in nk or f"agari{n}f" in nk or f"agari_{n}f" in nk:
            return f"上がり{n}F"
        # lap の出力 1F/・{日付}_1F.csv。rabbit_1F は廃止ではない（未対応の指数）
        if toks[-1] == f"{n}f" and not (len(toks) >= 2 and toks[-2] == "rabbit"):
            return f"上がり{n}F"
    return None


def _is_stride_folder(name: str) -> bool:
    return _nk(name).replace("_", "") == "strideplus"


def _classify(rel: Path) -> tuple[str | None, str]:
    """相対パス → (指数, 決められないときの理由)。

    ファイル名で決まる指数と、いちばん内側の（指数が決まる）フォルダ名の指数が違えば使わない。
    StridePlus/ の中はファイル名の接頭辞で決め、フォルダは StridePlus より下だけを見る。
    """
    folders = rel.parts[:-1]
    stem = Path(rel.name).stem
    idx = next((i for i, p in enumerate(folders) if _is_stride_folder(p)), None)
    if idx is not None:
        def by_name(raw: str) -> str | None:
            hit, name = _stride_prefix(raw)
            return name if hit else _name_indicator(raw)
        scan = folders[idx + 1:]
    else:
        by_name = _name_indicator
        scan = folders
    f_ind = by_name(stem)
    g_ind = next((g for g in (by_name(p) for p in reversed(scan)) if g), None)
    if f_ind and g_ind and f_ind != g_ind:
        return None, f"ファイル名（{f_ind}）とフォルダ名（{g_ind}）が食い違う"
    ind = f_ind or g_ind
    if ind:
        return ind, ""
    if idx is not None:
        return None, "StridePlus の中で、ファイル名の接頭辞から指数を決められない"
    return None, "名前から指数を決められない"


def _helper_reason(rel: Path) -> str | None:
    """取得ツールが同じフォルダに書く補助ファイル（2列の形ではない）なら理由。"""
    name = rel.name.casefold()
    stem = _nk(Path(rel.name).stem)
    if any(p.casefold() == "entries_in" for p in rel.parts[:-1]):
        return "entries_in/ の出馬表用CSV（使わない）"
    if re.search(r"_merged(?:_\d+)?$", stem):
        return "マージ完全版（*_merged.csv。使わない）"
    if name == "yasui_extract.csv":
        return "安井の抽出一覧（yasui_extract.csv。使わない）"
    if re.search(r"_noid(?:_\d+)?$", stem):
        return "レースID の無い一覧（*_noid.csv。使わない）"
    return None


def _content_reason(path: Path, pattern: str) -> str | None:
    """先頭の数行が形式に合わなければ理由（見出しが ID でない 日付 などのファイルを外す）。"""
    try:
        rows = [r for _, r in itertools.islice(_rows(path), 3)]
    except (OSError, csv.Error) as e:
        return f"読めない（{e}）"
    if not rows:
        return "空のファイル"
    first = rows[0]
    n_cells = sum(1 for c in first if c.strip())
    if pattern == "A":
        if _digits(first[0], 18):
            return None if n_cells <= 2 else f"2列の形でない（{n_cells}列）"
        if _is_rid_header(first[0]):
            return None if n_cells <= 2 else f"見出しが2列でない（{_short(','.join(first))}）"
        return f"1列目が{_PATTERN_LABEL[pattern]}でない（{_short(first[0])}）"
    check = {"B": normalize_old_id, "C": lambda v: _digits(v, 12), "D": normalize_old_race_id}[pattern]
    if any(check(r[0]) for r in rows):
        return None
    return f"1列目が{_PATTERN_LABEL[pattern]}でない（{_short(first[0])}）"


_IGNORED_DIRS = {"__macosx", "__pycache__"}
_IGNORED_FILES = {"thumbs.db", "desktop.ini"}


def _walk(root: Path) -> Iterator[Path]:
    """root 以下のファイル（名前順）。隠しファイル・__MACOSX などは見ない。"""
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.casefold() not in _IGNORED_DIRS)
        for f in sorted(files):
            if f.startswith((".", "~$")) or f.casefold() in _IGNORED_FILES:
                continue
            yield Path(cur) / f


def discover(root: Path | str, log: Logger = print) -> tuple[dict[str, ExtSource], list[tuple[Path, str]]]:
    """root 以下のファイルを指数に振り分ける。

    戻り値: (正式指数名 → ExtSource（SUPPORTED の順）, 使わないファイルと理由のリスト)。
    使わないもの: entries_in/ の下・CSV でない・補助ファイル（*_merged・yasui_extract・*_noid）・
    廃止された指数（INFO で種類ごとに1回だけ出す）・指数を決められない名前・形式に合わない中身。
    """
    root = Path(root)
    if not root.is_dir():
        log(f"[INFO] 外部指数のフォルダがありません: {root}")
        return {}, []
    found: dict[str, list[Path]] = {}
    skipped: list[tuple[Path, str]] = []
    gone: dict[str, list[Path]] = {}
    n_helper = 0
    unknown: list[tuple[Path, str]] = []
    for path in _walk(root):
        rel = path.relative_to(root)
        reason = _helper_reason(rel)
        if reason is None and path.suffix.casefold() != ".csv":
            reason = "CSV でない"
        if reason is not None:
            skipped.append((path, reason))
            n_helper += 1
            continue
        kind = next((k for k in map(_discontinued, (*rel.parts[:-1], path.stem)) if k), None)
        if kind:
            gone.setdefault(kind, []).append(path)
            skipped.append((path, f"廃止された指数（{kind}）"))
            continue
        ind, reason = _classify(rel)
        if ind is not None:
            reason = _content_reason(path, PATTERN_OF[ind]) or ""
            if reason:
                reason = f"{ind}（形式{PATTERN_OF[ind]}）として読めない: {reason}"
        if reason:
            skipped.append((path, reason))
            unknown.append((rel, reason))
            continue
        found.setdefault(ind, []).append(path)

    for kind, paths in gone.items():
        log(f"[INFO] 廃止された指数のため使いません: {kind}（{len(paths)}ファイル。例: "
            f"{paths[0].relative_to(root).as_posix()}）")
    sources: dict[str, ExtSource] = {}
    for ind in SUPPORTED:
        if ind in found:
            files = sorted(found[ind], key=lambda p: p.relative_to(root).parts)
            sources[ind] = ExtSource(ind, PATTERN_OF[ind], files)
            log(f"[INFO] 外部指数 {ind}（形式{PATTERN_OF[ind]}）: {len(files)}ファイル")
    if n_helper:
        log(f"[INFO] 補助ファイルなど {n_helper}本は使いません（マージ完全版・出馬表用・CSV でないもの）")
    if unknown:
        ex = " / ".join(f"{r.as_posix()}: {why}" for r, why in unknown[:5])
        log(f"[WARN] 使えないファイル {len(unknown)}本（{ex}{' …' if len(unknown) > 5 else ''}）")
    return sources, skipped


# ---------------------------------------------------------------------------
# 読込
# ---------------------------------------------------------------------------
class _FileStats:
    def __init__(self) -> None:
        self.bad = 0
        self.examples: list[str] = []
        self.line = 0

    def bad_row(self, line: int, cell) -> None:
        self.bad += 1
        if len(self.examples) < 3:
            self.examples.append(f"{line}行目 {_short(cell, 20)!r}")


Row = tuple[str, str, str]   # (キー, 値, 順位)


def _parse_a(path: Path, st: _FileStats) -> Iterator[Row]:
    for line, row in _rows(path):
        st.line = line
        key = _digits(row[0], 18)
        if not key:
            if not _is_rid_header(row[0]):        # 見出し（ファイルをつないだときの途中の見出しも）は飛ばす
                st.bad_row(line, row[0])
            continue
        yield key, (_cell(row[1]) if len(row) > 1 else ""), ""


def _parse_b(path: Path, st: _FileStats) -> Iterator[Row]:
    for line, row in _rows(path):
        st.line = line
        key = normalize_old_id(row[0])
        if not key:
            st.bad_row(line, row[0])
            continue
        n = len(row)
        yield key, (_cell(row[1]) if n > 1 else ""), (_cell(row[2]) if n > 2 else "")


def _parse_c(path: Path, st: _FileStats) -> Iterator[Row]:
    for line, row in _rows(path):
        st.line = line
        rid = _digits(row[0], 12)
        if not rid:
            st.bad_row(line, row[0])
            continue
        n = len(row)
        for k in range(1, min(n // 2, 99) + 1):      # 馬番 k: 値 = 2k-1 列目, 順位 = 2k 列目
            yield f"{rid}{k:02d}", _cell(row[2 * k - 1]), (_cell(row[2 * k]) if 2 * k < n else "")


def _parse_d(path: Path, st: _FileStats) -> Iterator[Row]:
    for line, row in _rows(path):
        st.line = line
        rid = normalize_old_race_id(row[0])
        if not rid:
            st.bad_row(line, row[0])
            continue
        for k in range(1, min(len(row) - 1, 99) + 1):  # 馬番 k: 値 = k 列目
            yield f"{rid}{k:02d}", _cell(row[k]), ""


_PARSERS = {"A": _parse_a, "B": _parse_b, "C": _parse_c, "D": _parse_d}


def load_source(src: ExtSource, log: Logger = print) -> ExtLookup:
    """1つの指数のファイルをすべて読む。同じキーは後のファイル（同じファイルなら後の行）が優先。

    値が空のセルは入れない（前のファイルの値を空で消さない）。値・順位は前後の空白を除いた文字列のまま。
    """
    if src.pattern not in _PARSERS:
        raise ValueError(f"未知の形式: {src.pattern!r}（{src.indicator}）")
    parse = _PARSERS[src.pattern]
    values: dict[str, str] = {}
    ranks: dict[str, str] | None = {} if src.pattern in ("B", "C") else None
    dup = changed = 0
    for path in src.files:
        st = _FileStats()
        try:
            for key, val, rank in parse(Path(path), st):
                if not val:
                    continue
                old = values.get(key)
                if old is not None:
                    dup += 1
                    if old != val or (ranks is not None and ranks.get(key) != rank):
                        changed += 1
                values[key] = val
                if ranks is not None:
                    ranks[key] = rank
        except (OSError, csv.Error) as e:
            log(f"[WARN] {src.indicator}: {Path(path).name} は {st.line}行目付近までしか読めませんでした（{e}）")
        if st.bad:
            log(f"[WARN] {src.indicator}: {Path(path).name} の {_PATTERN_LABEL[src.pattern]}が読めない行 "
                f"{st.bad}件を飛ばしました（{', '.join(st.examples)}）")
    if dup:
        log(f"[INFO] {src.indicator}: 同じ馬の値が複数あり、後のファイル（同じファイルでは後の行）で上書き "
            f"{dup}件（うち値が違う {changed}件）")
    log(f"[INFO] {src.indicator}（形式{src.pattern}）: {len(src.files)}ファイル → {len(values)}頭分")
    return ExtLookup(src.indicator, src.pattern, values, ranks, list(src.files))


# ---------------------------------------------------------------------------
# ベースへの付与（ベクトル化）
# ---------------------------------------------------------------------------
def _text(s: pd.Series) -> pd.Series:
    s = s.astype(object)
    return s.where(s.notna(), "").astype(str).str.normalize("NFKC").str.strip()


def _uma2_series(base: pd.DataFrame) -> pd.Series:
    """馬番 → 2桁（'1'・'01'・1・1.0）。読めなければ ''。"""
    if UMA not in base.columns:
        return pd.Series("", index=base.index, dtype=object)
    u = _text(base[UMA]).str.replace(r"\.0+$", "", regex=True)
    ok = u.str.fullmatch(r"\d{1,2}").fillna(False).astype(bool) & ~u.isin(["0", "00"])
    return u.str.zfill(2).where(ok, "").astype(object)


def _rid18_series(base: pd.DataFrame) -> pd.Series:
    """レースID(新) → 数字18桁。16桁（レース単位）なら 馬番 を付ける。読めなければ ''。"""
    if RID not in base.columns:
        return pd.Series("", index=base.index, dtype=object)
    r = _text(base[RID]).str.replace(r"\.0+$", "", regex=True).str.replace(r"\D", "", regex=True)
    uma = _uma2_series(base)
    need = ((r.str.len() == 16) & (uma != "")).astype(bool)
    r = r.where(~need, r + uma)
    return r.where(r.str.len() == 18, "").astype(object)


def _old10_series(base: pd.DataFrame, rid18: pd.Series) -> pd.Series:
    """旧10桁ID。レースID(旧) 列があればそれ（9文字は 0 を補う）、空なら レースID(新) から作る。"""
    kai = rid18.str.slice(10, 12).map(_TWO_TO_CHAR)
    nichi = rid18.str.slice(12, 14).map(_TWO_TO_CHAR)
    ok = ((rid18.str.len() == 18) & kai.notna() & nichi.notna()).astype(bool)
    derived = (rid18.str.slice(8, 10) + rid18.str.slice(2, 4) + kai.fillna("").astype(str)
               + nichi.fillna("").astype(str) + rid18.str.slice(14, 18))
    derived = derived.where(ok, "").astype(object)
    if OLD_RID not in base.columns:
        return derived
    g = _text(base[OLD_RID]).str.replace(r"\s+", "", regex=True).str.upper()
    g = g.str.replace(r"^(\d+)\.0+$", r"\1", regex=True)
    g = g.where(g.str.len() != 9, "0" + g)
    g = g.where(g.str.fullmatch(_OLD10_PAT).fillna(False).astype(bool), "").astype(object)
    return g.where(g != "", derived).astype(object)


def _keys(base: pd.DataFrame, pattern: str) -> pd.Series:
    if pattern in ("A", "C") and RID not in base.columns:
        raise KeyError(f"ベースに {RID} 列がありません（形式{pattern}の照合に必要）")
    if pattern in ("B", "D") and RID not in base.columns and OLD_RID not in base.columns:
        raise KeyError(f"ベースに {RID} 列も {OLD_RID} 列もありません（形式{pattern}の照合に必要）")
    rid18 = _rid18_series(base)
    if pattern == "A":
        return rid18
    if pattern == "C":                       # 12桁 + 馬番2桁 = YYYYMMDD + 場所 + R + 馬番
        k = rid18.str.slice(0, 10) + rid18.str.slice(14, 18)
        return k.where(rid18 != "", "").astype(object)
    if pattern in ("B", "D"):                # D のキー（旧8桁 + 馬番2桁）も旧10桁と同じ形
        return _old10_series(base, rid18)
    raise ValueError(f"未知の形式: {pattern!r}")


def attach(base: pd.DataFrame, lookup: ExtLookup) -> tuple[pd.Series, pd.Series | None]:
    """ベースの各行に指数の値（と順位）を付ける。

    戻り値: (値, 順位)。どちらも base.index にそろえた文字列の Series で、見つからなければ ''。
    順位は形式B・C だけ（A・D は None）。
    """
    keys = _keys(base, lookup.pattern).tolist()
    get = lookup.values.get
    values = pd.Series([get(k, "") for k in keys], index=base.index, dtype=object)
    if lookup.ranks is None:
        return values, None
    rget = lookup.ranks.get
    ranks = pd.Series([rget(k, "") for k in keys], index=base.index, dtype=object)
    return values, ranks


__all__ = [
    "ExtSource", "ExtLookup", "SUPPORTED", "PATTERN_OF", "YOSHIUMA_KEYS", "YASUI_KEYS", "STRIDE_PREFIXES",
    "discover", "load_source", "attach", "old_id_from_new", "normalize_old_id", "normalize_old_race_id",
]
