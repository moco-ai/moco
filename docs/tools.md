# Tools API リファレンス

moco のツールモジュールの API リファレンスです。

---

## 概要

moco のツールは、エージェントがファイル操作、検索、Web アクセスなどのアクションを実行するための関数群です。

### ツールの種類

| カテゴリ | ツール | 説明 |
|---------|--------|------|
| **ファイル操作** | `read_file`, `write_file`, `edit_file` | ファイルの読み書き |
| **コマンド実行** | `execute_bash` | Bash コマンドの実行 |
| **ファイルシステム** | `list_dir`, `glob_search`, `tree`, `file_info` | ディレクトリ操作 |
| **検索** | `grep`, `ripgrep`, `find_definition`, `find_references`, `codebase_search` | コード検索 |
| **Web** | `websearch`, `webfetch` | Web 検索・取得 |
| **TODO** | `todowrite`, `todoread` | タスク管理 |

---

## ファイル操作

### read_file

ファイルの内容を読み込んで返します。

```python
def read_file(path: str, offset: int = None, limit: int = None) -> str:
    """
    ファイルの内容を読み込んで返します。

    Args:
        path: 読み込むファイルのパス
        offset: 読み込み開始行番号（1始まり）。省略時は1行目から。
        limit: 読み込む行数。省略時は全行。

    Returns:
        ファイルの内容（行番号付き）

    Examples:
        >>> read_file("main.py")  # 全文読み込み
        >>> read_file("main.py", offset=1, limit=50)  # 1-50行目
        >>> read_file("main.py", offset=100, limit=30)  # 100-129行目
    """
```

#### 使用例

```python
from moco.tools import read_file

# 全文読み込み
content = read_file("README.md")

# 部分読み込み（大きなファイル向け）
content = read_file("large_file.py", offset=100, limit=50)
```

#### 出力形式

```
     1|# README
     2|
     3|This is a sample file.
     4|...
```

---

### write_file

ファイルに内容を書き込みます。

```python
def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """
    ファイルに内容を書き込みます。

    Args:
        path: 書き込み先のファイルパス
        content: 書き込む内容
        overwrite: Trueの場合、既存ファイルを上書き。デフォルトはFalse。

    Returns:
        成功/失敗メッセージ

    Examples:
        >>> write_file("new_file.py", "print('hello')")  # 新規作成
        >>> write_file("existing.py", content, overwrite=True)  # 上書き
    """
```

#### 使用例

```python
from moco.tools import write_file

# 新規ファイル作成
result = write_file("output.txt", "Hello, World!")
# -> "Successfully wrote 1 lines to output.txt"

# 既存ファイルの上書き
result = write_file("output.txt", "Updated content", overwrite=True)
```

!!! warning "上書き保護"
    `overwrite=False`（デフォルト）の場合、既存ファイルへの書き込みはエラーになります。
    これは意図しないデータ損失を防ぐための安全機能です。

---

### edit_file

ファイルの一部を置換して編集します（search/replace 形式）。

```python
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """
    ファイルの一部を置換して編集します。

    Args:
        path: 編集するファイルのパス
        old_string: 置換対象の文字列（ユニークな文字列を指定すること）
        new_string: 置換後の文字列

    Returns:
        成功/失敗メッセージ

    Note:
        - old_string はファイル内で一意である必要があります
        - 複数箇所にマッチする場合はエラーになります
    """
```

#### 使用例

```python
from moco.tools import edit_file

# 関数名の変更
result = edit_file(
    "main.py",
    "def old_function():",
    "def new_function():"
)

# 複数行の置換
result = edit_file(
    "config.py",
    """DEBUG = True
LOG_LEVEL = "DEBUG"
""",
    """DEBUG = False
LOG_LEVEL = "INFO"
"""
)
```

!!! tip "ユニークな文字列を指定"
    `old_string` はファイル内で一意である必要があります。
    複数箇所にマッチする場合は、より具体的な文字列（前後の行を含める等）を指定してください。

---

### execute_bash

Bash コマンドを実行し、結果を返します。

```python
def execute_bash(command: str) -> str:
    """
    Bashコマンドを実行し、結果を返します。

    Args:
        command: 実行するBashコマンド

    Returns:
        コマンドの標準出力、またはエラー時の標準エラー出力

    Note:
        - タイムアウト: 60秒
        - セキュリティに注意して使用してください
    """
```

