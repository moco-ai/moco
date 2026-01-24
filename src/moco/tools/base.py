import subprocess
import os
import re
import difflib
from typing import Tuple, Final
try:
    from ..utils.path import resolve_safe_path, get_working_directory
    from ..core.token_cache import TokenCache
except ImportError:
    # サブプロセスからロードされる場合のフォールバック
    from moco.utils.path import resolve_safe_path, get_working_directory
    from moco.core.token_cache import TokenCache

# 安全性のためのデフォルト最大行数 (read_file)
DEFAULT_MAX_LINES = 10000

# 編集可能な最大ファイルサイズ (5MB)
MAX_EDIT_SIZE = 5 * 1024 * 1024

# キャッシュインスタンス
_TOKEN_CACHE = TokenCache()

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
        abs_path = resolve_safe_path(path)
        if not os.path.exists(abs_path):
            return f"Error: File not found: {path}"

        # LLM からの入力を安全にキャスト
        try:
            start_line = max(1, int(offset)) if offset is not None else 1
            max_lines = int(limit) if limit is not None else DEFAULT_MAX_LINES
        except (ValueError, TypeError):
            return "Error: Invalid offset or limit. They must be integers."

        # キャッシュのチェック (全文読み取り時のみキャッシュ機能を利用/保存する)
        # NOTE: limit が文字列で渡されることがあるため、比較は int にキャスト済みの max_lines を使う
        is_full_read = (start_line == 1 and (limit is None or max_lines >= DEFAULT_MAX_LINES))
        
        raw_content = None
        if is_full_read:
            raw_content = _TOKEN_CACHE.get(abs_path)

        if raw_content is None:
            # キャッシュにない場合はファイルから読み込み
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    raw_content = f.read()
                # 全文読み取り時のみキャッシュに保存
                if is_full_read:
                    _TOKEN_CACHE.set(abs_path, raw_content)
            except Exception as e:
                return f"Error reading file: {e}"

        # 生データから指定範囲を抽出してフォーマット
        lines = raw_content.splitlines()
        total_lines = len(lines)
        
        result_lines = []
        if start_line > 1:
            result_lines.append(f"... {start_line - 1} lines not shown ...")

        # 抽出範囲
        end_idx = min(start_line - 1 + max_lines, total_lines)
        for i in range(start_line - 1, end_idx):
            line_num = i + 1
            result_lines.append(f"{line_num:6}|{lines[i]}")

        if end_idx < total_lines:
            result_lines.append(f"... more lines available (limit={max_lines}, total={total_lines}) ...")
            
        return "\n".join(result_lines)

    except Exception as e:
        return f"Error processing file: {e}"


