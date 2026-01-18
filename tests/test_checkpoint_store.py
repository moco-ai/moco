"""Tests for CheckpointStore"""
import pytest
import tempfile
import shutil
from pathlib import Path

from moco.storage.checkpoint_store import CheckpointStore


@pytest.fixture
def temp_dir():
    """一時ディレクトリを作成"""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def store(temp_dir):
    """テスト用のCheckpointStore"""
    return CheckpointStore(checkpoints_dir=temp_dir)


class TestCheckpointStore:
    def test_save_and_get_checkpoint(self, store):
        """チェックポイントの保存と取得"""
        store.save_checkpoint(
            name="test-checkpoint",
            session_id="session-123",
            profile="cursor",
            working_dir="/tmp/project"
        )
        
        checkpoint = store.get_checkpoint("test-checkpoint")
        assert checkpoint is not None
        assert checkpoint["name"] == "test-checkpoint"
        assert checkpoint["session_id"] == "session-123"
        assert checkpoint["profile"] == "cursor"
        assert checkpoint["working_dir"] == "/tmp/project"
        assert "timestamp" in checkpoint

    def test_list_checkpoints(self, store):
        """チェックポイント一覧の取得"""
        store.save_checkpoint("cp1", "s1", "p1")
        store.save_checkpoint("cp2", "s2", "p2")
        
        checkpoints = store.list_checkpoints()
        assert len(checkpoints) == 2
        names = [c["name"] for c in checkpoints]
        assert "cp1" in names
        assert "cp2" in names

    def test_delete_checkpoint(self, store):
        """チェックポイントの削除"""
        store.save_checkpoint("to-delete", "s1", "p1")
        assert store.get_checkpoint("to-delete") is not None
        
        result = store.delete_checkpoint("to-delete")
        assert result is True
        assert store.get_checkpoint("to-delete") is None

    def test_delete_nonexistent(self, store):
        """存在しないチェックポイントの削除"""
        result = store.delete_checkpoint("nonexistent")
        assert result is False

    def test_safe_path_prevents_traversal(self, store):
        """パストラバーサル攻撃の防止"""
        # 危険な文字を含む名前はサニタイズされる
        store.save_checkpoint("../../../etc/passwd", "s1", "p1")
        
        # ファイルは安全なパスに保存される
        checkpoint = store.get_checkpoint("etcpasswd")
        assert checkpoint is not None

    def test_invalid_name_raises_error(self, store):
        """無効な名前でエラー"""
        with pytest.raises(ValueError):
            store._safe_path("   ")  # 空白のみ
