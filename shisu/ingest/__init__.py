"""外部指数の取得をまとめて実行する（旧: 別々の Docker コンテナだったシステム＋安井Excel）。

  omega     ① Ω指数（競馬ラボ）            ベースCSV → omega/{YYYYMMDD}_omega.csv
  kichiuma  ② 吉馬6指数                    ベースCSV → kichiuma/kichiuma_{key}/{YYYYMMDD}_kichiuma_{key}.csv
  kayochin  ③ かよちん指数                 ベースCSV → kayochin/{YYYYMMDD}_kayochin.csv
  keibaai   ④ keibaAI指数（開始〜終了ID）  ベースCSV → keibaAI/{YYYYMMDD}_keibaAI.csv
  lap       ⑥ ラップ6指数（手元で計算）    ラップCSV → lap/{2F,1F,4F,33rap,rabbit_2F,rabbit_1F,pace}/...
  yasui     安井サロン Excel               Excel（＋ベースCSV）→ yasui/yasui_{key}/{YYYYMMDD}_yasui_{key}.csv
（⑤ 一隊 ittai はサイト側の仕様変更で取得できなくなったため廃止。番号は旧来のまま ⑥ ラップを残す）

画面（app.py の /ingest）と CLI（ingest.py）はどちらも run_ingest() を呼ぶ。
1つの取得元で失敗しても、残りの取得元は続ける（中止ボタンのときだけ全体を止める）。
"""
from __future__ import annotations

import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from .. import config
from ..csvio import read_csv_any, write_csv
from .common import RID as RID_HEADER, WORK, Cancelled, Fetcher, Logger, Outputs, default_log, read_base

MIN_INTERVAL = 1.0      # スクレイピングのアクセス間隔の下限（秒）


@dataclass(frozen=True)
class SourceInfo:
    name: str           # 識別子（画面のチェックボックス・CLI の --sources）
    label: str
    folder: str         # 出力フォルダ名
    scrape: bool        # ネットワークに取りに行く
    default_interval: float = 0.0
    needs: str = "base"  # base / lap / yasui


SOURCES: dict[str, SourceInfo] = {s.name: s for s in (
    SourceInfo("omega", "① Ω指数（競馬ラボ）", "omega", True, 3.0),
    SourceInfo("kichiuma", "② 吉馬6指数", "kichiuma", True, 2.0),
    SourceInfo("kayochin", "③ かよちん指数", "kayochin", True, 3.0),
    SourceInfo("keibaai", "④ keibaAI指数（連番ID）", "keibaAI", True, 2.0),
    SourceInfo("lap", "⑥ ラップ6指数（計算のみ）", "lap", False, needs="lap"),
    SourceInfo("yasui", "安井サロン Excel", "yasui", False, needs="yasui"),
)}
ORDER = tuple(SOURCES)
# 廃止した取得元（--sources に書かれたら理由を添えて断る）
DISCONTINUED = {"ittai": "⑤ 一隊（結果分析スピード指数）はサイト側の仕様変更で取得できなくなったため廃止しました"}


# ext_indices/ の置き場所（取得元 → ext_indices の下のフォルダ。shisu.ext_indices が指数を見分けられる名前）
EXT_FOLDERS = {"omega": "OMEGA", "kichiuma": "kichiuma", "kayochin": "kayochin", "keibaai": "keibaAI", "yasui": "yasui"}
_TARGET_FILE = re.compile(r"^\d{8}_.+\.csv$", re.IGNORECASE)


def _read_target_csv(path: Path) -> pd.DataFrame | None:
    """TARGET 取込用CSV（レースID(新), ワーク1）→ 文字列の2列。形が違えば None。"""
    try:
        df = read_csv_any(path)
    except Exception:
        return None
    if RID_HEADER not in df.columns or WORK not in df.columns:
        return None
    df = df[[RID_HEADER, WORK]].astype(object).where(df[[RID_HEADER, WORK]].notna(), "")
    return df.apply(lambda s: s.map(lambda v: str(v).strip()))


