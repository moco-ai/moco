# Alpaca 自律型株式トレーディングシステム 技術仕様書

## 1. プロジェクト概要
Alpaca Trading API (`alpaca-py`) を活用し、米国株式市場（Paper Trading環境推奨）において自律的に売買を行うトレーディングシステムを構築します。
本システムは、拡張可能な戦略インターフェース、厳格なリスク管理、および詳細なログ記録機能を備えた詳細設計図です。

---

## 2. 技術スタックと環境構築

### 2.1 技術スタック
- **Language**: Python 3.10+
- **Core Library**: `alpaca-py` (最新の型安全リクエストモデル使用)
- **Data Analysis**: `pandas`, `pandas-ta`
- **Utilities**: `python-dotenv`, `loguru`

### 2.2 ディレクトリ構造
- `src/`: client.py, data_provider.py, risk_manager.py, executor.py
- `src/strategies/`: base.py, golden_cross.py, rsi_strategy.py
- `main.py`, `.env`, `requirements.txt`

---

## 3. クラス構成

### `ClientWrapper`
認証とAPIクライアント管理。

### `RiskManager`
資金の2%リスク制限に基づく数量算定、ブラケット注文価格の決定。

---

## 4. 実行フロー
1. 初期化 -> 2. データ取得 -> 3. シグナル判定 -> 4. ブラケット注文の執行 -> 5. 待機