#### 使用例

```python
from moco.tools import execute_bash

# ディレクトリ一覧
result = execute_bash("ls -la")

# Git 操作
result = execute_bash("git status")

# Python スクリプトの実行
result = execute_bash("python -c 'print(1+1)'")
```

!!! danger "セキュリティ警告"
    `execute_bash` は任意のコマンドを実行できるため、ガードレールで制限することを推奨します。

---

## ファイルシステム

### list_dir

ディレクトリの内容を一覧表示します。

```python
def list_dir(path: str = ".", show_hidden: bool = False) -> str:
    """
    ディレクトリの内容を一覧表示します。

    Args:
        path: 一覧表示するディレクトリのパス
        show_hidden: 隠しファイルを表示するか

    Returns:
        ディレクトリ内容の一覧（ファイル/ディレクトリ、サイズ付き）
    """
```

---

### glob_search

パターンにマッチするファイルを検索します。

```python
def glob_search(pattern: str, directory: str = ".") -> str:
    """
    パターンにマッチするファイルを検索します。

    Args:
        pattern: 検索パターン（例: "**/*.py", "*.md"）
        directory: 検索開始ディレクトリ

    Returns:
        マッチしたファイルパスの一覧
    """
```

#### 使用例

```python
from moco.tools import glob_search

# すべての Python ファイル
result = glob_search("**/*.py")

# 特定ディレクトリ内の Markdown
result = glob_search("*.md", directory="docs")
```

---

### tree

ディレクトリ構造をツリー形式で表示します。

```python
def tree(path: str = ".", max_depth: int = 3) -> str:
    """
    ディレクトリ構造をツリー形式で表示します。

    Args:
        path: 表示するディレクトリのパス
        max_depth: 最大表示深度

    Returns:
        ツリー形式のディレクトリ構造
    """
```

---

### file_info

ファイルの詳細情報を取得します。

```python
def file_info(path: str) -> str:
    """
    ファイルの詳細情報を取得します。

    Args:
        path: ファイルパス

    Returns:
        ファイルサイズ、更新日時、パーミッションなどの情報
    """
```

---

## 検索

### grep

正規表現でファイル内を検索します。

```python
def grep(
    pattern: str,
    path: str = ".",
    recursive: bool = True,
    ignore_case: bool = False,
    max_results: int = 100
) -> str:
    """
    正規表現でファイル内を検索します。

    Args:
        pattern: 検索する正規表現パターン
        path: 検索対象のパス（ファイルまたはディレクトリ）
        recursive: ディレクトリを再帰的に検索するか
        ignore_case: 大文字小文字を無視するか
        max_results: 最大結果数

    Returns:
        マッチした行（ファイル名:行番号:内容）
    """
```

#### 使用例

```python
from moco.tools import grep

# 関数定義を検索
result = grep(r"def \w+\(", path="src/")

# 大文字小文字を無視して検索
result = grep("error", ignore_case=True)
```

---

### ripgrep

ripgrep (rg) を使用した高速検索です。

```python
def ripgrep(
    pattern: str,
    path: str = ".",
    file_type: str = None,
    ignore_case: bool = False
) -> str:
    """
    ripgrep を使用した高速検索。

    Args:
        pattern: 検索パターン
        path: 検索対象パス
        file_type: ファイルタイプフィルタ（例: "py", "js"）
        ignore_case: 大文字小文字を無視するか

    Returns:
        検索結果

    Note:
        システムに ripgrep (rg) がインストールされている必要があります。
    """
```

---

### find_definition

関数やクラスの定義を検索します。

```python
def find_definition(name: str, path: str = ".") -> str:
    """
    関数やクラスの定義を検索します。

    Args:
        name: 検索する関数名またはクラス名
        path: 検索対象パス

    Returns:
        定義が見つかったファイルと行番号
    """
```

---

### find_references

関数やクラスの参照箇所を検索します。

```python
def find_references(name: str, path: str = ".") -> str:
    """
    関数やクラスの参照箇所を検索します。

    Args:
        name: 検索する関数名またはクラス名
        path: 検索対象パス

    Returns:
        参照が見つかったファイルと行番号
    """
```

