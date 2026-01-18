"""Tests for TokenCache"""
import pytest
import tempfile
import shutil
import time
from pathlib import Path

from moco.core.token_cache import TokenCache


@pytest.fixture
def temp_dir():
    """一時ディレクトリを作成"""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def cache(temp_dir):
    """テスト用のTokenCache"""
    return TokenCache(cache_dir=str(temp_dir), max_size_mb=1, default_ttl=3600)


@pytest.fixture
def test_file(temp_dir):
    """テスト用ファイル"""
    f = temp_dir / "test_file.txt"
    f.write_text("test content")
    return f


class TestTokenCache:
    def test_set_and_get(self, cache, test_file):
        """キャッシュの保存と取得"""
        content = "cached content"
        cache.set(str(test_file), content)
        
        result = cache.get(str(test_file))
        
        assert result == content

    def test_get_nonexistent(self, cache):
        """存在しないファイル"""
        result = cache.get("/nonexistent/file.txt")
        
        assert result is None

    def test_cache_miss_after_file_change(self, cache, test_file):
        """ファイル変更後はキャッシュミス"""
        cache.set(str(test_file), "original")
        
        # ファイルを変更（mtime が変わる）
        time.sleep(0.1)
        test_file.write_text("modified content")
        
        result = cache.get(str(test_file))
        
        assert result is None  # キャッシュミス

    def test_ttl_expiration(self, temp_dir, test_file):
        """TTL切れでキャッシュミス"""
        cache = TokenCache(cache_dir=str(temp_dir), default_ttl=1)
        cache.set(str(test_file), "content")
        
        time.sleep(1.5)  # TTL超過
        
        result = cache.get(str(test_file))
        
        assert result is None

    def test_clear(self, cache, test_file):
        """キャッシュクリア"""
        cache.set(str(test_file), "content")
        cache.clear()
        
        result = cache.get(str(test_file))
        
        assert result is None

    def test_get_stats(self, cache, test_file):
        """統計情報の取得"""
        cache.set(str(test_file), "content")
        
        stats = cache.get_stats()
        
        assert stats["count"] == 1
        assert stats["total_size_bytes"] > 0
        assert stats["max_size_mb"] == 1

    def test_delete_by_path(self, cache, test_file):
        """パス指定での削除"""
        cache.set(str(test_file), "content")
        cache.delete_by_path(str(test_file))
        
        result = cache.get(str(test_file))
        
        assert result is None
