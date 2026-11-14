"""成績表の保存形式（aggregate_stats_ver2.py が書き、build_db.py・screen_families.py・テストが読む）。

2026-11-10〜: master 1本（{地域}_{指数名}）につき1ファイル agg_csv/agg_master_{地域}_{指数名}.csv.gz
  - 1行 = 1結合データ。列は旧来の成績表（foragg_*.csv）の列 ＋ ROW_META
    （family, odds_mode, rank_scheme, indicator, region, テーブル名）。テーブル名は旧来の成績表のテーブル名と同じ
  - 書き出しは一時ファイル（.part）に書いてから置き換える（途中で止まっても完成したファイルだけが残る。--resume 用）
  - 文字コードは cp932（旧来の成績表と同じ。書けない文字は消して警告）
旧来の形（2026-11-09 まで。family × スキーム × オッズ帯ごとに1本の foragg_*.csv）も read_agg_tables で読める。

コンパクト化（aggregate の --compact。既定 ON）: compact_mask の条件のどれかを満たす行だけを残す
  a) classify（shisu/classify.py）で 絶対軸・軸候補・相手候補・消し候補 のどれかになる行
     （判定の値は build_db が DB に入れる値と同じ: 5つの率の *_val は文字列の括弧内の推定値）
  b) WEDGE >= min_wedge または adjusted_WEDGE >= min_wedge（既定 0.70。0 以下ならこの条件を使わない）
  c) layer が FULL_LAYERS（初期セットのコース固定 A）の family の行、または 件数 >= min_count（既定30。0 以下なら件数の条件を使わない）
"""
from __future__ import annotations

import gzip
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .classify import classify_frame
from .csvio import READ_ENCODINGS, WRITE_ENCODING, read_csv_any
from .naming import (AGG_MASTER_PREFIX, AGG_MASTER_SUFFIX, FORAGG_PREFIX, agg_filename, file_stem,
                     parse_agg_master_name, parse_foragg_stem)
from .wedge import WEDGE_FLOAT_COLUMNS, WEDGE_INT_COLUMNS

RATE_NAMES = ("勝率", "連対率", "複勝率", "2着率", "3着率")
FLOAT_COLUMNS = ("掲示板率_val", "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値") + WEDGE_FLOAT_COLUMNS
INT_COLUMNS = ("件数",) + WEDGE_INT_COLUMNS
ROW_META = ("family", "odds_mode", "rank_scheme", "indicator", "region", "テーブル名")

COMPACT_MIN_COUNT = 30
COMPACT_MIN_WEDGE = 0.70
FULL_LAYERS = ("A",)
PART_SUFFIX = ".part"

_EST_RE = re.compile(r"^\s*([\d.]+)%\s*(?:[(（]\s*([\d.]+)%\s*[)）])?")


def estimate_percent(series: pd.Series) -> pd.Series:
    """'45.00%（47.10%）' → 47.10（括弧内の推定値）。括弧が無ければ先頭の値。取れなければ NaN。"""
    m = series.astype(str).str.extract(_EST_RE)
    return pd.to_numeric(m[1].where(m[1].notna(), m[0]), errors="coerce")


def prepare_agg_frame(df: pd.DataFrame) -> pd.DataFrame:
    """成績表（文字列）→ DB に入れる値。数の列を数に、5つの率の *_val を括弧内の推定値に作り直す。"""
    df = df.copy()
    for c in INT_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").round().astype("Int64")
    for c in FLOAT_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for r in RATE_NAMES:
        if r in df.columns:
            df[f"{r}_val"] = estimate_percent(df[r])
    return df


