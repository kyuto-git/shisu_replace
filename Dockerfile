FROM python:3.9-slim

WORKDIR /app

# pandasインストールのための依存関係のインストール
RUN apt-get update && apt-get install -y gcc libpq-dev

# 必要なPythonライブラリのインストール
RUN pip install pandas

COPY replace_csv.py /app/

CMD ["python", "replace_csv.py"]
