# -*- coding: utf-8 -*-
"""Lintツール"""
import os
import subprocess
import re
from typing import List, Optional
from moco.utils.path import resolve_safe_path


# ESLint Unix format: path:line:column: message
ESLINT_UNIX_RE = re.compile(r'^(.+):(\d+):(\d+): (.*)$')


def read_lints(paths: Optional[List[str]] = None) -> str:
    """
    指定されたパスに対してLintを実行し、結果を返します。

    Args:
        paths: Lintを実行するパスのリスト。Noneの場合はカレントディレクトリ。

    Returns:
        Lintの結果（エラーメッセージのリスト、または 'No lint errors found'）
    """
    if paths is None:
        paths = ["."]

    resolved_paths = [resolve_safe_path(p) for p in paths]
    all_errors = []

    for path in resolved_paths:
        if not os.path.exists(path):
            all_errors.append(f"Error: Path not found: {path}")
            continue

        # 拡張子やディレクトリ構造に基づいて適切なリンターを選択
        if os.path.isfile(path):
            errors = _run_linter_for_file(path)
            all_errors.extend(errors)
        elif os.path.isdir(path):
            errors = _run_linter_for_dir(path)
            all_errors.extend(errors)

    if not all_errors:
        return "No lint errors found"

    return "\n".join(all_errors)


def _run_linter_for_file(file_path: str) -> List[str]:
    """特定のファイルに対して適切なリンターを実行する"""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".py":
        return _run_python_linter(file_path)
    elif ext in [".js", ".jsx", ".ts", ".tsx"]:
        return _run_javascript_linter(file_path)

    return []


def _run_linter_for_dir(dir_path: str) -> List[str]:
    """ディレクトリ全体に対してリンターを実行する"""
    errors = []

    # ファイルの存在チェックを1回の走査で行う
    has_py = False
    has_js = False
    for root, _, files in os.walk(dir_path):
        if not has_py and any(f.endswith('.py') for f in files):
            has_py = True
        if not has_js and any(f.endswith(('.js', '.jsx', '.ts', '.tsx')) for f in files):
            has_js = True
        if has_py and has_js:
            break

    if has_py:
        errors.extend(_run_python_linter(dir_path))

    if has_js:
        errors.extend(_run_javascript_linter(dir_path))

    return errors


def _run_python_linter(target: str) -> List[str]:
    """Pythonのリンター（ruffを優先、次点にflake8）を実行する"""
    # ruff
    try:
        # ruff check --format concise を使用
        # オプションインジェクション対策で "--" を使用
        result = subprocess.run(
            ["ruff", "check", "--format", "concise", "--", target],
            capture_output=True,
            text=True,
            check=False
        )
        if result.stdout:
            return result.stdout.strip().splitlines()
        if result.returncode != 0 and result.stderr:
            return [f"Linter Error (ruff): {result.stderr.strip()}"]
    except FileNotFoundError:
        pass

    # flake8
    try:
        result = subprocess.run(
            ["flake8", "--format=%(path)s:%(row)d:%(col)d: %(text)s", "--", target],
            capture_output=True,
            text=True,
            check=False
        )
        if result.stdout:
            return result.stdout.strip().splitlines()
        if result.returncode != 0 and result.stderr:
            return [f"Linter Error (flake8): {result.stderr.strip()}"]
    except FileNotFoundError:
        pass

    return []


def _run_javascript_linter(target: str) -> List[str]:
    """JavaScript/TypeScriptのリンター (eslint) を実行する"""
    try:
        # v9以降でも --format unix は標準搭載
        # オプションインジェクション対策で "--" を使用
        result = subprocess.run(
            ["npx", "eslint", "--format", "unix", "--", target],
            capture_output=True,
            text=True,
            check=False
        )

        stdout = result.stdout.strip()
        if stdout:
            lines = stdout.splitlines()
            # unix形式は path:line:col: message 形式
            # サマリー行（"X problems"）を除外
            return [line for line in lines if ESLINT_UNIX_RE.match(line)]

        if result.returncode != 0 and result.stderr:
            return [f"Linter Error (eslint): {result.stderr.strip()}"]

    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    return []
