"""危険条件メモ（条件ヒット馬の行に赤字で出す、人が書いたメモ）の保存。

保存先は JSON 1本（config.notes_path()。既定はプロジェクト直下の condition_notes.json、環境変数 NOTES_PATH で変えられる）。
racing.db の外に持つので、DB を作り直しても消えない。

1つのメモは (indicator, family, rank_scheme, odds_mode, region, key) に付く。region が空のメモは「全地域共通」で、
どの地域の同じ条件にも出る（その地域だけのメモがあれば、そちらを優先する）。

  {"version": 1, "notes": [{"indicator": "SAV", "family": "normal", "rank_scheme": "", "odds_mode": "odds10u",
                            "region": "全場", "key": "東京×ダ×1600×定量×1・2_勝×1",
                            "note": "⚠️馬体重478kg以下は危険", "updated": "2026-11-16T10:00:00"}]}
"""
from __future__ import annotations

import json
import os
import secrets
import stat
import threading
from datetime import datetime
from pathlib import Path

from . import config

FIELDS = ("indicator", "family", "rank_scheme", "odds_mode", "region", "key")
MAX_NOTE = 2000                 # メモの長さの上限（文字）
MAX_FIELD = 500                 # 条件の1項目（指数・パターン・結合データ など）の長さの上限（文字）
VERSION = 1

_LOCK = threading.Lock()
_CACHE: dict = {}               # {"stamp": (path, mtime_ns, size), "notes": {ident: {"note", "updated"}}}


class NotesError(Exception):
    """メモのファイルを読めない・書けない。"""


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def ident(indicator, family, rank_scheme, odds_mode, region, key) -> tuple[str, str, str, str, str, str]:
    """メモを引くキー。オッズ帯の空は normal、ほかは前後の空白を除いた文字列。"""
    return (_text(indicator), _text(family), _text(rank_scheme), _text(odds_mode) or "normal", _text(region), _text(key))


def clean_note(text) -> str:
    """保存する形（改行をそろえ、前後の空白を除き、長さを MAX_NOTE までにする）。"""
    s = "" if text is None else str(text)
    s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
    return s[:MAX_NOTE]


def _path(path=None) -> Path:
    return Path(path) if path is not None else config.notes_path()


def _read(path: Path) -> dict[tuple, dict]:
    """ファイル → {ident: {"note", "updated"}}。無ければ空。読めない・形が違えば NotesError。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as e:
        raise NotesError(f"{path.name} を読めません（{type(e).__name__}）") from e
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        items = data["notes"] if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("notes が配列ではありません")
        out: dict[tuple, dict] = {}
        for it in items:
            if not isinstance(it, dict):
                raise ValueError("メモの要素が {…} ではありません")
            note = clean_note(it.get("note"))
            if note:
                k = ident(*(it.get(f) for f in FIELDS))
                for s in k + (note,):
                    s.encode("utf-8")                  # 片方だけのサロゲート（"\ud83d" など）は画面に出せず書き直せない → 形の違いとして断る
                out[k] = {"note": note, "updated": _text(it.get("updated"))}
        return out
    except (ValueError, KeyError, TypeError) as e:
        raise NotesError(f"{path.name} の形を読めません（{e}）。直すか、別の名前に変えてください") from e


def load(path=None) -> dict[tuple, dict]:
    """全部のメモ（{ident: {"note", "updated"}}）。ファイルが変わったときだけ読み直す。読めないときは NotesError。"""
    p = _path(path)
    try:
        st = p.stat()
        stamp = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (str(p), None, None)
    with _LOCK:
        if _CACHE.get("stamp") == stamp:
            return _CACHE["notes"]
        notes = _read(p)
        _CACHE.update(stamp=stamp, notes=notes)
        return notes


def load_safe(path=None) -> tuple[dict[tuple, dict], str]:
    """(メモ, エラーの文)。読めないときは空のメモとエラーの文を返す（画面はメモなしで出す）。"""
    try:
        return load(path), ""
    except NotesError as e:
        return {}, str(e)


def lookup(notes: dict, indicator, family, rank_scheme, odds_mode, region, key) -> tuple[str, str] | None:
    """この条件に出すメモ → (本文, 'region' か 'common')。その地域のメモを優先し、無ければ全地域共通（region が空）のメモ。"""
    k = ident(indicator, family, rank_scheme, odds_mode, region, key)
    hit = notes.get(k)
    if hit is not None and k[4]:
        return hit["note"], "region"
    common = notes.get(k[:4] + ("",) + k[5:])
    if common is not None:
        return common["note"], "common"
    return None


def _write(path: Path, notes: dict[tuple, dict]) -> None:
    items = [{**dict(zip(FIELDS, k)), "note": v["note"], "updated": v.get("updated", "")} for k, v in sorted(notes.items())]
    text = json.dumps({"version": VERSION, "notes": items}, ensure_ascii=False, indent=1) + "\n"
    try:
        data = text.encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path.parent / f"{path.name}.{secrets.token_hex(6)}.tmp")
        # 一時ファイルは umask どおりの権限で作る（mkstemp は必ず 0600 になり、Docker（root）で書いたメモをホスト側で読めなくなる）
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))   # 前のファイルがあれば、その権限のまま
            except FileNotFoundError:
                pass
            os.replace(tmp, path)                      # 途中で止まっても、前のファイルか新しいファイルのどちらかが残る
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except (OSError, UnicodeError) as e:
        raise NotesError(f"{path.name} に書けません（{type(e).__name__}: {e}）") from e


def save(indicator, family, rank_scheme, odds_mode, region, key, text, path=None, now: str | None = None) -> str:
    """メモを保存する（本文が空なら消す）。戻り値: 'saved' / 'deleted' / 'none'（消すものが無かった）。
    region を空にすると全地域共通のメモ。ファイルが読めない形のときは上書きせずに NotesError（人が書いたメモを壊さない）。"""
    p = _path(path)
    k = ident(indicator, family, rank_scheme, odds_mode, region, key)
    if not (k[0] and k[1] and k[5]):
        raise NotesError("指数・パターン・結合データのどれかが空です")
    if max(map(len, k)) > MAX_FIELD:
        raise NotesError(f"条件の文字が長すぎます（1項目 {MAX_FIELD} 文字まで）")
    note = clean_note(text)
    with _LOCK:
        notes = dict(_read(p))
        if note:
            notes[k] = {"note": note, "updated": now or datetime.now().isoformat(timespec="seconds")}
            result = "saved"
        elif k in notes:
            del notes[k]
            result = "deleted"
        else:
            return "none"
        _write(p, notes)
        _CACHE.clear()
    return result


def delete(indicator, family, rank_scheme, odds_mode, region, key, path=None) -> str:
    return save(indicator, family, rank_scheme, odds_mode, region, key, "", path)