def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """
    ファイルに内容を書き込みます。新規ファイルを作成するか、既存ファイルを上書きします。

    Args:
        path (str): 書き込み先のファイルパス（相対パスまたは絶対パス）
        content (str): ファイルに書き込む内容（テキスト全体）
        overwrite (bool, optional): Trueの場合、既存ファイルを上書き。デフォルトはFalse。

    Returns:
        str: 成功時は書き込んだ行数、失敗時はエラーメッセージ

    IMPORTANT（必ず守る）:
        - 必須: `path` と `content` を必ず渡す（片方でも欠けると実行されません）
        - 引数は「JSONオブジェクト1つ」で渡す（途中で切れた `{` や複数JSONはNG）
        - 改行は `\\n`、ダブルクォートは `\\"` に必ずエスケープする

    IMPORTANT - JSON arguments format:
        {
            "path": "ファイルパス",
            "content": "ファイル内容（改行は\\nでエスケープ）",
            "overwrite": false
        }

    Example JSON calls:
        {"path": "hello.txt", "content": "Hello World!"}
        {"path": "script.py", "content": "def main():\\n    print('hello')\\n"}
        {"path": "config.yaml", "content": "name: test\\nversion: 1.0\\n", "overwrite": true}

    注意:
        - content内の改行は必ず \\n でエスケープすること
        - content内のダブルクォートは \\" でエスケープすること
        - 全てのキーと文字列値はダブルクォートで囲むこと
    """
    try:
        # パスを解決
        abs_path = resolve_safe_path(path)

        # インタラクティブパッチUIの呼び出し
        if os.environ.get('MOCO_INTERACTIVE_PATCH') == '1':
            from ..ui.patch_viewer import preview_patch, save_patch
            old_content = ""
            if os.path.exists(abs_path):
                with open(abs_path, 'r', encoding='utf-8') as f:
                    old_content = f.read()

            choice = preview_patch(path, old_content, content, title=f"Write File: {path}")
            if choice == 'n':
                return "Write cancelled by user."
            if choice == 's':
                save_patch(path, old_content, content)
                return f"Patch saved for {path}. Write cancelled."
            if choice == 'e':
                return "Edit mode not implemented yet, write cancelled."

        # 既存ファイルのガード
        if os.path.exists(abs_path):
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    existing_lines = sum(1 for _ in f)
            except Exception:
                existing_lines = 0

            new_lines = content.count('\n') + 1

            # 既存ファイルが5行以上の場合は、意図しない大規模なデータ消失を防ぐため
            # write_file による全上書きを制限し、edit_file を推奨する。
            if existing_lines >= 5:
                # 警告メッセージ。overwrite=True が指定されていても拒否する。
                return (
                    f"Error: Substantial existing content detected ({existing_lines} lines).\n"
                    "Overwriting large files with write_file is prohibited to prevent accidental data loss.\n"
                    "Please use edit_file(path, old_string, new_string) for partial modifications."
                )

            if not overwrite:
                file_size = os.path.getsize(abs_path)
                return (
                    f"Error: File already exists: {path} ({file_size} bytes)\n"
                    "To overwrite (only for small files < 5 lines), use: overwrite=True"
                )

        if os.path.dirname(abs_path):
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # キャッシュを無効化
        _TOKEN_CACHE.delete_by_path(abs_path)

        lines = content.count('\n') + 1
        return f"Successfully wrote {lines} lines to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """
    ファイルの一部を置換して編集します（search/replace形式）。
    既存ファイルの特定の文字列を新しい文字列に置き換えます。

    Args:
        path (str): 編集するファイルのパス
        old_string (str): 置換対象の文字列（ファイル内で一意である必要があります）
        new_string (str): 置換後の文字列

    Returns:
        str: 成功時は成功メッセージ、失敗時はエラーメッセージ

    Note:
        - 最初に完全一致を試行し、失敗した場合はインデントや空白を無視したスマートマッチを試行します。
        - 置換箇所が複数見つかった場合はエラーとなります。
    """
    try:
        # パスを解決
        abs_path = resolve_safe_path(path)

        if not os.path.exists(abs_path):
            return f"Error: File not found: {path}"

        # 巨大ファイル編集のガード
        file_size = os.path.getsize(abs_path)
        if file_size > MAX_EDIT_SIZE:
             return (
                 f"Error: File is too large to edit via string replacement ({file_size} bytes). "
                 f"Maximum allowed size is {MAX_EDIT_SIZE} bytes."
             )

        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # --- ステップ1: 完全一致の試行 ---
        count = content.count(old_string)
        
        if count == 1:
            new_content = content.replace(old_string, new_string, 1)
        elif count > 1:
            return f"Error: old_string found {count} times in {path}. Please provide a more specific string."
        else:
            # --- ステップ2: スマートマッチ (空白・インデント無視) の試行 ---
            # 各行を正規化（前後の空白削除）して比較
            content_lines = content.splitlines(keepends=True)
            old_lines = old_string.splitlines()
            
            def normalize(s):
                return "".join(s.split())

            old_norm_lines = [normalize(line) for line in old_lines if line.strip()]
            
            match_indices = []
            for i in range(len(content_lines) - len(old_norm_lines) + 1):
                # content側の空白行を飛ばしつつ、old_linesの有効な行数分を比較
                window = []
                curr_idx = i
                while len(window) < len(old_norm_lines) and curr_idx < len(content_lines):
                    line = content_lines[curr_idx].strip()
                    if line:
                        window.append(normalize(line))
                    curr_idx += 1
                
                if window == old_norm_lines:
                    # マッチした範囲（i から curr_idx まで）を記録
                    match_indices.append((i, curr_idx))

            if len(match_indices) == 0:
                # 診断レポートの作成
                diff = difflib.ndiff(old_string.splitlines(), content.splitlines())
                nearby_matches = [line for line in diff if line.startswith('  ')] # 共通行
                
                msg = f"Error: old_string not found in {path}\n"
                msg += "ヒント: 完全一致もスマートマッチも見つかりませんでした。\n"
                if len(old_lines) > 5:
                    msg += "範囲が広すぎる可能性があります。修正したい箇所の前後2-3行に絞ってみてください。"
                return msg
            
            if len(match_indices) > 1:
                return f"Error: multiple fuzzy matches found ({len(match_indices)}). Please be more specific."

            # 置換実行（スマートマッチ成功）
            start, end = match_indices[0]
            
            # 元のインデントを維持するための簡易的な推測
            # 最初の非空行のインデントを取得
            original_indent = ""
            for idx in range(start, end):
                if content_lines[idx].strip():
                    original_indent = content_lines[idx][:len(content_lines[idx]) - len(content_lines[idx].lstrip())]
                    break
            
            # new_string の各行にベースインデントを付与
            # new_string 内の最小インデントを計算し、それを original_indent に置き換える
            new_lines_raw = new_string.splitlines()
            
            # new_string 本体の最小インデント幅を特定
            new_indents = []
            for l in new_lines_raw:
                if l.strip():
                    new_indents.append(len(l) - len(l.lstrip()))
            min_new_indent = min(new_indents) if new_indents else 0

            adjusted_new_lines = []
            for line in new_lines_raw:
                if line.strip():
                    # 自身の相対インデントを維持しつつ、ベースインデントを付与
                    relative_indent = line[:len(line) - len(line.lstrip())][min_new_indent:]
                    adjusted_new_lines.append(original_indent + relative_indent + line.lstrip())
                else:
                    adjusted_new_lines.append("")
            
            new_block = "\n".join(adjusted_new_lines)
            if content_lines[end-1].endswith('\n') and not new_block.endswith('\n'):
                new_block += '\n'
                
            new_content = "".join(content_lines[:start]) + new_block + "".join(content_lines[end:])

        # --- ステップ3: 書き込み ---
        # インタラクティブパッチUIの呼び出し (環境変数が設定されている場合)
        if os.environ.get('MOCO_INTERACTIVE_PATCH') == '1':
            try:
                from ..ui.patch_viewer import preview_patch, save_patch
                choice = preview_patch(path, content, new_content, title=f"Edit File: {path}")
                if choice == 'n': return "Edit cancelled by user."
                if choice == 's':
                    save_patch(path, content, new_content)
                    return f"Patch saved for {path}. Edit cancelled."
            except ImportError:
                pass

        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        # キャッシュを無効化
        _TOKEN_CACHE.delete_by_path(abs_path)
        return f"Successfully edited {path} using {'exact' if count == 1 else 'smart'} match."

    except Exception as e:
        return f"Error editing file: {e}"


def execute_bash(command: str, allow_dangerous: bool = False) -> str:
    """
    Bashコマンドを実行し、結果を返します。
    """
    try:
        # サンドボックス実行の判定
        use_sandbox = os.environ.get("MOCO_SANDBOX") == "1"
        sandbox_image = os.environ.get("MOCO_SANDBOX_IMAGE", "python:3.12-slim")

        # 危険なコマンドのチェック
        if not allow_dangerous and not use_sandbox:
            is_dangerous, reason = is_dangerous_command(command)
            if is_dangerous:
                return f"Error: Command blocked for security reasons. {reason}"

        # パスを作業ディレクトリを基準に解決
        working_dir = get_working_directory()

        if use_sandbox:
            from .sandbox import execute_bash_in_sandbox
            return execute_bash_in_sandbox(
                command,
                image=sandbox_image,
                working_dir=working_dir,
                network_disabled=True,  # デフォルトで制限
                timeout=60
            )

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
