# Alpaca 自律型株式トレーディングシステム 技術仕様書

## 1. プロジェクト概要
Alpaca Trading API (`alpaca-py`) を活用し、米国株式市場（Paper Trading環境推奨）において自律的に売買を行うトレーディングシステムを構築します。
本システムは、拡張可能な戦略インターフェース、厳格なリスク管理、および透明性の高いログ記録機能を備えた、コード生成AI（Cursor/Claude）による実装を前提とした詳細な設計図です。

---

## 2. 技術スタックと環境構築

### 2.1 技術スタック
- **Language**: Python 3.10+
- **Core Library**: `alpaca-py` (v0.30.0以降の最新インターフェース)
- **Data Analysis**: `pandas`, `pandas-ta` (テクニカル指標算出)
- **Utilities**: `python-dotenv` (環境変数管理), `loguru` (ロギング)

---

## 3. クラス構成

### `ClientWrapper` (src/client.py)
認証とAPIクライアントを一括管理。
- **Properties**: `trading_client`, `data_client` (StockHistoricalDataClient)

### `DataProvider` (src/data_provider.py)
最新Barデータの取得とDataFrame化。

### `RiskManager` (src/risk_manager.py)
- **calculate_size**: 資金の2%を最大損切額とした発注数量の算出。
- **get_bracket_params**: 利確・損切の動的な価格設定。

---

## 4. 実行フロー (Main Loop)
1. 初期化 (.env読込) -> 2. 市場監視 (最新Bar) -> 3. シグナル判定 -> 4. 数量計算 -> 5. ブラケット注文執行 -> 6. 待機
  
  