def copy_to_ext(files: list[Path], src_root: Path, ext_root: Path, log: Logger | None = None) -> list[Path]:
    """TARGET 取込用の日付別CSV（{YYYYMMDD}_{名前}.csv。レースID(新), ワーク1 の2列）を ext_indices/ に置く。
    マージ完全版（*_merged.csv）・照合できなかった馬（*_noid.csv）・ベースCSVの無い日の 日付・場所… の形は置かない。
    フォルダの形は取得元の出力と同じ（kichiuma/kichiuma_{key}/、yasui/yasui_{key}/）。

    同じ名前のファイルがすでにあれば、丸ごと上書きせず レースID(新) ごとに足し合わせる: 今回値の取れた馬だけ
    新しい値にし、取れなかった馬（空欄）は前の値を残す（取得できなかった日・別の場所や範囲で取り直したときに、
    前に取った値を消さない）。新しいファイルの値が全部空欄なら、新しく作らない。"""
    log = log or (lambda m: None)
    out = []
    for f in files:
        f = Path(f)
        name = f.name.lower()
        if not _TARGET_FILE.match(f.name) or name.endswith("_merged.csv") or name.endswith("_noid.csv"):
            continue
        new = _read_target_csv(f)
        if new is None:                  # 日付, 場所, … の形（ベースCSVの無い日）は TARGET に取り込めない
            continue
        try:
            rel = f.relative_to(src_root)
        except ValueError:
            rel = Path(f.name)
        dst = Path(ext_root) / rel
        has = new[WORK] != ""
        if dst.exists():
            old = _read_target_csv(dst)
            if old is None:
                log(f"[WARN] {dst} は レースID(新), ワーク1 の形ではないので上書きしません（今回の {f.name} は置きません）")
                continue
            vals = dict(zip(old[RID_HEADER], old[WORK]))
            order = list(dict.fromkeys(old[RID_HEADER]))
            for rid, v, h in zip(new[RID_HEADER], new[WORK], has):
                if rid not in vals:
                    order.append(rid)
                    vals[rid] = v
                elif h:
                    vals[rid] = v
            merged = pd.DataFrame({RID_HEADER: order, WORK: [vals[r] for r in order]})
            kept = int(((old[WORK] != "") & ~old[RID_HEADER].isin(new.loc[has, RID_HEADER])).sum())
            if kept:
                log(f"[INFO] {dst.name}: 今回値の取れなかった {kept} 頭は前の値を残しました")
        else:
            if not has.any():
                log(f"[INFO] {f.name}: 値が1頭も無いので ext_indices には置きません")
                continue
            merged = new
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.tmp")
        write_csv(merged, tmp)
        tmp.replace(dst)
        out.append(dst)
    return out


@dataclass
class IngestRequest:
    sources: list[str]
    base_paths: list[Path] = field(default_factory=list)
    lap_paths: list[Path] = field(default_factory=list)
    yasui_paths: list[Path] = field(default_factory=list)
    keibaai_start: int | None = None
    keibaai_end: int | None = None
    intervals: dict[str, float] = field(default_factory=dict)
    ext_dir: Path | None = None               # TARGET 取込用CSVを ext_indices/ にも置く（None なら置かない）

    def interval_for(self, name: str) -> float:
        v = self.intervals.get(name, SOURCES[name].default_interval)
        return max(MIN_INTERVAL, float(v))


def validate(req: IngestRequest) -> list[str]:
    errors = []
    for s in req.sources:
        if s in DISCONTINUED:
            errors.append(DISCONTINUED[s])
    unknown = [s for s in req.sources if s not in SOURCES and s not in DISCONTINUED]
    if unknown:
        errors.append(f"知らない取得元: {unknown}（使えるのは {list(ORDER)}）")
    if not req.sources:
        errors.append("取得元を1つ以上選んでください")
    if any(SOURCES[s].needs == "base" for s in req.sources if s in SOURCES) and not req.base_paths:
        errors.append("ベースCSV（レースID(新), 日付S, 枠番, 馬番, ワーク1）を指定してください")
    if "lap" in req.sources and not req.lap_paths:
        errors.append("ラップCSV（ワーク1 にレースのラップ）を指定してください")
    if "yasui" in req.sources and not req.yasui_paths:
        errors.append("安井サロンの Excel（ステータス新バージョンYYYY_M月.xlsx）を指定してください")
    if "keibaai" in req.sources:
        s, e = req.keibaai_start, req.keibaai_end
        if s is None or e is None:
            errors.append("keibaAI の開始ID・終了IDを入れてください")
        elif int(e) < int(s):
            errors.append(f"keibaAI の終了ID（{e}）が開始ID（{s}）より小さい")
        elif int(e) - int(s) + 1 > 20000:
            errors.append("keibaAI のIDの範囲が広すぎます（1回 20000 ページまで）")
    for name, v in req.intervals.items():
        try:
            float(v)
        except (TypeError, ValueError):
            errors.append(f"{name} のアクセス間隔が数ではありません: {v}")
    return errors