---

### codebase_search

セマンティック検索でコードベースを検索します。

```python
def codebase_search(
    query: str,
    target_dir: str = ".",
    top_k: int = 5
) -> str:
    """
    セマンティック検索でコードベースを検索します。

    Args:
        query: 自然言語のクエリ
        target_dir: 検索対象ディレクトリ
        top_k: 返す結果の数

    Returns:
        関連度の高いコードスニペット

    Note:
        OPENAI_API_KEY または GEMINI_API_KEY が必要です。
    """
```

#### 使用例

```python
from moco.tools import codebase_search

# 自然言語で検索
result = codebase_search("ユーザー認証の処理")
result = codebase_search("エラーハンドリングのパターン")
```

---

## Web

### websearch

Web 検索を実行します。

```python
def websearch(query: str, site_filter: str = None) -> str:
    """
    Web検索を実行します。

    Args:
        query: 検索クエリ
        site_filter: 検索を制限するドメイン（例: "github.com"）

    Returns:
        検索結果のタイトル、URL、スニペット
    """
```

#### 使用例

```python
from moco.tools import websearch

# 一般的な検索
result = websearch("Python asyncio tutorial")

# サイト限定検索
result = websearch("moco agent", site_filter="github.com")
```

---

### webfetch

Web ページの内容を取得します。

```python
def webfetch(url: str, question: str = None) -> str:
    """
    Webページの内容を取得します。

    Args:
        url: 取得するURL
        question: URLの内容に対する質問（省略時は要約）

    Returns:
        ページの内容または質問への回答
    """
```

#### 使用例

```python
from moco.tools import webfetch

# ページ内容を取得
result = webfetch("https://example.com/docs")

# 特定の情報を抽出
result = webfetch(
    "https://example.com/api-docs",
    question="認証方法は何ですか？"
)
```

---

## TODO 管理

### todowrite

TODO リストを作成・更新します。

```python
def todowrite(todos: str) -> str:
    """
    TODOリストを作成・更新します。

    Args:
        todos: JSON形式のTODOリスト
               [{"task": "タスク内容", "status": "pending|done"}]

    Returns:
        成功/失敗メッセージ
    """
```

#### 使用例

```python
from moco.tools import todowrite

todos = '''[
    {"task": "READMEを更新", "status": "pending"},
    {"task": "テストを追加", "status": "pending"},
    {"task": "リファクタリング", "status": "done"}
]'''

result = todowrite(todos)
```

---

### todoread

現在の TODO リストを読み込みます。

```python
def todoread() -> str:
    """
    現在のTODOリストを読み込みます。

    Returns:
        JSON形式のTODOリスト
    """
```

---

## ツールマップ

すべてのベースツールは `TOOL_MAP` からアクセスできます。

```python
from moco.tools import TOOL_MAP

# 利用可能なツール一覧
print(list(TOOL_MAP.keys()))

# 個別のツールを取得
read_file = TOOL_MAP["read_file"]
```

### エイリアス

互換性のため、以下のエイリアスが提供されています：

| エイリアス | 実際のツール |
|-----------|-------------|
| `read` | `read_file` |
| `write` | `write_file` |
| `edit` | `edit_file` |
| `bash` | `execute_bash` |

---

## カスタムツールの作成

プロファイル固有のツールを作成できます。

### 1. ツールファイルを作成

```python title="moco/profiles/my-profile/tools/my_tool.py"
def my_custom_tool(param1: str, param2: int = 10) -> str:
    """
    カスタムツールの説明。

    Args:
        param1: パラメータ1の説明
        param2: パラメータ2の説明（デフォルト: 10）

    Returns:
        処理結果
    """
    # 処理を実装
    return f"Result: {param1}, {param2}"
```

### 2. エージェント定義でツールを有効化

```yaml title="moco/profiles/my-profile/agents/my-agent.md"
---
description: カスタムエージェント
tools:
  - my_custom_tool
  - read_file
---
システムプロンプト...
```

### ツール作成のベストプラクティス

1. **明確な docstring**: 引数と戻り値を説明
2. **型ヒント**: すべての引数に型を指定
3. **エラーハンドリング**: 例外をキャッチして文字列で返す
4. **戻り値は文字列**: LLM が解釈できる形式で返す
