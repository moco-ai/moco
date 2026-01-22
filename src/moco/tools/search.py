# -*- coding: utf-8 -*-
"""検索ツール"""
import os
import re
import subprocess
from typing import List
from moco.utils.path import resolve_safe_path


def grep(pattern: str, path: str = '.', recursive: bool = True,
         ignore_case: bool = False, max_results: int = 100) -> str:
    """
    正規表現でファイル内を検索します。

    Args:
        pattern: 検索する正規表現パターン
        path: 検索対象のパス（ファイルまたはディレクトリ）
        recursive: ディレクトリを再帰的に検索するか
        ignore_case: 大文字小文字を無視するか
        max_results: 最大結果数

    Returns:
        検索結果
    """
    try:
        path = resolve_safe_path(path)
        results = []
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)

        def search_file(file_path: str) -> List[str]:
            matches = []
            MAX_LINE_LENGTH = 500
            
            # シンボリックリンクの安全性をチェック
            try:
                # 実体を解決して安全性を確認
                resolved_file_path = resolve_safe_path(file_path)
            except PermissionError:
                return []

            try:
                with open(resolved_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            line_content = line.rstrip()
                            if len(line_content) > MAX_LINE_LENGTH:
                                line_content = line_content[:MAX_LINE_LENGTH] + "... [TRUNCATED]"
                            matches.append(f"{file_path}:{line_num}: {line_content}")
            except Exception:
                pass
            return matches

        if os.path.isfile(path):
            results.extend(search_file(path))
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                # 隠しディレクトリと一般的なビルド/依存関係ディレクトリをスキップ
                # - node_modules: npm/yarn の依存関係（大量のファイルを含む）
                # - __pycache__: Python のバイトコードキャッシュ
                # - venv: Python 仮想環境
                # - .git: Git リポジトリメタデータ（隠しディレクトリとして除外）
                # これらは検索対象として意味がなく、パフォーマンスに大きく影響するため除外
                dirs[:] = [d for d in dirs if not d.startswith('.')
                          and d not in ('node_modules', '__pycache__', 'venv', '.git')]

                for file in files:
                    if file.startswith('.'):
                        continue
                    file_path = os.path.join(root, file)
                    results.extend(search_file(file_path))

                    if len(results) >= max_results:
                        break

                if len(results) >= max_results:
                    break

                if not recursive:
                    break
        else:
            return f"Error: Path not found: {path}"

        if not results:
            return f"No matches found for '{pattern}' in {path}"

        if len(results) >= max_results:
            return f"Found {max_results}+ matches (truncated). To see more results, please refine your search pattern or search in a more specific subdirectory.\n" + "\n".join(results[:max_results])

        return f"Found {len(results)} matches:\n" + "\n".join(results)

    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"
    except Exception as e:
        return f"Error searching: {e}"


def find_definition(symbol: str, directory: str = '.', language: str = 'python') -> str:
    """
    関数やクラスの定義を検索します。

    Args:
        symbol: 検索するシンボル名（関数名、クラス名）
        directory: 検索ディレクトリ
        language: 言語 (python, javascript, typescript, go)

    Returns:
        定義の場所
    """
    patterns = {
        'python': [
            rf'^(async\s+)?def\s+{re.escape(symbol)}\s*\(',
            rf'^class\s+{re.escape(symbol)}\s*[:\(]',
            rf'^{re.escape(symbol)}\s*=',
        ],
        'javascript': [
            rf'(function\s+{re.escape(symbol)}\s*\(|const\s+{re.escape(symbol)}\s*=|let\s+{re.escape(symbol)}\s*=|var\s+{re.escape(symbol)}\s*=)',
            rf'class\s+{re.escape(symbol)}\s*[\{{]',
            rf'{re.escape(symbol)}\s*:\s*function',
        ],
        'typescript': [
            rf'(function\s+{re.escape(symbol)}\s*[<\(]|const\s+{re.escape(symbol)}\s*[=:]|let\s+{re.escape(symbol)}\s*[=:])',
            rf'(class|interface|type|enum)\s+{re.escape(symbol)}\s*[<\{{\(]',
            rf'export\s+(default\s+)?(function|class|const|let|type|interface)\s+{re.escape(symbol)}',
        ],
        'go': [
            rf'^func\s+(\([^)]+\)\s+)?{re.escape(symbol)}\s*\(',
            rf'^type\s+{re.escape(symbol)}\s+(struct|interface)',
            rf'^var\s+{re.escape(symbol)}\s+',
        ],
    }

    extensions = {
        'python': ['.py'],
        'javascript': ['.js', '.jsx', '.mjs'],
        'typescript': ['.ts', '.tsx'],
        'go': ['.go'],
    }

    lang_patterns = patterns.get(language, patterns['python'])
    lang_extensions = extensions.get(language, extensions['python'])

    directory = resolve_safe_path(directory)
    results = []

    try:
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith('.')
                      and d not in ('node_modules', '__pycache__', 'venv', '.git', 'dist', 'build')]

            for file in files:
                if not any(file.endswith(ext) for ext in lang_extensions):
                    continue

                file_path = os.path.join(root, file)
                
                # 安全性をチェック
                try:
                    resolved_file_path = resolve_safe_path(file_path)
                except PermissionError:
                    continue

                try:
                    with open(resolved_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            for pattern in lang_patterns:
                                if re.search(pattern, line):
                                    results.append(f"{file_path}:{line_num}: {line.rstrip()}")
                                    break
                except Exception:
                    continue

        if not results:
            return f"No definition found for '{symbol}' in {directory}"

        return f"Found {len(results)} definition(s):\n" + "\n".join(results)

    except Exception as e:
        return f"Error searching for definition: {e}"


def find_references(symbol: str, directory: str = '.', max_results: int = 50) -> str:
    """
    シンボルの参照を検索します。

    Args:
        symbol: 検索するシンボル名
        directory: 検索ディレクトリ
        max_results: 最大結果数

    Returns:
        参照の場所
    """
    # 単語境界を使用してシンボルを検索
    pattern = rf'\b{re.escape(symbol)}\b'
    return grep(pattern, directory, recursive=True, max_results=max_results)


def ripgrep(pattern: str, path: str = '.', file_type: str = None) -> str:
    """
    ripgrep (rg) を使用した高速検索。
    rgがインストールされていない場合はgrep関数にフォールバック。

    Args:
        pattern: 検索パターン
        path: 検索パス
        file_type: ファイルタイプ (py, js, ts, go, etc.)

    Returns:
        検索結果
    """
    try:
        path = resolve_safe_path(path)
        cmd = ['rg', '--line-number', '--no-heading', pattern, path]

        if file_type:
            cmd.extend(['--type', file_type])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            return result.stdout.strip() or f"No matches found for '{pattern}'"
        elif result.returncode == 1:
            return f"No matches found for '{pattern}'"
        else:
            # rgが利用できない場合はPython実装にフォールバック
            return grep(pattern, path)

    except FileNotFoundError:
        # rgがインストールされていない
        return grep(pattern, path)
    except subprocess.TimeoutExpired:
        return "Error: Search timed out"
    except Exception as e:
        return f"Error: {e}"
