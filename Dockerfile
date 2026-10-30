# Dockerfile
FROM python:3.11-slim

ENV LANG=ja_JP.UTF-8 \
    LC_ALL=ja_JP.UTF-8 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# OS最小セット + ロケール + sqlite3 CLI（必要なら）
RUN apt-get update && apt-get install -y --no-install-recommends locales sqlite3 \
 && sed -i '/ja_JP.UTF-8/s/^# //' /etc/locale.gen && locale-gen \
 && rm -rf /var/lib/apt/lists/*

# Pythonライブラリ（まとめて）
# pandas / numpy はCSV処理、flask はWeb
RUN pip install --no-cache-dir pandas numpy flask

# プロジェクト一式コピー（順序はここでOK）
COPY . .

# 作業ディレクトリ（マウント前提の空ディレクトリ群）
RUN mkdir -p /app/csv_files /app/replace_csv /app/foragg_csv

# ポートを開ける（Flask）
EXPOSE 8000

# デフォルトはWebを起動
CMD ["python", "app.py"]


# # ベースは軽量な Python
# FROM python:3.11-slim

# ENV LANG=ja_JP.UTF-8
# ENV LC_ALL=ja_JP.UTF-8
# ENV PYTHONUNBUFFERED=1

# WORKDIR /app


# # OSレベルの最小限（日本語CSV/Shift_JISに特別な追加は不要）
# RUN apt-get update && apt-get install -y --no-install-recommends locales \
#  && sed -i '/ja_JP.UTF-8/s/^# //' /etc/locale.gen && locale-gen \
#  && rm -rf /var/lib/apt/lists/*


# # sqlite3 は Python に標準で含まれるので追加インストール不要
# # ただしCLIコマンドも使うなら apt 経由で入れる
# RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/*

# # ロケールは任意（ログなどで日本語が化ける場合に有効）
# RUN sed -i '/ja_JP.UTF-8/s/^# //' /etc/locale.gen && locale-gen

# # 依存ライブラリ（scipy を追加）
# RUN pip install --no-cache-dir pandas numpy scipy flask

# # スクリプトをコピー
# COPY replace_csv.py /app/replace_csv.py
# COPY aggregate_stats.py /app/aggregate_stats.py

# # 入出力用ディレクトリ（マウントでもOK）
# RUN mkdir -p /app/csv_files /app/replace_csv /app/foragg_csv



# # デフォルトは置換を実行
# ENTRYPOINT ["python", "/app/replace_csv_ver3.py"]

# COPY . .

# CMD ["python", "app.py"]