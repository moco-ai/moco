"""Tests for patch_viewer"""
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

from moco.ui.patch_viewer import preview_patch, save_patch, get_patch_dir


@pytest.fixture
def temp_dir():
    """一時ディレクトリを作成"""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


class TestPreviewPatch:
    @patch("moco.ui.patch_viewer.console")
    def test_no_changes(self, mock_console):
        """変更がない場合"""
        result = preview_patch("test.py", "content", "content")
        
        assert result == "y"  # 変更なしは自動承認

    @patch("moco.ui.patch_viewer.console")
    def test_with_changes(self, mock_console):
        """変更がある場合"""
        mock_console.input.return_value = "y"
        
        result = preview_patch(
            "test.py",
            "old content",
            "new content"
        )
        
        assert result == "y"
        mock_console.print.assert_called()  # テーブルが表示される


class TestSavePatch:
    def test_save_patch_creates_file(self, temp_dir):
        """パッチファイルの作成"""
        with patch("moco.ui.patch_viewer.get_patch_dir", return_value=temp_dir):
            path = save_patch(
                "test.py",
                "old content\n",
                "new content\n",
                patch_name="test.patch"
            )
        
        assert path.exists()
        content = path.read_text()
        assert "---" in content or "+++ " in content or "-old" in content

    def test_save_patch_auto_name(self, temp_dir):
        """自動命名のパッチファイル"""
        with patch("moco.ui.patch_viewer.get_patch_dir", return_value=temp_dir):
            path = save_patch(
                "src/main.py",
                "old\n",
                "new\n"
            )
        
        assert path.exists()
        assert "src_main.py" in path.name or "main" in path.name


class TestGetPatchDir:
    def test_creates_directory(self, temp_dir):
        """ディレクトリが存在しない場合に作成"""
        with patch("moco.ui.patch_viewer.Path") as mock_path:
            mock_instance = mock_path.return_value
            mock_instance.mkdir = lambda **kwargs: None
            
            get_patch_dir()
            
            mock_path.assert_called_with(".moco/patches")
