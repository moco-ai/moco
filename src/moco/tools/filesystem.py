# -*- coding: utf-8 -*-
"""ファイルシステム操作ツール"""
import os
import glob as glob_module
import json
from typing import List, Optional
from moco.utils.path import resolve_safe_path


def list_dir(path: str = '.', show_hidden: bool = False) -> str:
    """
    ディレクトリの内容を一覧表示します。

    Args:
        path: 一覧表示するディレクトリのパス
        show_hidden: 隠しファイルを表示するか

    Returns:
        ディレクトリ内容のリスト
    """
    try:
        path = resolve_safe_path(path)

        if not os.path.exists(path):
            return f"Error: Directory not found: {path}"

        if not os.path.isdir(path):
            return f"Error: Not a directory: {path}"

        items = os.listdir(path)

        if not show_hidden:
            items = [item for item in items if not item.startswith('.')]

        items.sort()

        result = []
        for item in items:
            full_path = os.path.join(path, item)
            if os.path.isdir(full_path):
                result.append(f"📁 {item}/")
            else:
                size = os.path.getsize(full_path)
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size // 1024}KB"
                else:
                    size_str = f"{size // (1024 * 1024)}MB"
                result.append(f"📄 {item} ({size_str})")

        if not result:
            return f"Directory {path} is empty"

        return f"Contents of {path}:\n" + "\n".join(result)

    except Exception as e:
        return f"Error listing directory: {e}"


def read_file(path: str, offset: int = 1, limit: int = 10000) -> str:
    """
    ファイルを読み込みます。

    Args:
        path: 読み込むファイルのパス
        offset: 読み込み開始行番号（1始まり）
        limit: 読み込む行数（デフォルト10000）

    Returns:
        ファイルの内容
    """
    MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
    MAX_LINE_LENGTH = 1000

    try:
        path = resolve_safe_path(path)

        if not os.path.exists(path):
            return f"Error: File not found: {path}"

        if not os.path.isfile(path):
            return f"Error: Not a file: {path}"

        file_size = os.path.getsize(path)

        # 2MBを超えるファイルで offset/limit がデフォルトのままの場合は警告
        if file_size > MAX_FILE_SIZE and offset == 1 and limit == 50:
             return f"Error: File size ({file_size} bytes) exceeds 2MB. Please specify 'offset' and 'limit' to read this file."

        lines = []
        next_offset = offset

        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f, 1):
                if i < offset:
                    continue
                if i >= offset + limit:
                    next_offset = i
                    break

                # 1行あたルの文字数制限
                if len(line) > MAX_LINE_LENGTH:
                    line = line[:MAX_LINE_LENGTH] + "... [TRUNCATED]"

                lines.append(f"{i:6}|{line.rstrip()}")
                next_offset = i + 1

        if not lines:
            return f"No content found at offset {offset}"

        result = "\n".join(lines)

        # 切り捨て通知
        if len(lines) == limit:
            result += f"\n\n⚠️ Content truncated to {limit} lines.\n👉 NEXT STEP: 続きを読むには以下を実行:\n   read_file(path=\"{path}\", offset={next_offset}, limit={limit})"

        return result

    except Exception as e:
        return f"Error reading file: {e}"


def glob_search(pattern: str, directory: str = '.') -> str:
    """
    globパターンでファイルを検索します。

    Args:
        pattern: 検索パターン (例: "**/*.py", "*.md")
        directory: 検索開始ディレクトリ

    Returns:
        マッチしたファイルのリスト
    """
    try:
        directory = resolve_safe_path(directory)

        if not os.path.exists(directory):
            return f"Error: Directory not found: {directory}"

        search_pattern = os.path.join(directory, pattern)
        matches = glob_module.glob(search_pattern, recursive=True)

        # ソートして相対パスに変換
        matches.sort()
        if directory != '.':
            matches = [os.path.relpath(m, directory) for m in matches]

        if not matches:
            return f"No files matching '{pattern}' in {directory}"

        return f"Found {len(matches)} files:\n" + "\n".join(matches)

    except Exception as e:
        return f"Error searching files: {e}"


def tree(path: str = '.', max_depth: int = 3) -> str:
    """
    ディレクトリツリーを表示します。

    Args:
        path: 表示するディレクトリのパス
        max_depth: 最大深さ

    Returns:
        ツリー形式の文字列
    """
    def _tree(dir_path: str, prefix: str = '', depth: int = 0) -> List[str]:
        if depth >= max_depth:
            return []

        try:
            items = sorted(os.listdir(dir_path))
            items = [i for i in items if not i.startswith('.')]
        except PermissionError:
            return [f"{prefix}[Permission Denied]"]

        lines = []
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = '└── ' if is_last else '├── '
            full_path = os.path.join(dir_path, item)

            if os.path.isdir(full_path):
                lines.append(f"{prefix}{connector}{item}/")
                extension = '    ' if is_last else '│   '
                lines.extend(_tree(full_path, prefix + extension, depth + 1))
            else:
                lines.append(f"{prefix}{connector}{item}")

        return lines

    try:
        path = resolve_safe_path(path)

        if not os.path.exists(path):
            return f"Error: Path not found: {path}"

        lines = [f"{path}/"] + _tree(path)
        return "\n".join(lines)

    except Exception as e:
        return f"Error generating tree: {e}"


def file_info(path: str) -> str:
    """
    ファイルの詳細情報を取得します。

    Args:
        path: ファイルパス

    Returns:
        ファイル情報のJSON文字列
    """
    try:
        path = resolve_safe_path(path)

        if not os.path.exists(path):
            return f"Error: Path not found: {path}"

        stat = os.stat(path)
        info = {
            "path": os.path.abspath(path),
            "name": os.path.basename(path),
            "is_file": os.path.isfile(path),
            "is_dir": os.path.isdir(path),
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
            "created": stat.st_ctime,
        }

        if os.path.isfile(path):
            # ファイルの行数をカウント
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    info["line_count"] = sum(1 for _ in f)
            except Exception:
                info["line_count"] = None

        return json.dumps(info, indent=2, ensure_ascii=False)

    except Exception as e:
        return f"Error getting file info: {e}"
