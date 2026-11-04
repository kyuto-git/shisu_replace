"""文字の正規化と CSV 入出力。

- norm_text: キーに使う文字列の正規化（NFKC・空白除去・欠損表記の空文字化）。
  過去側・出馬表側とも、結合データの部品は必ずこれを通す。
- 書き出しは cp932。shift_jis だと「髙」「①」「～」などが書けず、
  過去側だけ文字が消えて出馬表側と一致しなくなるため（旧版の不具合）。
- パスが .gz で終わるときは gzip 圧縮して書く（読込は pandas が拡張子から自動判定）。
"""
from __future__ import annotations

import gzip
import math
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

READ_ENCODINGS = ("cp932", "utf-8-sig", "utf-8")
WRITE_ENCODING = "cp932"

_WS = re.compile(r"\s+")
_NULL_TOKENS = frozenset({"", "nan", "NaN", "NAN", "None", "none", "NULL", "null", "<NA>", "NaT"})


def norm_text(value) -> str:
    """1値を正規化する。欠損（None/NaN/pd.NA/'nan' など）は空文字。"""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    try:
        if value is pd.NA or value is pd.NaT:
            return ""
    except Exception:  # pragma: no cover
        pass
    s = unicodedata.normalize("NFKC", str(value))
    s = _WS.sub("", s)
    return "" if s in _NULL_TOKENS else s


def norm_series(series: pd.Series) -> pd.Series:
    """Series 版 norm_text（ベクトル化）。戻り値は object dtype の文字列。"""
    s = series.astype(object)
    s = s.where(s.notna(), "")
    s = s.astype(str).str.normalize("NFKC").str.replace(r"\s+", "", regex=True)
    s = s.where(~s.isin(_NULL_TOKENS), "")
    return s.astype(object)


def read_csv_any(path, encodings=READ_ENCODINGS) -> pd.DataFrame:
    """全列を文字列として読む（空欄は ""）。cp932 → utf-8-sig → utf-8 の順に試す。"""
    last = None
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
        except UnicodeDecodeError as e:
            last = e
        except pd.errors.ParserError:
            raise
    # どの符号化でも厳密には読めない場合だけ、置換して読む（警告を出す）
    print(f"[WARN] {Path(path).name}: {READ_ENCODINGS} で厳密に読めないため置換読み込みします ({last})",
          file=sys.stderr)
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="cp932", errors="replace", newline="") as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False)


def write_csv(df: pd.DataFrame, path) -> None:
    """cp932 で書く。書けない文字は削除し、何を消したかを警告する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = df.to_csv(index=False)
    try:
        data = text.encode(WRITE_ENCODING)
    except UnicodeEncodeError:
        bad = sorted({ch for ch in text if not _encodable(ch)})
        print(f"[WARN] {path.name}: cp932 で書けない文字を削除しました: {''.join(bad)[:40]}",
              file=sys.stderr)
        data = text.encode(WRITE_ENCODING, errors="ignore")
    if path.name.endswith(".gz"):
        data = gzip.compress(data, compresslevel=6, mtime=0)
    path.write_bytes(data)


def _encodable(ch: str) -> bool:
    try:
        ch.encode(WRITE_ENCODING)
        return True
    except UnicodeEncodeError:
        return False
