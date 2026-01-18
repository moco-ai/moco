"""Tests for UsageStore"""
import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

from moco.storage.usage_store import UsageStore


@pytest.fixture
def temp_dir():
    """一時ディレクトリを作成"""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def store(temp_dir):
    """テスト用のUsageStore"""
    return UsageStore(db_path=temp_dir / "test_usage.db")


class TestUsageStore:
    def test_record_and_get_session_usage(self, store):
        """使用量の記録と取得"""
        store.record_usage(
            provider="gemini",
            model="gemini-2.0-flash",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            session_id="session-123"
        )
        
        usage = store.get_session_usage("session-123")
        
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50
        assert usage["total_tokens"] == 150
        assert usage["cost_usd"] == 0.001

    def test_get_usage_summary(self, store):
        """日別サマリーの取得"""
        now = datetime.now(timezone.utc)
        
        store.record_usage("gemini", "flash", 100, 50, 0.001, timestamp=now)
        store.record_usage("gemini", "flash", 200, 100, 0.002, timestamp=now)
        
        summary = store.get_usage_summary(days=7)
        
        assert len(summary) >= 1
        today_summary = summary[-1]
        assert today_summary["total_tokens"] == 450  # (100+50) + (200+100)

    def test_get_breakdown_by_provider(self, store):
        """プロバイダ別内訳"""
        store.record_usage("gemini", "flash", 100, 50, 0.001)
        store.record_usage("openai", "gpt-4", 200, 100, 0.01)
        
        breakdown = store.get_breakdown(days=7, group_by="provider")
        
        assert len(breakdown) == 2
        labels = [b["label"] for b in breakdown]
        assert "gemini" in labels
        assert "openai" in labels

    def test_get_breakdown_by_model(self, store):
        """モデル別内訳"""
        store.record_usage("gemini", "flash", 100, 50, 0.001)
        store.record_usage("gemini", "pro", 200, 100, 0.01)
        
        breakdown = store.get_breakdown(days=7, group_by="model")
        
        assert len(breakdown) == 2

    def test_empty_session_usage(self, store):
        """存在しないセッションの使用量"""
        usage = store.get_session_usage("nonexistent")
        
        assert usage["total_tokens"] == 0
        assert usage["cost_usd"] == 0.0

    def test_get_recent_usage(self, store):
        """最近の使用量ログ取得"""
        store.record_usage("gemini", "flash", 100, 50, 0.001)
        store.record_usage("openai", "gpt-4", 200, 100, 0.01)
        
        recent = store.get_recent_usage(limit=10)
        
        assert len(recent) == 2