# ---------------------------------------------------------------------------
# コンパクト化
# ---------------------------------------------------------------------------
def compact_mask(out: pd.DataFrame, mode: str, layer: str = "", min_count: int = COMPACT_MIN_COUNT,
                 min_wedge: float = COMPACT_MIN_WEDGE) -> np.ndarray:
    """成績表 out（aggregate_frame の出力。1つの family・スキーム・オッズ帯 mode）のうち残す行。
    min_count・min_wedge が 0 以下ならその条件は使わない（区分と layer A だけで残す行を決められるように）。"""
    n = len(out)
    if n == 0 or layer in FULL_LAYERS:
        return np.ones(n, dtype=bool)
    num = lambda c: (pd.to_numeric(out[c], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
                     if c in out.columns else np.full(n, np.nan))
    keep = np.zeros(n, dtype=bool)
    with np.errstate(invalid="ignore"):
        if min_count > 0:
            keep |= num("件数") >= min_count
        if min_wedge > 0:
            keep |= (num("WEDGE") >= min_wedge) | (num("adjusted_WEDGE") >= min_wedge)
    rest = ~keep
    if rest.any():                                      # 判定は残りの行だけ（DB と同じ推定値で）
        sub = out.loc[rest, [c for c in ("結合データ", "件数", "複勝率", "連対率", "掲示板率_val", "複勝回収値",
                                         "単勝適正回収値") if c in out.columns]]
        vals = pd.DataFrame({"件数": sub.get("件数"), "掲示板率_val": sub.get("掲示板率_val"),
                             "複勝回収値": sub.get("複勝回収値"), "単勝適正回収値": sub.get("単勝適正回収値")},
                            index=sub.index)
        if "結合データ" in sub.columns:               # 希少条件（G1・重賞・長距離など）は最低件数が下がる
            vals["結合データ"] = sub["結合データ"]
        for r in ("複勝率", "連対率"):
            vals[f"{r}_val"] = estimate_percent(sub[r]) if r in sub.columns else np.nan
        cats = classify_frame(vals, mode)
        idx = np.flatnonzero(rest)
        keep[idx[cats != None]] = True                   # noqa: E711（object 配列の要素ごとの比較）
    return keep


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------
class AggMasterWriter:
    """成績表を1本の .csv.gz に少しずつ書く。close() で一時ファイルを本来の名前に置き換える。
    途中で止まったとき（例外・強制終了）は一時ファイルだけが残り、本来の名前のファイルは前のまま。"""

    def __init__(self, path, compresslevel: int = 5):
        self.path = Path(path)
        self.tmp = self.path.with_name(self.path.name + PART_SUFFIX)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 見出しに時刻・名前を入れない（同じ入力なら同じバイト列。再実行の確認がしやすい）
        self._raw = open(self.tmp, "wb")
        self._f = gzip.GzipFile(filename="", mode="wb", fileobj=self._raw, compresslevel=compresslevel, mtime=0)
        self._columns: list[str] | None = None
        self.rows = 0
        self._warned = False

    def write(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        if self._columns is None:
            self._columns = list(df.columns)
            text = df.to_csv(index=False)
        else:
            text = df[self._columns].to_csv(index=False, header=False)
        try:
            data = text.encode(WRITE_ENCODING)
        except UnicodeEncodeError:
            if not self._warned:
                print(f"[WARN] {self.path.name}: cp932 で書けない文字を削除しました", file=sys.stderr)
                self._warned = True
            data = text.encode(WRITE_ENCODING, errors="ignore")
        self._f.write(data)
        self.rows += len(df)

    def close(self) -> bool:
        """書いた行が1行以上なら本来の名前に置き換えて True。0行なら一時ファイルも本来のファイルも消して False。
        置き換える前に fsync する（Docker の VM ごと落ちても、本来の名前のファイルが途中で切れていないように）。"""
        self._f.close()
        self._raw.flush()
        os.fsync(self._raw.fileno())
        self._raw.close()
        if self.rows == 0:
            self.tmp.unlink(missing_ok=True)
            self.path.unlink(missing_ok=True)
            return False
        os.replace(self.tmp, self.path)
        _fsync_dir(self.path.parent)
        return True

    def abort(self) -> None:
        try:
            self._f.close()
            self._raw.close()
        finally:
            self.tmp.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()
        else:
            self.abort()
        return False


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# 書き終わりの印（--resume 用）
# ---------------------------------------------------------------------------
STAMP_SUFFIX = ".done"


def settings_hash(settings: dict) -> str:
    """集計の設定（agg_settings.json の中身）の短いハッシュ。"""
    import hashlib
    import json
    text = json.dumps(settings, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def stamp_path(agg_path) -> Path:
    p = Path(agg_path)
    return p.with_name(p.name + STAMP_SUFFIX)


def master_fingerprint(master) -> str:
    """master の中身の指紋。.gz は末尾8バイト（展開した中身の CRC32 と長さ。読むのは8バイトだけ）、
    それ以外はファイル全体の sha256。更新時刻は使わない（コピー・Docker のマウントで変わるため）。"""
    import hashlib
    p = Path(master)
    with open(p, "rb") as f:
        if p.name.endswith(".gz"):
            f.seek(0, os.SEEK_END)
            if f.tell() >= 18:
                f.seek(-8, os.SEEK_END)
                return "gz:" + f.read(8).hex()
            f.seek(0)
        return "sha256:" + hashlib.sha256(f.read()).hexdigest()


def master_identity(master) -> dict:
    """印に書く master の識別: 名前・大きさ・中身の指紋（と、確認用に読んだ場所・更新時刻。比べるときは使わない）。"""
    p = Path(master)
    st = p.stat()
    return {"master": p.name, "master_size": st.st_size, "master_fingerprint": master_fingerprint(p),
            "master_path": str(p.resolve()), "master_mtime_ns": st.st_mtime_ns}


IDENTITY_KEYS = ("master", "master_size", "master_fingerprint")      # 同じ master かを比べる項目


def write_stamp(agg_path, settings_id: str, master, rows: int) -> None:
    """agg_master_*.csv.gz を書き終えた印（設定のハッシュ・元の master・行数・ファイルの大きさ）。
    成績表を本来の名前に置き換えて fsync したあとに書く（印があれば、成績表は最後まで書けている）。
    master は master のパスか、読む前に取った master_identity() の dict（集計の間に変わっても読んだときの値を残す）。"""
    import json
    agg_path = Path(agg_path)
    ident = master if isinstance(master, dict) else master_identity(master)
    from .odds import ODDS_BREAKS
    data = {"settings": settings_id, **ident, "rows": int(rows),
            "bytes": agg_path.stat().st_size if agg_path.exists() else 0,
            "odds_breaks": list(ODDS_BREAKS)}     # この成績表を集計したときのオッズ帯の境目（build_db が DB に残し、画面が確かめる）
    sp = stamp_path(agg_path)
    tmp = sp.with_name(sp.name + PART_SUFFIX)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, sp)
    _fsync_dir(sp.parent)


def read_stamp(agg_path) -> dict | None:
    import json
    try:
        return json.loads(stamp_path(agg_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stamp_matches(agg_path, settings_id: str, master) -> bool:
    """--resume で飛ばしてよいか: 印があり、同じ設定・同じ master（名前・大きさ・中身の指紋）から作られ、
    成績表のファイルが印のとおりの大きさで残っている（0行の master はファイルが無いのが正しい）。"""
    st = read_stamp(agg_path)
    if not st or st.get("settings") != settings_id:
        return False
    cur = master_identity(master)
    if any(st.get(k) != cur[k] for k in IDENTITY_KEYS):
        return False
    agg_path = Path(agg_path)
    if int(st.get("rows", -1)) == 0:
        return not agg_path.exists()
    return agg_path.exists() and agg_path.stat().st_size == int(st.get("bytes", -1))


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------
def agg_master_files(agg_dir) -> list[Path]:
    """agg_csv の master ごとの成績表（書きかけの .part は含めない）。"""
    return sorted(p for p in Path(agg_dir).glob(f"{AGG_MASTER_PREFIX}*{AGG_MASTER_SUFFIX}")
                  if parse_agg_master_name(p.name))


def legacy_files(agg_dir) -> list[Path]:
    """旧来の成績表（foragg_*.csv）。"""
    return sorted(Path(agg_dir).glob(f"{FORAGG_PREFIX}*.csv"))


def iter_agg_chunks(path, chunksize: int = 200_000, encoding: str = WRITE_ENCODING):
    """agg_master_*.csv.gz を chunksize 行ずつ（全列を文字列・空欄は ""）。"""
    yield from pd.read_csv(path, compression="gzip", encoding=encoding, dtype=str, keep_default_na=False,
                           chunksize=chunksize)


def read_agg_master(path) -> pd.DataFrame:
    """agg_master_*.csv.gz を丸ごと読む（小さいファイル・テスト・確認用）。"""
    return read_csv_any(path)


def read_agg_tables(agg_dir) -> dict[str, pd.DataFrame]:
    """agg_csv の成績表を、旧来の1表1ファイルの形（{旧来のファイル名: 成績表}）に分けて返す（確認・テスト用）。
    同じ master の agg_master_*.csv.gz があれば、その master の旧来のファイルは使わない（build_db と同じ）。"""
    out: dict[str, pd.DataFrame] = {}
    sources = set()
    for p in agg_master_files(agg_dir):
        source = parse_agg_master_name(p.name)
        sources.add(source)
        df = read_agg_master(p)
        if df.empty:
            continue
        cols = [c for c in df.columns if c not in ROW_META]
        for (fam, mode, scheme), g in df.groupby(["family", "odds_mode", "rank_scheme"], sort=False):
            out[agg_filename(fam, mode, scheme, source)] = g[cols].reset_index(drop=True)
    for p in legacy_files(agg_dir):
        info = parse_foragg_stem(file_stem(p.name))
        if info is None or info["source"] in sources:
            continue
        out[p.name] = read_csv_any(p)
    return out


__all__ = ["AggMasterWriter", "COMPACT_MIN_COUNT", "COMPACT_MIN_WEDGE", "FLOAT_COLUMNS", "FULL_LAYERS",
           "INT_COLUMNS", "RATE_NAMES", "READ_ENCODINGS", "ROW_META", "agg_master_files", "compact_mask",
           "estimate_percent", "iter_agg_chunks", "legacy_files", "prepare_agg_frame", "read_agg_master",
           "read_agg_tables"]
