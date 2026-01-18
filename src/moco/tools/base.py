import subprocess
import os


def _resolve_path(path: str) -> str:
    """
    相対パスを MOCO_WORKING_DIRECTORY を基準に解決する。
    絶対パスの場合はそのまま返す。
    """
    if os.path.isabs(path):
        return path
    
    working_dir = os.environ.get('MOCO_WORKING_DIRECTORY')
    if working_dir:
        return os.path.join(working_dir, path)
    
    return path


def read_file(path: str, offset: int = None, limit: int = None) -> str:
    """
    ファイルの内容を読み込んで返します。
    大きなファイルの場合は offset と limit を指定して一部だけ読むことを推奨。
    
    Args:
        path (str): 読み込むファイルのパス
        offset (int, optional): 読み込み開始行番号（1始まり）。省略時は1行目から。
        limit (int, optional): 読み込む行数。省略時は全行。
        
    Returns:
        str: ファイルの内容（行番号付き）
        
    Examples:
        read_file("main.py")  # 全文読み込み
        read_file("main.py", offset=1, limit=50)  # 1-50行目
        read_file("main.py", offset=100, limit=30)  # 100-129行目
    """
    try:
        # パスを解決
        path = _resolve_path(path)
        
        # LLM が文字列で渡す場合があるので変換
        if offset is not None:
            offset = int(offset)
        if limit is not None:
            limit = int(limit)
        
        if not os.path.exists(path):
            return f"Error: File not found: {path}"
        
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        
        # offset/limit が指定されていない場合は全文返す
        if offset is None and limit is None:
            # 行番号付きで返す
            numbered_lines = []
            for i, line in enumerate(lines, 1):
                numbered_lines.append(f"{i:6}|{line.rstrip()}")
            return "\n".join(numbered_lines)
        
        # offset/limit 処理
        start = (offset or 1) - 1  # 0-indexed に変換
        start = max(0, start)
        
        if limit:
            end = start + limit
        else:
            end = total_lines
        
        end = min(end, total_lines)
        
        # 範囲外チェック
        if start >= total_lines:
            return f"Error: offset {offset} is beyond file length ({total_lines} lines)"
        
        # 行番号付きで返す
        result_lines = []
        if start > 0:
            result_lines.append(f"... {start} lines not shown ...")
        
        for i in range(start, end):
            line_num = i + 1
            result_lines.append(f"{line_num:6}|{lines[i].rstrip()}")
        
        if end < total_lines:
            result_lines.append(f"... {total_lines - end} lines not shown ...")
        
        return "\n".join(result_lines)
        
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """
    ファイルに内容を書き込みます。
    既存ファイルを上書きする場合は overwrite=True を指定するか、
    先に read_file でファイル内容を確認してください。
    
    Args:
        path (str): 書き込み先のファイルパス
        content (str): 書き込む内容
        overwrite (bool): Trueの場合、既存ファイルを上書き。デフォルトはFalse。
        
    Returns:
        str: 成功/失敗メッセージ
        
    Examples:
        write_file("new_file.py", "print('hello')")  # 新規作成
        write_file("existing.py", content, overwrite=True)  # 上書き
    """
    try:
        # パスを解決
        path = _resolve_path(path)
        
        # 既存ファイルのガード
        if os.path.exists(path) and not overwrite:
            file_size = os.path.getsize(path)
            return (
                f"Error: File already exists: {path} ({file_size} bytes)\n"
                f"To overwrite, use: write_file(path, content, overwrite=True)\n"
                f"Or read the file first with: read_file('{path}')"
            )
        
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        lines = content.count('\n') + 1
        return f"Successfully wrote {lines} lines to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """
    ファイルの一部を置換して編集します（search/replace形式）。
    
    Args:
        path (str): 編集するファイルのパス
        old_string (str): 置換対象の文字列（ユニークな文字列を指定すること）
        new_string (str): 置換後の文字列
        
    Returns:
        str: 成功/失敗メッセージ
        
    Examples:
        edit_file("main.py", "def old_func():", "def new_func():")
        edit_file("cli.py", 
            "def version():\n    pass",
            "def version():\n    print('v1.0')\n\ndef chat():\n    pass"
        )
        
    注意:
        - old_string はファイル内で一意である必要があります
        - 複数箇所にマッチする場合はエラーになります
        - 大きな変更を追加する場合は、既存コードの後に挿入位置を指定してください
    """
    try:
        # パスを解決
        path = _resolve_path(path)
        
        if not os.path.exists(path):
            return f"Error: File not found: {path}"
        
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # old_string がファイル内に存在するか確認
        count = content.count(old_string)
        
        if count == 0:
            # 部分一致を探してヒントを出す
            lines = old_string.split('\n')
            first_line = lines[0].strip() if lines else ""
            hints = []
            
            # 最初の行で部分一致を探す
            if first_line and first_line in content:
                hints.append(f"'{first_line}' は見つかりましたが、完全一致しません。")
                hints.append("インデントや空白、コメントを確認してください。")
            
            # 空白を無視してマッチを探す
            old_normalized = ' '.join(old_string.split())
            content_normalized = ' '.join(content.split())
            if old_normalized in content_normalized:
                hints.append("空白を正規化すると一致します。インデントが異なる可能性があります。")
            
            # 推奨アクション
            hints.append(f"\n推奨: read_file('{path}') で実際の内容を確認してから、正確な文字列をコピペしてください。")
            
            hint_str = "\n".join(hints) if hints else ""
            return f"Error: old_string not found in {path}\n{hint_str}"
        
        if count > 1:
            return f"Error: old_string found {count} times in {path}. Please provide a more specific string."
        
        # 置換実行
        new_content = content.replace(old_string, new_string, 1)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        # 変更の統計
        old_lines = old_string.count('\n') + 1
        new_lines = new_string.count('\n') + 1
        diff = new_lines - old_lines
        
        if diff > 0:
            return f"Successfully edited {path}: replaced {old_lines} lines with {new_lines} lines (+{diff})"
        elif diff < 0:
            return f"Successfully edited {path}: replaced {old_lines} lines with {new_lines} lines ({diff})"
        else:
            return f"Successfully edited {path}: modified {old_lines} lines"
        
    except Exception as e:
        return f"Error editing file: {e}"


def execute_bash(command: str) -> str:
    """
    Bashコマンドを実行し、結果を返します。
    セキュリティに注意して使用してください。
    
    Args:
        command (str): 実行するBashコマンド
        
    Returns:
        str: コマンドの標準出力、またはエラー時の標準エラー出力
    """
    try:
        # 作業ディレクトリを取得
        working_dir = os.environ.get('MOCO_WORKING_DIRECTORY')
        
        # タイムアウトを設けて実行
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=working_dir  # 作業ディレクトリを指定
        )
        
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
            
        if result.returncode != 0:
            output += f"\nReturn Code: {result.returncode}"
            
        return output.strip() if output else "Command executed successfully (no output)."
        
    except subprocess.TimeoutExpired:
        return "Error: Command execution timed out (60s)."
    except Exception as e:
        return f"Error executing command: {e}"
