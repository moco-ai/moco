FROM python:3.11-slim

# システム依存関係
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# 非rootユーザーを作成
RUN useradd -m -s /bin/bash moco

# 作業ディレクトリ
WORKDIR /app

# 依存関係をまずコピー（キャッシュ最適化）
COPY pyproject.toml README.md ./

# パッケージをインストール（編集可能モード）
RUN pip install --no-cache-dir -e .

# ソースをコピー
COPY src/ ./src/

# プロファイルディレクトリを作成
RUN mkdir -p /app/profiles /app/data /home/moco/workspace && \
    chown -R moco:moco /app /home/moco

# 環境変数
ENV PYTHONUNBUFFERED=1
ENV MOCO_HOME=/app
ENV MOCO_DATA_DIR=/app/data

# 非rootユーザーに切り替え
USER moco

# ポート
EXPOSE 8000

# デフォルトコマンド
CMD ["moco", "ui", "--host", "0.0.0.0"]
