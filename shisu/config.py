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


def foragg_dir() -> Path:       # foragg_*（集計素材、1行=1頭）
    return base_dir() / "foragg_csv"


def agg_dir() -> Path:          # 集計結果（1行=1結合データ）。build_db の入力
    return base_dir() / "agg_csv"


def master_dir() -> Path:       # master_*（正規化済み 1頭1行。WEDGE・組合せ分析用）
    return base_dir() / "master_csv"


def entries_in_dir() -> Path:   # 出馬表入力
    return base_dir() / "entries_in"


def entries_out_dir() -> Path:  # 偏差値付き出馬表
    return base_dir() / "entries_out"


def ingest_in_dir() -> Path:   # 指数取得（/ingest・ingest.py）の入力（ベースCSV・ラップCSV・安井Excel）
    return base_dir() / "ingest_in"


def ingest_out_dir() -> Path:  # 指数取得の出力（実行ごとのフォルダ）
    return base_dir() / "ingest_out"


def db_path() -> Path:
    env = os.environ.get("DB_PATH")
    return Path(env) if env else base_dir() / "racing.db"
