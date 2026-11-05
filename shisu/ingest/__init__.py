"""外部指数の取得をまとめて実行する（旧: 別々の Docker コンテナだった6システム＋安井Excel）。

  omega     ① Ω指数（競馬ラボ）            ベースCSV → omega/{YYYYMMDD}_omega.csv
  kichiuma  ② 吉馬6指数                    ベースCSV → kichiuma/kichiuma_{key}/{YYYYMMDD}_kichiuma_{key}.csv
  kayochin  ③ かよちん指数                 ベースCSV → kayochin/{YYYYMMDD}_kayochin.csv
  keibaai   ④ keibaAI指数（開始〜終了ID）  ベースCSV → keibaAI/{YYYYMMDD}_keibaAI.csv
  ittai     ⑤ 一隊（結果分析スピード指数） ベースCSV → ittai/{YYYYMMDD}_ittai.csv
  lap       ⑥ ラップ6指数（手元で計算）    ラップCSV → lap/{2F,1F,4F,33rap,rabbit_2F,rabbit_1F,pace}/...
  yasui     安井サロン Excel               Excel（＋ベースCSV）→ yasui/...

画面（app.py の /ingest）と CLI（ingest.py）はどちらも run_ingest() を呼ぶ。
1つの取得元で失敗しても、残りの取得元は続ける（中止ボタンのときだけ全体を止める）。
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .common import Cancelled, Fetcher, Logger, Outputs, default_log, read_base

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
    SourceInfo("ittai", "⑤ 一隊（結果分析スピード指数）", "ittai", True, 2.0),
    SourceInfo("lap", "⑥ ラップ6指数（計算のみ）", "lap", False, needs="lap"),
    SourceInfo("yasui", "安井サロン Excel", "yasui", False, needs="yasui"),
)}
ORDER = tuple(SOURCES)


@dataclass
class IngestRequest:
    sources: list[str]
    base_paths: list[Path] = field(default_factory=list)
    lap_paths: list[Path] = field(default_factory=list)
    yasui_paths: list[Path] = field(default_factory=list)
    keibaai_start: int | None = None
    keibaai_end: int | None = None
    intervals: dict[str, float] = field(default_factory=dict)
    yasui_entries_dir: Path | None = None     # 安井の出馬表用CSVを entries_in/ にも置くとき

    def interval_for(self, name: str) -> float:
        v = self.intervals.get(name, SOURCES[name].default_interval)
        return max(MIN_INTERVAL, float(v))


def validate(req: IngestRequest) -> list[str]:
    errors = []
    unknown = [s for s in req.sources if s not in SOURCES]
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
    from . import ittai, kayochin, keibaai, kichiuma, lap, omega
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
            elif name == "ittai":
                res = ittai.run(base, fetcher, dest, log)
            elif name == "lap":
                res = lap.run(req.lap_paths, dest, log)
            else:
                res = yasui_extractor.run(req.yasui_paths, base, dest, log, entries_dir=req.yasui_entries_dir)
            results[name] = res
            log(f"[DONE] {info.label}: {len(res.files)} ファイル")
        except Cancelled:
            log("[INFO] 中止しました")
            results[name] = "CANCELLED"
            break
        except Exception as e:  # 1つの取得元の失敗で全体を止めない
            log(f"[ERROR] {info.label}: {type(e).__name__}: {e}")
            log(traceback.format_exc(limit=3).rstrip())
            results[name] = f"ERROR: {type(e).__name__}: {e}"
    return results
