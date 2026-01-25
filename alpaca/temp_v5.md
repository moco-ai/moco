# Alpaca 自律型株式トレーディングシステム 技術仕様書

## 1. プロジェクト概要
Alpaca Trading API (`alpaca-py`) を活用し、米国株式市場において自律的に売買を行うシステムを構築します。
拡張可能な戦略、厳格なリスク管理、透明なロギングを実現するための詳細な設計図です。

---

## 2. 技術スタック
- **Language**: Python 3.10+
- **Core Library**: `alpaca-py` (最新リクエストモデルベース)
- **Data Analysis**: `pandas`, `pandas-ta`
- **Utilities**: `python-dotenv`, `loguru`

---

## 3. クラス構成

### `ClientWrapper` (src/client.py)
認証管理と各種クライアント (`TradingClient`, `StockHistoricalDataClient`) の統合提供。

### `DataProvider` (src/data_provider.py)
市場データの取得。`StockBarsRequest` を使用。

### `StrategyBase` (src/strategies/base.py)
シグナル判定の基底クラス。

### `RiskManager` (src/risk_manager.py)
- 総資金の2%リスク制限に基づく数量算出。
- ブラケット注文 (利確・損切) の価格決定。

### `Executor` (src/executor.py)
注文執行管理。

---

## 4. 実行フロー
1. 初期化 -> 2. データ取得 -> 3. シグナル判定 -> 4. リスク評価・数量計算 -> 5. ブラケット注文執行 -> 6. 待機
