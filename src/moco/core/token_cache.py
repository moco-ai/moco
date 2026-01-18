import os
import hashlib
import time
import json
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

class TokenCache:
    """
    moco のトークンキャッシュ機能。
    長いコンテキスト（ファイル内容等）をローカルにキャッシュし、
    ファイルが変更（mtime）されていない場合はキャッシュを利用することで
    読み取り速度の向上やトークン節約に寄与する。
    
    保存先: ~/.moco/cache/
    """
    
    def __init__(
        self, 
        cache_dir: Optional[str] = None, 
        max_size_mb: int = 100,
        default_ttl: int = 3600
    ):
        if cache_dir is None:
            self.cache_dir = Path.home() / ".moco" / "cache"
        else:
            self.cache_dir = Path(cache_dir)
            
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.default_ttl = default_ttl
        
        # ディレクトリ作成
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
    def _compute_key(self, path: str, mtime: float) -> str:
        """キャッシュキーを生成（パス + mtime のハッシュ）"""
        key_str = f"{os.path.abspath(path)}:{mtime}"
        return hashlib.sha256(key_str.encode()).hexdigest()
        
    def get(self, path: str) -> Optional[str]:
        """
        ファイル内容をキャッシュから取得する（生データ）。
        """
        if not os.path.isfile(path):
            return None
            
        try:
            mtime = os.path.getmtime(path)
            key = self._compute_key(path, mtime)
            cache_file = self.cache_dir / f"{key}.cache"
            meta_file = self.cache_dir / f"{key}.meta"
            
            if not cache_file.exists() or not meta_file.exists():
                return None
                
            # メタデータ確認（TTL）
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                
            expires_at = meta.get("expires_at", 0)
            if time.time() > expires_at:
                self.delete_entry(key)
                return None
                
            # キャッシュヒット
            with open(cache_file, "r", encoding="utf-8") as f:
                content = f.read()
                
            # アクセス時刻を更新（LRU用）
            try:
                cache_file.touch()
            except Exception:
                pass
            return content
            
        except Exception:
            return None
            
    def set(self, path: str, content: str, ttl: Optional[int] = None):
        """ファイル内容（生データ）をキャッシュに保存する"""
        if not os.path.isfile(path):
            return
            
        try:
            abs_path = os.path.abspath(path)
            mtime = os.path.getmtime(abs_path)
            key = self._compute_key(abs_path, mtime)
            cache_file = self.cache_dir / f"{key}.cache"
            meta_file = self.cache_dir / f"{key}.meta"
            
            ttl = ttl if ttl is not None else self.default_ttl
            
            # キャッシュファイル書き込み
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(content)
                
            # メタデータ書き込み
            meta = {
                "path": abs_path,
                "mtime": mtime,
                "created_at": time.time(),
                "expires_at": time.time() + ttl,
                "size_bytes": len(content.encode("utf-8"))
            }
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta, f)
                
            # サイズ制限のチェック（書き込みごとに全スキャンしないよう配慮）
            # ここではシンプルに呼び出し、内部でサイズチェック
            self._cleanup_if_needed()
            
        except Exception:
            pass
            
    def delete_by_path(self, path: str):
        """パスに基づいてキャッシュを無効化（ファイル変更時用）"""
        try:
            abs_path = os.path.abspath(path)
            # 全てのメタデータをチェックして該当するパスのキーを削除
            # パフォーマンスのため、一致する可能性のあるファイルを走査
            for meta_file in self.cache_dir.glob("*.meta"):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        if meta.get("path") == abs_path:
                            self.delete_entry(meta_file.stem)
                except Exception:
                    continue
        except Exception:
            pass

    def delete_entry(self, key: str):
        """特定のエントリを削除"""
        try:
            (self.cache_dir / f"{key}.cache").unlink(missing_ok=True)
            (self.cache_dir / f"{key}.meta").unlink(missing_ok=True)
        except Exception:
            pass
            
    def clear(self):
        """全てのキャッシュを安全にクリア"""
        if not self.cache_dir.exists():
            return
        for item in self.cache_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception:
                pass
            
    def get_stats(self) -> Dict[str, Any]:
        """キャッシュ統計を取得"""
        total_size = 0
        count = 0
        files = list(self.cache_dir.glob("*.cache"))
        for f in files:
            total_size += f.stat().st_size
            count += 1
            
        return {
            "count": count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "max_size_mb": round(self.max_size_bytes / (1024 * 1024), 2),
            "cache_dir": str(self.cache_dir)
        }
        
    def _cleanup_if_needed(self):
        """LRU方式で古いキャッシュを削除して、サイズ制限内に収める"""
        files = []
        current_size = 0
        for f in self.cache_dir.glob("*.cache"):
            stat = f.stat()
            current_size += stat.st_size
            files.append((f, stat.st_atime))
            
        if current_size <= self.max_size_bytes:
            return
            
        # アクセス時刻（st_atime）の昇順（古い順）にソート
        files.sort(key=lambda x: x[1])
        
        for f, _ in files:
            if current_size <= self.max_size_bytes:
                break
            
            size = f.stat().st_size
            key = f.stem
            self.delete_entry(key)
            current_size -= size
