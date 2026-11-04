# shisu_replace 実行環境
#   docker build -t csv_processor_shisu .
# Docker Hub が使えない環境では: --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.11.18-slim-bookworm
ARG PYTHON_IMAGE=python:3.11.18-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV LANG=ja_JP.UTF-8 \
    LC_ALL=ja_JP.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 日本語ロケール + sqlite3 CLI（DB の確認用）
RUN apt-get update && apt-get install -y --no-install-recommends locales sqlite3 \
 && sed -i '/ja_JP.UTF-8/s/^# //' /etc/locale.gen && locale-gen \
 && rm -rf /var/lib/apt/lists/*

# ライブラリは版を固定（pandas の版で欠損値の扱いが変わるため）。pytest も入れてコンテナ内でテストできるようにする
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# コード一式（データ・DB は .dockerignore で除外。実行時は -v "$(pwd)":/app でホストのディレクトリをマウントする）
COPY . .
RUN mkdir -p csv_files replace_csv foragg_csv agg_csv master_csv entries_in entries_out

EXPOSE 8000
CMD ["python", "app.py"]
