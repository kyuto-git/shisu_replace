"""shisu — 外部指数の集計・照合システムの共通部品。

過去レース側（replace_csv_ver3.py / aggregate_stats_ver2.py）と出馬表側
（calc_entry_hensachi.py / build_db.py / app.py）が同じ正規化・キー生成・
偏差値計算を使うよう、ここに集約している。どちらか片方だけを直すと
結合データが一致しなくなるので、キーに関わる処理は必ずこのパッケージで変更する。
"""
