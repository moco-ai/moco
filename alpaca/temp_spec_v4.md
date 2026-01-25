# Alpaca 自律型株式トレーディングシステム 技術仕様書

## 1. プロジェクト概要
Alpaca Trading API (`alpaca-py`) を活用し、米国株式市場（Paper Trading環境推奨）において自律的に売買を行うトレーディングシステムを構築します。
本システムは、拡張可能な戦略インターフェース、厳格なリスク管理、および透明性の高いログ記録機能を備えた、コード生成AI（Cursor/Claude）による実装を前提とした詳細な設計図です。

---

## 2. 技術スタックと環境構築

### 2.1 技術スタック
- **Language**: Python 3.10+
- **Core Library**: `alpaca-py` (最新インターフェース)
- **Data Analysis**: `pandas`, `pandas-ta` (テクニカル指標算出)
- **Utilities**: `python-dotenv` (環境変数), `loguru` (ロギング)

---

## 3. クラス構成

### `ClientWrapper` (src/client.py)
認証とAPIクライアント (`TradingClient`, `StockHistoricalDataClient`) を保持。

### `DataProvider` (src/data_provider.py)
最新Barデータの取得とDataFrame化。

### `RiskManager` (src/risk_manager.py)
資金の2%を最大リスクとした数量算出、ブラケット注文価格の決定。

### `StrategyBase` (src/strategies/base.py)
シグナル判定の基底クラス。

---

## 4. 実行フロー (Main Loop)
1. 初期化 -> 2. 最新Bar取得 -> 3. シグナル判定 -> 4. 数量・注文構成算出 -> 5. 執行 -> 6. 待機
