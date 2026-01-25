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
- **Utilities**: `python-dotenv` (環境変数), `loguru` (ロギング)

---

---

## 3. クラス構成

### `ClientWrapper` (src/client.py)
`alpaca-py` の各種クライアントを一括管理。
- **Properties**: `trading_client`, `data_client` (StockHistoricalDataClient)

### `DataProvider` (src/data_provider.py)
市場データの取得・DataFrame化。
- **Method**: `get_historical_bars(symbol, timeframe, limit)`

### `StrategyBase` (src/strategies/base.py)
戦略の抽象基底クラス。
- **Abstract Method**: `check_signal(df: pd.DataFrame) -> Signal`

### `RiskManager` (src/risk_manager.py)
- **Method**: `calculate_size(equity, price)` (資金の2%リスク制限)
- **Method**: `get_bracket_params(entry_price)` (利確・損切設定)

---

## 4. 実行フロー (Main Loop)

1. **初期化**: `.env` を読み込み、APIクライアントを生成。
2. **監視**: 各銘柄の最新Barを取得し、シグナル判定。
3. **執行**: シグナル発生時、`RiskManager` で算出された数量でブラケット注文を送信。
4. **モニタリング**: `logs/trading.log` および `data/history.csv` へ記録。
5. **待機**: 次の判定タイミングまでスリープ。
