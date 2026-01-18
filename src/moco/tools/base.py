import subprocess
import os
import re
from typing import Tuple, Final
try:
    from ..utils.path import resolve_safe_path, get_working_directory
except ImportError:
    # サブプロセスからロードされる場合のフォールバック
    from moco.utils.path import resolve_safe_path, get_working_directory


# 安全性のためのデフォルト最大行数 (read_file)
DEFAULT_MAX_LINES = 10000

# 編集可能な最大ファイルサイズ (5MB)
MAX_EDIT_SIZE = 5 * 1024 * 1024

# 危険なパターンの定義
DANGEROUS_PATTERNS: Final[list[str]] = [
    # 破壊的な削除 (フラグに任意の文字を含めるように改善)
    r'rm\s+.*(-[a-z]*[rf][a-z]*|--recursive|--force)\s+(/|~|\$HOME)',
    # フォークボム
    r':\(\)\{\s*:\|:&\s*\};:',
    # フォーマット・ディスク操作 (ddをより厳しく)
    r'mkfs\.',
    r'dd\s+.*of=',
    # デバイス・ファイルへの直接書き込み/切り詰め ( /dev/null への出力は許可 )
    r'(?<![0-9&])>\s*[^&|]',
    r'(?<!&)[0-9]>\s*[^&|]',
    # 全開放パーミッション・所有権
    r'chmod\s+.*777',
    r'chown\s+.*-R',
    # シェル/インタプリタへの流し込み
    r'[|;&<]\s*(bash|sh|zsh|python\d?|perl|ruby|php|node)\b',
    r'\b(bash|sh|zsh|python\d?|perl|ruby|php|node)\s+.*-c\s+',
    # リモートスクリプト実行 (パイプ先を拡充)
    r'(curl|wget).*([|;&<]\s*(bash|sh|zsh|python\d?|perl|ruby|node)\b|-o\s+)',
    # 特権使用
    r'sudo\s+(rm|dd|chmod|chown|mkfs|su|apt|yum|dnf)\b',
    # findによる削除
    r'find\s+.*\s+-delete',
]

# 事前にコンパイルしてパフォーマンスを改善
_DANGEROUS_RE = re.compile('|'.join(f'(?:{p})' for p in DANGEROUS_PATTERNS), re.IGNORECASE)


def is_dangerous_command(command: str) -> Tuple[bool, str]:
    """コマンドが危険かどうかチェック"""
    # /dev/null へのリダイレクトを一時的に無害化（判定から除外）
    # スペースの有無にかかわらず対応
    safe_command = re.sub(r'[0-9&]?\s*>\s*/dev/null', '', command, flags=re.IGNORECASE)
    
    normalized_command = safe_command.strip()
    match = _DANGEROUS_RE.search(normalized_command)
    if match:
        return True, "Potentially destructive or unauthorized command detected."
    return False, ""


def read_file(path: str, offset: int = None, limit: int = None) -> str:
    """
    ファイルの内容を読み込んで返します。
    大きなファイルの場合は offset と limit を指定して一部だけ読むことを推奨。

    Args:
        path (str): 読み込むファイルのパス
        offset (int, optional): 読み込み開始行番号（1始まり）。省略時は1行目から。
        limit (int, optional): 読み込む行数。省略時は全行（ただし DEFAULT_MAX_LINES までの制限あり）。

    Returns:
        str: ファイルの内容（行番号付き）
    """
    try:
        # パスを解決
        path = resolve_safe_path(path)
        if not os.path.exists(path):
            return f"Error: File not found: {path}"

        # LLM からの入力を安全にキャスト
        try:
            start_line = max(1, int(offset)) if offset is not None else 1
            max_lines = int(limit) if limit is not None else DEFAULT_MAX_LINES
        except (ValueError, TypeError):
            return "Error: Invalid offset or limit. They must be integers."

        result_lines = []
        current_line = 0

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            # 指定行までスキップ (メモリ効率のため iterator を使用)
            for _ in range(start_line - 1):
                if not f.readline():
                    break
                current_line += 1

            if current_line < start_line - 1:
                return f"Error: offset {start_line} is beyond file length"

            if start_line > 1:
                result_lines.append(f"... {start_line - 1} lines not shown ...")

            # 必要な分だけ読み込み
            count = 0
            for line in f:
                current_line += 1
                result_lines.append(f"{current_line:6}|{line.rstrip()}")
                count += 1
                if count >= max_lines:
                    # 続きがあるか確認
                    if f.readline():
                        # 総行数は数えない (巨大ファイル対策)
                        result_lines.append(f"... more lines available (limit={max_lines}) ...")
                    break

        return "\n".join(result_lines)

    except Exception as e:
        return f"Error reading file: {e}"


def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """
    ファイルに内容を書き込みます。
    """
    try:
        # パスを解決
        path = resolve_safe_path(path)

        # 既存ファイルのガード
        if os.path.exists(path) and not overwrite:
            file_size = os.path.getsize(path)
            return (
                f"Error: File already exists: {path} ({file_size} bytes)\n"
                f"To overwrite, use: write_file(path, content, overwrite=True)\n"
                f"Or read the file first with: read_file('{path}')"
            )

        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        lines = content.count('\n') + 1
        return f"Successfully wrote {lines} lines to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """
    ファイルの一部を置換して編集します（search/replace形式）。
    """
    try:
        # パスを解決
        path = resolve_safe_path(path)

        if not os.path.exists(path):
            return f"Error: File not found: {path}"

        # 巨大ファイル編集のガード
        file_size = os.path.getsize(path)
        if file_size > MAX_EDIT_SIZE:
             return (
                 f"Error: File is too large to edit via string replacement ({file_size} bytes). "
                 f"Maximum allowed size is {MAX_EDIT_SIZE} bytes."
             )

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

        # 統計
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


def execute_bash(command: str, allow_dangerous: bool = False) -> str:
    """
    Bashコマンドを実行し、結果を返します。
    """
    try:
        # 危険なコマンドのチェック
        if not allow_dangerous:
            is_dangerous, reason = is_dangerous_command(command)
            if is_dangerous:
                return f"Error: Command blocked for security reasons. {reason}"

        # パスを作業ディレクトリを基準に解決
        working_dir = get_working_directory()

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
