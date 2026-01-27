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


def _find_similar_files(path: str, max_results: int = 3) -> list:
    """類似ファイル名を検索"""
    try:
        abs_path = resolve_safe_path(path)
        parent_dir = os.path.dirname(abs_path)
        filename = os.path.basename(abs_path)
        
        if not os.path.exists(parent_dir):
            # 親ディレクトリも存在しない場合、作業ディレクトリから検索
            parent_dir = get_working_directory() or os.getcwd()
        
        if not os.path.isdir(parent_dir):
            return []
        
        # 同じディレクトリ内のファイルを取得
        try:
            files = os.listdir(parent_dir)
        except PermissionError:
            return []
        
        # 類似度でソート
        similar = difflib.get_close_matches(filename, files, n=max_results, cutoff=0.4)
        return [os.path.join(os.path.basename(parent_dir), f) for f in similar]
    except Exception:
        return []


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

    Examples:
        read_file("src/main.py")                    # 全体を読む
        read_file("src/main.py", offset=100, limit=50)  # 100行目から50行
        read_file("package.json")                   # JSON設定ファイル
    
    Tips:
        - 大きなファイルは offset/limit で分割して読む
        - バイナリファイルは読めない（テキストのみ）
    """
    try:
        # パスを解決
        abs_path = resolve_safe_path(path)
        if not os.path.exists(abs_path):
            msg = f"Error: File not found: {path}\n"
            similar = _find_similar_files(path)
            if similar:
                msg += "\n💡 Did you mean:\n"
                for s in similar:
                    msg += f"  - {s}\n"
            parent = os.path.dirname(path) or "."
            msg += f"\n🔄 Try: list_dir('{parent}') or glob_search('**/{os.path.basename(path)}')"
            return msg

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

            # new_lines = content.count('\n') + 1

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
        ext = os.path.splitext(path)[1].lower()
        msg = f"✅ Successfully wrote {lines} lines to {path}"
        
        # ファイル種別に応じた次のアクション提案
        if ext == '.py':
            msg += f"\n\n💡 Next: execute_bash('python {path}') to test"
        elif ext in ('.js', '.ts'):
            msg += f"\n\n💡 Next: execute_bash('node {path}') to test"
        elif ext == '.sh':
            msg += f"\n\n💡 Next: execute_bash('bash {path}') to run"
        
        return msg
    except Exception as e:
        return f"Error writing file: {e}"


def edit_file(path: str, old_string: str, new_string: str, dry_run: bool = False) -> str:
    """
    ファイルの一部を置換して編集します（search/replace形式）。
    既存ファイルの特定の文字列を新しい文字列に置き換えます。

    Args:
        path (str): 編集するファイルのパス
        old_string (str): 置換対象の文字列（短くユニークな部分を指定）
        new_string (str): 置換後の文字列
        dry_run (bool, optional): Trueの場合、実際に書き込まずに差分を表示します。

    Returns:
        str: 実行結果メッセージまたは差分

    ベストプラクティス（重要）:
        - old_string は短くユニークな部分だけを指定する（5-10行程度が理想）
        - 巨大な old_string は一致しにくい（エスケープ文字や空白の違いで失敗する）
        - JSONファイルの場合、変更したいキーの前後数行だけを指定する
        - 失敗した場合は read_file で実際の内容を確認し、より短い old_string で再試行する
    """
    try:
        abs_path = resolve_safe_path(path)
        if not os.path.exists(abs_path):
            msg = f"Error: File not found: {path}\n"
            similar = _find_similar_files(path)
            if similar:
                msg += "\n💡 Did you mean:\n"
                for s in similar:
                    msg += f"  - {s}\n"
            msg += f"\n🔄 Try: list_dir('{os.path.dirname(path) or '.'}') to find the correct file."
            return msg

        file_size = os.path.getsize(abs_path)
        if file_size > MAX_EDIT_SIZE:
             return f"Error: File too large ({file_size} bytes). Max {MAX_EDIT_SIZE} bytes."

        with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # 改行コードの揺らぎを吸収
        content_unix = content.replace('\r\n', '\n')
        old_unix = old_string.replace('\r\n', '\n')
        new_unix = new_string.replace('\r\n', '\n')

        new_content = None

        # 1. 完全一致 (Strict Match)
        if content.count(old_string) == 1:
            new_content = content.replace(old_string, new_string, 1)
        elif content_unix.count(old_unix) == 1:
            new_content = content_unix.replace(old_unix, new_unix, 1)

        if new_content is None:
            # 2. スマートマッチ (Indentation/Whitespace Insensitive Match)
            content_lines = content_unix.splitlines(keepends=True)
            old_lines = old_unix.splitlines()

            def normalize(s):
                return "".join(s.split())

            old_valid_indices = [i for i, line in enumerate(old_lines) if line.strip()]
            old_norm_lines = [normalize(old_lines[i]) for i in old_valid_indices]

            if not old_norm_lines:
                return "Error: old_string consists only of whitespace/empty lines."

            match_indices = []
            first_line_norm = old_norm_lines[0]

            for i, line in enumerate(content_lines):
                if normalize(line) == first_line_norm:
                    is_match = True
                    match_end_idx = i
                    old_idx_ptr = 1
                    offset = 1
                    while old_idx_ptr < len(old_norm_lines):
                        if i + offset >= len(content_lines):
                            is_match = False
                            break

                        target_line = content_lines[i + offset]
                        target_norm = normalize(target_line)
                        if target_norm == "":
                            offset += 1
                            continue

                        if target_norm == old_norm_lines[old_idx_ptr]:
                            old_idx_ptr += 1
                            match_end_idx = i + offset
                        else:
                            is_match = False
                            break
                        offset += 1

                    if is_match:
                        match_indices.append((i, match_end_idx + 1))

            if len(match_indices) == 0:
                msg = f"Error: old_string not found in {path}\n"
                
                # 部分一致を探す（最初の非空行で検索）
                first_old_line = next((l.strip() for l in old_lines if l.strip()), "")
                if first_old_line and len(first_old_line) > 10:
                    partial_matches = []
                    for i, line in enumerate(content_lines):
                        # 最初の20文字で部分一致を探す
                        if first_old_line[:20] in line or line.strip()[:20] in first_old_line:
                            partial_matches.append((i + 1, line.rstrip()[:80]))
                    
                    if partial_matches:
                        msg += f"\n📍 Partial matches found (line numbers where similar content exists):\n"
                        for line_num, preview in partial_matches[:3]:
                            msg += f"  Line {line_num}: {preview}...\n"
                        msg += "\nTip: Use read_file to check exact content around these lines.\n"
                
                # インデントの差異をチェック
                normalized_old = set(normalize(ol) for ol in old_lines if ol.strip())
                normalized_content = {normalize(cl): cl for cl in content_lines if cl.strip()}
                
                matching_normalized = normalized_old & set(normalized_content.keys())
                if matching_normalized:
                    msg += f"\n⚠️ Content matches but formatting differs.\n"
                    sample = list(matching_normalized)[0]
                    actual_line = normalized_content[sample]
                    msg += f"Expected (normalized): {sample[:60]}...\n"
                    msg += f"Actual in file: {actual_line.rstrip()[:60]}...\n"
                    msg += "Tip: Check whitespace, indentation, or escape sequences.\n"
                
                # JSON ファイルの場合は構造を表示
                if path.endswith('.json'):
                    msg += f"\n📋 For JSON files, consider reading the file first to get exact content.\n"
                    # 最初の10行を表示
                    msg += f"File preview (first 10 lines):\n"
                    for i, line in enumerate(content_lines[:10]):
                        msg += f"  {i+1}: {line.rstrip()[:70]}\n"
                
                # old_string が長すぎる場合のガイダンス
                old_line_count = len([l for l in old_lines if l.strip()])
                if old_line_count > 10:
                    msg += f"\n🔧 old_string が長すぎます（{old_line_count}行）。\n"
                    msg += "   → 変更したい部分の前後5行程度に絞って再試行してください。\n"
                    msg += "   → 特にJSONファイルでは、変更するキーの周辺だけを指定してください。\n"
                
                return msg

            if len(match_indices) > 1:
                msg = f"Error: Multiple matches found ({len(match_indices)}).\n"
                msg += "\n📍 Matches at:\n"
                for start, end in match_indices[:5]:
                    preview = content_lines[start].strip()[:60]
                    msg += f"  Line {start+1}: {preview}...\n"
                msg += "\n💡 Include more surrounding context in old_string to make it unique."
                return msg

            # 置換の実行
            start_line_idx, end_line_idx = match_indices[0]
            first_matched_line = content_lines[start_line_idx]
            original_indent = first_matched_line[:len(first_matched_line) - len(first_matched_line.lstrip())] if first_matched_line.strip() else ""

            new_lines_list = new_unix.splitlines()
            new_indents = [len(ln) - len(ln.lstrip()) for ln in new_lines_list if ln.strip()]
            min_new_indent = min(new_indents) if new_indents else 0

            replacement_lines = []
            for line in new_lines_list:
                if not line.strip():
                    replacement_lines.append("")
                    continue
                current_indent_len = len(line) - len(line.lstrip())
                relative_indent_len = max(0, current_indent_len - min_new_indent)
                indent_char = "\t" if "\t" in original_indent else " "
                indent_str = original_indent + (indent_char * relative_indent_len)
                replacement_lines.append(indent_str + line.lstrip())

            new_block = "\n".join(replacement_lines)
            if new_string.endswith('\n') or (end_line_idx < len(content_lines) and content_lines[end_line_idx-1].endswith('\n')):
                new_block += '\n'

            new_content = "".join(content_lines[:start_line_idx]) + new_block + "".join(content_lines[end_line_idx:])

        # 3. 差分シミュレーション (Dry Run)
        if dry_run:
            diff = difflib.unified_diff(
                content_unix.splitlines(),
                new_content.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm=""
            )
            diff_text = "\n".join(diff)
            if not diff_text:
                return f"Simulation: No changes would be applied to {path} (content already matches)."
            return f"Simulation Results for {path}:\n\n{diff_text}\n\nTo apply these changes, call edit_file with dry_run=False."

        # 4. 書き込み実行
        if os.environ.get('MOCO_INTERACTIVE_PATCH') == '1':
            try:
                from ..ui.patch_viewer import preview_patch, save_patch
                choice = preview_patch(path, content, new_content, title=f"Edit File: {path}")
                if choice == 'n':
                    return "Edit cancelled by user."
                if choice == 's':
                    save_patch(path, content, new_content)
                    return f"Patch saved for {path}. Edit cancelled."
            except ImportError:
                pass

        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        _TOKEN_CACHE.delete_by_path(abs_path)
        
        # 変更行数を計算
        old_line_count = len(old_string.splitlines())
        new_line_count = len(new_string.splitlines())
        diff = new_line_count - old_line_count
        
        msg = f"✅ Successfully edited {path}"
        if diff > 0:
            msg += f" (+{diff} lines)"
        elif diff < 0:
            msg += f" ({diff} lines)"
        
        return msg

    except Exception as e:
        import traceback
        return f"Error editing file: {e}\n{traceback.format_exc()}"


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
        return (
            "Error: Command execution timed out (60s).\n"
            "\n💡 Suggestions:\n"
            "  - Use start_background() for long-running commands\n"
            "  - Add a timeout flag if the command supports it\n"
            "  - Break the task into smaller steps"
        )
    except Exception as e:
        return f"Error executing command: {e}"
