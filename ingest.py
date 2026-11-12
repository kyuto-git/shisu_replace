# -*- coding: utf-8 -*-
"""外部指数の取得（スクレイピング・ラップ計算・安井Excel）を CLI でまとめて実行する。画面版は app.py の /ingest。

  python ingest.py --sources omega,kichiuma,kayochin                # ベースCSV は ingest_in/base/*.csv
  python ingest.py --sources keibaai --keibaai-start 18952 --keibaai-end 18987
  python ingest.py --sources lap                                     # ラップCSV は ingest_in/lap/*.csv
  python ingest.py --sources yasui                                   # 安井 Excel は ingest_in/yasui/*.xlsx
  python ingest.py --sources omega --base my_base.csv --interval 3

取得元: omega ① Ω指数 / kichiuma ② 吉馬6指数 / kayochin ③ かよちん / keibaai ④ keibaAI /
        lap ⑥ ラップ6指数 / yasui 安井サロン Excel（⑤ 一隊 ittai は廃止）
出力: ingest_out/{実行日時}/{取得元}/...（TARGET 取込用の日付別2列CSV と マージ完全版。詳細は shisu/ingest/）
      TARGET 取込用CSV（Ω・吉馬・かよちん・keibaAI・安井）は ext_indices/ にも置く（--no-ext-copy で置かない）
アクセス間隔の既定: Ω・かよちん 3秒、ほかは2秒（1秒未満は1秒にする）。
選んだ取得元は同時に実行する（取得元ごとに1スレッド。①〜④は別々のサイトなので、サイトごとのアクセス間隔はそのまま守る）。
ログの各行に取得元の名前が付く。--sequential で前と同じ1つずつの実行。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

from shisu import config
from shisu.ingest import ORDER, SOURCES, IngestRequest, run_ingest, validate


def _files(paths: list[str] | None, default_dir: Path, patterns: tuple[str, ...]) -> list[Path]:
    if paths:
        return [Path(p) for p in paths]
    out: list[Path] = []
    for pat in patterns:
        for p in sorted(default_dir.glob(pat)):
            try:
                p.name.encode("utf-8")
            except UnicodeEncodeError:      # Windows の zip を展開した cp932 の名前など
                print(f"[WARN] ファイル名を UTF-8 で読めないので使いません（名前を付け直してください）: "
                      f"{os.fsencode(p.name).decode('utf-8', 'replace')}", file=sys.stderr)
                continue
            out.append(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", required=True, help=f"カンマ区切り: {','.join(ORDER)}（all で全部）")
    ap.add_argument("--base", nargs="*", help="ベースCSV（既定: ingest_in/base/*.csv）")
    ap.add_argument("--lap", nargs="*", help="ラップCSV（既定: ingest_in/lap/*.csv）")
    ap.add_argument("--yasui", nargs="*", help="安井 Excel（既定: ingest_in/yasui/*.xlsx）")
    ap.add_argument("--keibaai-start", type=int)
    ap.add_argument("--keibaai-end", type=int)
    ap.add_argument("--interval", type=float, help="スクレイピングのアクセス間隔（秒。全取得元に同じ値。既定は取得元ごと）")
    ap.add_argument("--sequential", action="store_true",
                    help="取得元を1つずつ実行する（既定は同時に実行。ログを取得元ごとにまとめて読みたいとき用）")
    ap.add_argument("--no-ext-copy", action="store_true",
                    help="TARGET 取込用CSVを ext_indices/ に置かない（既定は置く。過去レース・出馬表に自動で結び付く）")
    ap.add_argument("--out", default=str(config.ingest_out_dir()), help="出力のルート（既定 ingest_out）")
    ap.add_argument("--run-id", default=None, help="出力フォルダ名（既定: 実行日時 YYYYMMDD_HHMMSS）")
    args = ap.parse_args(argv)

    names = list(ORDER) if args.sources.strip() == "all" else [s.strip() for s in args.sources.split(",") if s.strip()]
    inp = config.ingest_in_dir()
    req = IngestRequest(
        sources=names,
        base_paths=_files(args.base, inp / "base", ("*.csv",)),
        lap_paths=_files(args.lap, inp / "lap", ("*.csv",)),
        yasui_paths=_files(args.yasui, inp / "yasui", ("*.xlsx", "*.xlsm")),
        keibaai_start=args.keibaai_start, keibaai_end=args.keibaai_end,
        intervals={n: args.interval for n in names if n in SOURCES and SOURCES[n].scrape}
        if args.interval is not None else {},
        ext_dir=None if args.no_ext_copy else config.ext_dir(),
        parallel=not args.sequential,
    )
    errors = validate(req)
    if errors:
        for e in errors:
            print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    run_id = args.run_id or _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) / run_id
    results = run_ingest(req, out)
    print(f"[INFO] 出力先: {out}")
    bad = {k: v for k, v in results.items() if isinstance(v, str)}
    for k, v in bad.items():
        print(f"[ERROR] {SOURCES[k].label}: {v}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
