# カスタムツール作成ガイド

プロファイル固有のツールを Python で作成できます。

---

## ディレクトリ構造

```
profiles/<profile-name>/
└── tools/
    ├── calculator.py     # ツールファイル
    ├── api_client.py
    └── __init__.py       # （任意）
```

---

## 基本的なツール

### シンプルな関数

```python
# profiles/my-profile/tools/calculator.py

def add_numbers(a: int, b: int) -> int:
    """
    2つの数値を加算します。
    
    Args:
        a: 1つ目の数値
        b: 2つ目の数値
    
    Returns:
        加算結果
    """
    return a + b


def multiply(a: float, b: float) -> float:
    """
    2つの数値を乗算します。
    
    Args:
        a: 1つ目の数値
        b: 2つ目の数値
    
    Returns:
        乗算結果
    """
    return a * b
```

### エージェントでの使用

```yaml
# agents/orchestrator.md
---
tools:
  add_numbers: true
  multiply: true
---
```

---

## docstring のルール

**docstring は必須です！** LLM はこれを見てツールの使い方を判断します。

### 推奨フォーマット（Google スタイル）

```python
def search_database(query: str, limit: int = 10) -> str:
    """
    データベースを検索します。
    
    Args:
        query: 検索クエリ（SQL LIKE パターン対応）
        limit: 最大取得件数（デフォルト: 10）
    
    Returns:
        検索結果の JSON 文字列
    
    Examples:
        >>> search_database("user%", limit=5)
        '[{"id": 1, "name": "user1"}, ...]'
    
    Note:
        - 大文字小文字を区別しません
        - ワイルドカードは % を使用
    """
    # 実装
```

### 悪い例

```python
# ❌ docstring がない
def search(q):
    return db.query(q)

# ❌ 説明が不十分
def search(q):
    """検索する"""
    return db.query(q)
```

---

## 型ヒント

型ヒントは**必須**です。LLM が引数の型を正しく理解するために必要です。

### サポートされる型

```python
from typing import List, Dict, Optional, Any

def process_items(
    items: List[str],           # 文字列のリスト
    options: Dict[str, Any],    # 辞書
    count: Optional[int] = None # オプショナル
) -> str:
    """..."""
```

### 型ヒントなしの場合

```python
# ❌ 型ヒントなし → LLM が誤った型を渡す可能性
def process(data):
    return str(data)

# ✅ 型ヒントあり
def process(data: dict) -> str:
    return str(data)
```

---

## 戻り値

### 文字列を返す（推奨）

LLM は文字列を最もよく理解します。

```python
import json

def get_user(user_id: int) -> str:
    """ユーザー情報を取得します。"""
    user = db.get_user(user_id)
    return json.dumps(user, ensure_ascii=False, indent=2)
```

### 複雑なオブジェクトを返す場合

```python
def analyze_code(file_path: str) -> str:
    """
    コードを解析してレポートを返します。
    
    Returns:
        解析結果（Markdown フォーマット）
    """
    # 解析処理
    return f"""
## 解析結果: {file_path}

### 統計
- 行数: {lines}
- 関数数: {functions}
- クラス数: {classes}

### 問題点
{issues}
"""
```

---

## エラーハンドリング

### 例外を投げる

```python
def read_config(path: str) -> str:
    """設定ファイルを読み込みます。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
    
    with open(path) as f:
        return f.read()
```

### エラーメッセージを返す

```python
def safe_read_config(path: str) -> str:
    """設定ファイルを読み込みます（エラー時はメッセージを返す）。"""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: 設定ファイルが見つかりません: {path}"
    except Exception as e:
        return f"Error: {e}"
```

---

## 外部依存

### 依存パッケージの使用

```python
# profiles/my-profile/tools/api_client.py

import httpx  # 外部パッケージ

def fetch_api(endpoint: str) -> str:
    """
    外部 API からデータを取得します。
    
    Args:
        endpoint: API エンドポイント URL
    
    Returns:
        レスポンス JSON
    
    Note:
        httpx パッケージが必要です: pip install httpx
    """
    response = httpx.get(endpoint)
    response.raise_for_status()
    return response.text
```

### 依存関係の記録

プロファイルに `requirements.txt` を追加することを推奨：

```
profiles/my-profile/
├── requirements.txt   # httpx>=0.26.0
├── profile.yaml
├── agents/
└── tools/
```

---

## 高度な例

### 状態を持つツール（クラスベース）

```python
# profiles/my-profile/tools/session_manager.py

class SessionManager:
    """セッション管理クラス"""
    
    def __init__(self):
        self._sessions = {}
    
    def create_session(self, name: str) -> str:
        """新しいセッションを作成します。"""
        import uuid
        session_id = str(uuid.uuid4())[:8]
        self._sessions[session_id] = {"name": name, "data": {}}
        return f"セッション作成完了: {session_id}"
    
    def get_session(self, session_id: str) -> str:
        """セッション情報を取得します。"""
        if session_id not in self._sessions:
            return f"Error: セッション {session_id} が見つかりません"
        return str(self._sessions[session_id])


# グローバルインスタンス（ツールとして公開）
_manager = SessionManager()

# これらの関数がツールとして公開される
def create_session(name: str) -> str:
    """新しいセッションを作成します。"""
    return _manager.create_session(name)

def get_session(session_id: str) -> str:
    """セッション情報を取得します。"""
    return _manager.get_session(session_id)
```

### 非同期ツール

```python
# profiles/my-profile/tools/async_api.py

import asyncio
import httpx

async def fetch_multiple(urls: list) -> str:
    """
    複数の URL から並列でデータを取得します。
    
    Args:
        urls: URL のリスト
    
    Returns:
        取得結果の JSON
    """
    async with httpx.AsyncClient() as client:
        tasks = [client.get(url) for url in urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
    
    results = []
    for url, resp in zip(urls, responses):
        if isinstance(resp, Exception):
            results.append({"url": url, "error": str(resp)})
        else:
            results.append({"url": url, "status": resp.status_code})
    
    return json.dumps(results, indent=2)
```

### 環境変数を使用

```python
# profiles/my-profile/tools/secure_api.py

import os

def call_secure_api(endpoint: str) -> str:
    """
    認証付き API を呼び出します。
    
    Args:
        endpoint: API エンドポイント
    
    Returns:
        API レスポンス
    
    Note:
        環境変数 MY_API_KEY が必要です
    """
    api_key = os.environ.get("MY_API_KEY")
    if not api_key:
        return "Error: MY_API_KEY 環境変数が設定されていません"
    
    import httpx
    response = httpx.get(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}"}
    )
    return response.text
```

---

## デバッグ

### ツールのテスト

```python
# profiles/my-profile/tools/test_calculator.py

from calculator import add_numbers, multiply

def test_add():
    assert add_numbers(1, 2) == 3

def test_multiply():
    assert multiply(3, 4) == 12

if __name__ == "__main__":
    test_add()
    test_multiply()
    print("All tests passed!")
```

### 実行

```bash
cd profiles/my-profile/tools
python test_calculator.py
```

---

## チェックリスト

新しいツールを作成する際のチェックリスト：

- [ ] docstring を記述した
- [ ] 型ヒントを追加した
- [ ] エラーハンドリングを実装した
- [ ] テストを作成した
- [ ] agents/*.md で `tools:` に追加した
- [ ] 依存パッケージがあれば requirements.txt に記載した
