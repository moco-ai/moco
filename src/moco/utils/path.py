import os
from pathlib import Path

def resolve_safe_path(path: str) -> str:
    """
    パスを安全に解決する。
    作業ディレクトリ外へのアクセス（シンボリックリンクを含む）を防ぐ。
    
    Args:
        path: 解決するパス（相対または絶対）
    
    Returns:
        解決された絶対パス
    
    Raises:
        PermissionError: 作業ディレクトリ外へのアクセスを試みた場合
    """
    working_dir = os.environ.get('MOCO_WORKING_DIRECTORY', os.getcwd())
    abs_working_dir = Path(working_dir).resolve()
    
    try:
        # 入力パスを作業ディレクトリ基準で結合し、完全に解決（シンボリックリンクも解消）
        if os.path.isabs(path):
            target_path = Path(path).resolve()
        else:
            target_path = (abs_working_dir / path).resolve()
    except (OSError, RuntimeError):
        # 存在しないパスでも解決を試みるが、失敗した場合は偽装の可能性があるため拒否
        raise PermissionError(f"Invalid path or circular reference: {path}")

    # 作業ディレクトリ配下かチェック (relative_to は範囲外だと ValueError を投げる)
    try:
        target_path.relative_to(abs_working_dir)
    except ValueError:
        if target_path != abs_working_dir:
            raise PermissionError(f"Access outside of working directory is not allowed: {path}")
    
    return str(target_path)

def get_working_directory() -> str:
    """作業ディレクトリを取得"""
    return os.environ.get('MOCO_WORKING_DIRECTORY', os.getcwd())