def run_ingest(req: IngestRequest, out_dir: Path, log: Logger = default_log, stop=None, session=None,
               sleep: Callable[[float], None] = time.sleep,
               on_source: Callable[[str, int, int], None] | None = None) -> dict[str, Outputs | str]:
    """選んだ取得元を順に実行する。戻り値: 取得元 → Outputs（失敗は 'ERROR: …'、中止は 'CANCELLED'）。"""
    from . import kayochin, keibaai, kichiuma, lap, omega
    from .. import yasui_extractor

    errors = validate(req)
    if errors:
        raise ValueError(" / ".join(errors))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    selected = [s for s in ORDER if s in req.sources]
    base = None
    if req.base_paths and any(SOURCES[s].needs == "base" or s == "yasui" for s in selected):
        base = read_base(req.base_paths, log)
    results: dict[str, Outputs | str] = {}
    for k, name in enumerate(selected, 1):
        info = SOURCES[name]
        if on_source:
            on_source(name, k, len(selected))
        log(f"[INFO] ===== {k}/{len(selected)} {info.label} =====")
        dest = out_dir / info.folder
        try:
            fetcher = None
            if info.scrape:
                interval = req.interval_for(name)
                log(f"[INFO] アクセス間隔 {interval:g} 秒")
                fetcher = Fetcher(interval=interval, session=session, log=log, stop=stop, sleep=sleep)
            if name == "omega":
                res = omega.run(base, fetcher, dest, log)
            elif name == "kichiuma":
                res = kichiuma.run(base, fetcher, dest, log)
            elif name == "kayochin":
                res = kayochin.run(base, fetcher, dest, log)
            elif name == "keibaai":
                res = keibaai.run(base, fetcher, dest, req.keibaai_start, req.keibaai_end, log)
            elif name == "lap":
                res = lap.run(req.lap_paths, dest, log)
            else:
                # ベースCSVの指定が無い開催日は ingest_in/base/ と entries_in/ の CSV から探す
                res = yasui_extractor.run(req.yasui_paths, base, dest, log,
                                          search_dirs=(config.ingest_in_dir() / "base", config.entries_in_dir()),
                                          exclude_paths=req.base_paths)
            results[name] = res
            log(f"[DONE] {info.label}: {len(res.files)} ファイル")
            if req.ext_dir is not None and name in EXT_FOLDERS:
                ext_to = Path(req.ext_dir) / EXT_FOLDERS[name]
                try:
                    copied = copy_to_ext(res.files, dest, ext_to, log)
                except OSError as e:     # 置けなくても取得の結果（ingest_out）は残っているので、失敗扱いにしない
                    log(f"[WARN] {info.label}: ext_indices に置けませんでした（{type(e).__name__}: {e}）。"
                        f"ingest_out の TARGET 取込用CSVを手で {ext_to} に置いてください")
                    copied = []
                res.ext_files = copied
                if copied:
                    log(f"[INFO] {info.label}: TARGET 取込用CSV {len(copied)} 本を {ext_to} に置きました"
                        "（replace・calc_entry_hensachi が自動で結び付けます）")
        except Cancelled:
            log("[INFO] 中止しました")
            results[name] = "CANCELLED"
            break
        except Exception as e:  # 1つの取得元の失敗で全体を止めない
            log(f"[ERROR] {info.label}: {type(e).__name__}: {e}")
            log(traceback.format_exc(limit=3).rstrip())
            results[name] = f"ERROR: {type(e).__name__}: {e}"
    return results
