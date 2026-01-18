import os
from pathlib import Path

def resolve_safe_path(path: str) -> str:
    """
    パスを安全に解決する。
    作業ディレクトリ外へのアクセスを防ぐ。
    
    Args:
        path: 解決するパス（相対または絶対）
    
    Returns:
        解決された絶対パス
    
    Raises:
        PermissionError: 作業ディレクトリ外へのアクセスを試みた場合
    """
    working_dir = os.environ.get('MOCO_WORKING_DIRECTORY', os.getcwd())
    abs_working_dir = os.path.abspath(working_dir)
    
    # 絶対パスの場合はそのまま、相対パスの場合は作業ディレクトリからの相対
    if os.path.isabs(path):
        target_path = os.path.abspath(path)
    else:
        target_path = os.path.abspath(os.path.join(abs_working_dir, path))
    
    # 作業ディレクトリ配下かチェック
    if not target_path.startswith(abs_working_dir + os.sep) and target_path != abs_working_dir:
        raise PermissionError(f"Access outside of working directory is not allowed: {path}")
    
    return target_path

def get_working_directory() -> str:
    """作業ディレクトリを取得"""
    return os.environ.get('MOCO_WORKING_DIRECTORY', os.getcwd())
