"""子プロセスを少しずつ作り直す並列実行（replace_csv_ver3.py・aggregate_stats_ver2.py で共通）。

jobs を workers × per_child 件ずつのまとまりに分け、まとまりごとに新しい ProcessPoolExecutor を作り、
1件ずつ渡して（chunksize=1 と同じ）、終わったら閉じて gc.collect() する。子プロセス1つが処理するのは
per_child 件ほどなので、読み込んだ CSV のキャッシュなどがたまって Docker の OOM Killer に止められるのを防ぐ。

ProcessPoolExecutor(max_tasks_per_child=…) は使わない（Python 3.11・3.12 では、子プロセスが規定の件数で
終わったあとに代わりが起動されず、親が止まったままになる。Docker の Python 3.11.18 で再現）。

子プロセスが止められた（BrokenProcessPool）ときは、そのまとまりの終わっていないジョブを
"[ERROR] {label}: {killed_message}" にして次のまとまりへ進む。
"""
from __future__ import annotations

import gc
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from typing import Callable, Sequence

KILLED_MESSAGE = ("子プロセスが止められました（メモリ不足の可能性。--workers を減らすか、"
                  "Docker のメモリの割り当てを増やしてやり直してください）")


def run_batched(func: Callable, jobs: Sequence, workers: int, per_child: int, label: Callable[[object], str],
                executor=ProcessPoolExecutor, killed_message: str = KILLED_MESSAGE,
                on_result: Callable[[int, str], None] | None = None) -> list[str]:
    """func(job) を並列に実行して、jobs と同じ並びの結果（文字列）を返す。
    func は子プロセスで実行されるので、モジュールの直下の関数にする（pickle で渡せるように）。
    on_result(i, result) は結果が出るたびに親プロセスで呼ばれる（進み具合の表示用）。"""
    results: list[str | None] = [None] * len(jobs)
    step = max(1, workers * max(1, per_child))
    for start in range(0, len(jobs), step):
        idx = list(range(start, min(start + step, len(jobs))))
        try:
            with executor(max_workers=workers) as ex:
                futs = {ex.submit(func, jobs[i]): i for i in idx}
                for f in as_completed(futs):
                    i = futs[f]
                    try:
                        results[i] = f.result()
                    except BrokenProcessPool:
                        continue                    # 下でまとめて [ERROR] にする
                    if on_result is not None:
                        on_result(i, results[i])
        except BrokenProcessPool:
            pass
        for i in idx:
            if results[i] is None:
                results[i] = f"[ERROR] {label(jobs[i])}: {killed_message}"
                if on_result is not None:
                    on_result(i, results[i])
        gc.collect()
    return [r if r is not None else "" for r in results]
