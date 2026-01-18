import os
from pathlib import Path
import fnmatch
from collections import Counter
from typing import List, Dict, Optional

def get_project_context(path: str = None, depth: int = 2) -> str:
    """
    プロジェクトの構造、主要ファイル、統計情報を取得してMarkdown形式で返す。
    
    Args:
        path: 探索を開始するパス（Noneの場合はMOCO_WORKING_DIRECTORYまたはカレントディレクトリ）
        depth: ディレクトリツリーを表示する深さ
        
    Returns:
        プロジェクトのコンテキスト情報（Markdown形式）
    """
    if path is None:
        path = os.getenv('MOCO_WORKING_DIRECTORY', '.')
    start_path = Path(path).resolve()
    
    # MOCO_WORKING_DIRECTORY が明示的に設定されている場合は、
    # プロジェクトルート探索をスキップして、指定されたパスをそのまま使う
    if os.getenv('MOCO_WORKING_DIRECTORY'):
        root_path = start_path
    else:
        root_path = _find_project_root(start_path)
    ignore_patterns = _get_ignore_patterns(root_path)
    
    context = []
    context.append(f"# Project Context: {root_path.name}")
    context.append(f"Root: `{root_path}`\n")
    
    # 1. README Summary
    readme_summary = _get_readme_summary(root_path)
    if readme_summary:
        context.append("## README Summary")
        context.append(f"```markdown\n{readme_summary}\n```\n")
    
    # 2. Directory Structure
    context.append("## Directory Structure")
    tree = _generate_tree(root_path, root_path, depth, 0, ignore_patterns)
    if tree:
        context.append("```text")
        context.extend(tree)
        context.append("```\n")
    else:
        context.append("No visible directories found at this depth.\n")
    
    # 3. Configuration Files
    context.append("## Configuration Files")
    configs = _get_config_files(root_path)
    if configs:
        context.append(", ".join([f"`{c}`" for c in configs]) + "\n")
    else:
        context.append("No common configuration files found.\n")
        
    # 4. File Extension Statistics
    context.append("## File Extension Statistics")
    stats = _get_extension_stats(root_path, ignore_patterns)
    if stats:
        sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)
        stats_md = [f"- {ext if ext else '(no ext)'}: {count}" for ext, count in sorted_stats]
        context.extend(stats_md)
    else:
        context.append("No files found.")
    
    return "\n".join(context)

def _find_project_root(start_path: Path) -> Path:
    """
    親ディレクトリを遡り、プロジェクトルートのマーカーを探す。
    """
    markers = {".git", "pyproject.toml", "package.json", "setup.py", "Makefile", "requirements.txt", "go.mod", "Cargo.toml"}
    current = start_path
    # ルートディレクトリまで、または最大10階層遡る
    for _ in range(10):
        if any((current / marker).exists() for marker in markers):
            return current
        if current.parent == current:
            break
        current = current.parent
    return start_path

def _get_ignore_patterns(root_path: Path) -> List[str]:
    """
    デフォルトの無視パターンと.gitignoreからパターンを取得する。
    """
    patterns = [
        ".git", "__pycache__", "node_modules", ".venv", "venv", 
        "dist", "build", ".DS_Store", "*.pyc", ".idea", ".vscode",
        "*.egg-info", ".mypy_cache", ".pytest_cache"
    ]
    gitignore = root_path / ".gitignore"
    if gitignore.exists():
        try:
            with open(gitignore, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # パターンの末尾の / を削除（fnmatch用）
                        patterns.append(line.rstrip("/"))
        except Exception:
            pass
    return list(set(patterns))

def _is_ignored(path: Path, root_path: Path, patterns: List[str]) -> bool:
    """
    パスが無視パターンに合致するか判定する。
    """
    name = path.name
    # 高速化: 頻出する無視ディレクトリの完全一致チェック
    common_ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
    if name in common_ignored:
        return True

    try:
        # as_posix() を使用して Windows 環境でもスラッシュ区切りで統一
        rel_path = path.relative_to(root_path).as_posix()
    except ValueError:
        return False
    
    for pattern in patterns:
        # 名前のみでマッチ
        if fnmatch.fnmatch(name, pattern):
            return True
        # 相対パスでマッチ
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # ディレクトリ配下のマッチを模倣
        if pattern in rel_path.split('/'):
            return True
            
    return False

def _generate_tree(path: Path, root_path: Path, max_depth: int, current_depth: int, ignore_patterns: List[str]) -> List[str]:
    """
    ディレクトリツリーを再帰的に生成する。
    """
    if current_depth > max_depth:
        return []
    
    lines = []
    try:
        # ディレクトリを先に、名前順でソート
        items = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        for item in items:
            if _is_ignored(item, root_path, ignore_patterns):
                continue
            
            indent = "  " * current_depth
            prefix = "📁 " if item.is_dir() else "📄 "
            lines.append(f"{indent}{prefix}{item.name}")
            
            if item.is_dir():
                lines.extend(_generate_tree(item, root_path, max_depth, current_depth + 1, ignore_patterns))
    except (PermissionError, FileNotFoundError):
        pass
    return lines

def _get_readme_summary(root_path: Path) -> str:
    """
    READMEファイルの冒頭部分を取得する（効率化のため直接存在確認）。
    """
    # 一般的なREADMEのバリエーション
    candidates = [
        "README.md", "README.markdown", "README.txt", "README",
        "readme.md", "readme.markdown", "readme.txt", "readme",
        "Readme.md", "Readme.txt"
    ]
    for name in candidates:
        p = root_path / name
        if p.exists() and p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    lines = []
                    for _ in range(10): # 10行程度取得
                        line = f.readline()
                        if not line:
                            break
                        lines.append(line)
                    summary = "".join(lines).strip()
                    return summary + "\n..." if summary else ""
            except Exception:
                pass
    return ""

def _get_config_files(root_path: Path) -> List[str]:
    """
    主要な設定ファイルの存在を確認する。
    """
    config_markers = [
        "pyproject.toml", "requirements.txt", "package.json", 
        "docker-compose.yml", "Dockerfile", "Makefile",
        "setup.py", "tox.ini", ".env.example", "tsconfig.json",
        "go.mod", "Cargo.toml", "composer.json", "Gemfile"
    ]
    found = []
    for marker in config_markers:
        if (root_path / marker).exists():
            found.append(marker)
    return found

def _get_extension_stats(root_path: Path, ignore_patterns: List[str], max_depth: int = 5) -> Dict[str, int]:
    """
    拡張子ごとのファイル数を集計する（パフォーマンスのため深さを制限）。
    """
    ext_counter = Counter()
    
    def _scan(path: Path, current_depth: int):
        if current_depth > max_depth:
            return
        try:
            for item in path.iterdir():
                if _is_ignored(item, root_path, ignore_patterns):
                    continue
                if item.is_file():
                    ext = item.suffix.lower()
                    ext_counter[ext] += 1
                elif item.is_dir():
                    _scan(item, current_depth + 1)
        except (PermissionError, FileNotFoundError):
            pass
            
    _scan(root_path, 0)
    return dict(ext_counter)

if __name__ == "__main__":
    # 簡易的な動作確認
    print(get_project_context())
