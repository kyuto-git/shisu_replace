"""ディレクトリと DB のパス。

既定のルートはこのリポジトリ直下（Docker では /app）。
環境変数 SHISU_BASE_DIR でルートを、DB_PATH で DB ファイルを差し替えられる。
関数で返すのは、テストや CLI で環境変数を後から変えても反映させるため。
"""
import os
from pathlib import Path


def base_dir() -> Path:
    env = os.environ.get("SHISU_BASE_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent


def csv_dir() -> Path:          # 過去レース入力（TARGET 出力）
    return base_dir() / "csv_files"


def processed_dir() -> Path:    # processed_*（結合データ確認用・TARGET 取込用）
    return base_dir() / "replace_csv"


def agg_dir() -> Path:          # 集計結果（1行=1結合データ）。build_db の入力
    return base_dir() / "agg_csv"


def master_dir() -> Path:       # master_*（正規化済み 1頭1行。WEDGE・組合せ分析用）
    return base_dir() / "master_csv"


def entries_in_dir() -> Path:   # 出馬表入力
    return base_dir() / "entries_in"


def entries_out_dir() -> Path:  # 偏差値付き出馬表
    return base_dir() / "entries_out"


def ext_dir() -> Path:          # 外部指数CSV群（★6タブの基本CSVに Python 側で結合する。shisu/ext_indices.py）
    env = os.environ.get("SHISU_EXT_DIR")
    return Path(env) if env else base_dir() / "ext_indices"


def selected_families_path() -> Path:  # screen_families.py の選抜結果。あれば初期セットの family の代わりに使う
    env = os.environ.get("SHISU_FAMILIES_FILE")
    return Path(env) if env else base_dir() / "selected_families.json"


def raw_dir() -> Path:          # 未統合の過去基本CSV（★6タブの追加分）。replace が csv_files/全場_*.csv に統合する
    return base_dir() / "raw_csv"


def ingest_in_dir() -> Path:   # 指数取得（/ingest・ingest.py）の入力（ベースCSV・ラップCSV・安井Excel）
    return base_dir() / "ingest_in"


def ingest_out_dir() -> Path:  # 指数取得の出力（実行ごとのフォルダ）
    return base_dir() / "ingest_out"


def db_path() -> Path:
    env = os.environ.get("DB_PATH")
    return Path(env) if env else base_dir() / "racing.db"


def result_indices_dir() -> Path:
    """結果指数（SP指数・ST指数。レースのあとに確定する値）のファイル置き場。過去3走/5走の平均・最高の8指数の元
    （shisu/past_perf_indices.py）。"""
    env = os.environ.get("RESULT_INDICES_DIR")
    return Path(env) if env else base_dir() / "result_indices"


def race_marks_dir() -> Path:
    """TARGET の外部レース印（メンバーレベル・タイムレベル・ペース判定・クッション値・含水率）の置き場。前走条件の元
    （shisu/race_marks.py）。"""
    env = os.environ.get("RACE_MARKS_DIR")
    return Path(env) if env else base_dir() / "race_marks"


def past_perf_settings_path() -> Path:
    """過去SP・ST指数の想定馬場状態（/ingest の画面で選んだ値）。calc_entry_hensachi が --baba-* の既定として読む。"""
    env = os.environ.get("PAST_PERF_SETTINGS")
    return Path(env) if env else base_dir() / "past_perf_settings.json"


def notes_path() -> Path:
    """危険条件メモ（condition_notes.json）。DB（racing.db）を作り直しても消えないように、DB の外に持つ。"""
    env = os.environ.get("NOTES_PATH")
    return Path(env) if env else base_dir() / "condition_notes.json"


def baseline_dir() -> Path:
    """SP指数・ST指数の3着内基準値（index_baseline_SP.csv・index_baseline_ST.csv）の置き場所（shisu/baselines.py）。
    既定はパッケージの shisu/data/（リポジトリに入れてあるマスタ。replace・python -m shisu.baselines --update が書き換える）。"""
    env = os.environ.get("SHISU_BASELINE_DIR")
    return Path(env) if env else Path(__file__).resolve().parent / "data"
