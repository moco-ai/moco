"""
承認機能のテスト

moco/ui/api.py の承認関連エンドポイントのテスト
- /api/sessions/{session_id}/approve: 承認要求の送信
- /api/approvals/{approval_id}/respond: 承認/拒否のレスポンス
"""

import pytest
import asyncio
import threading
import uuid
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

# APIモジュールのインポート
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from moco.ui.api import app, session_logger, pending_approvals, pending_approvals_lock, approval_manager


class TestApprovalAPI:
    """承認APIのテストクラス"""

    @pytest.fixture
    def client(self):
        """FastAPIテストクライアント"""
        return TestClient(app)

    @pytest.fixture
    def mock_session(self):
        """モックセッション"""
        return {
            "session_id": "test-session-123",
            "title": "Test Session",
            "profile": "development",
            "created_at": "2026-01-08T10:00:00"
        }

    @pytest.fixture
    def setup_approval(self, mock_session):
        """承認要求のセットアップ"""
        session_id = mock_session["session_id"]
        approval_id = str(uuid.uuid4())
        
        # 承認要求を登録
        with pending_approvals_lock:
            event = threading.Event()
            pending_approvals[approval_id] = {
                "event": event,
                "decision": False,
                "tool": "test_tool",
                "args": {"param": "value"},
                "session_id": session_id
            }
        
        return approval_id, session_id

    # === 1. 承認要求の送信 ===

    def test_create_approval_success(self, client, mock_session):
        """承認要求の作成（正常系）"""
        # セッション存在をモック
        with patch.object(session_logger, 'get_session', return_value=mock_session):
            response = client.post(
                f"/api/sessions/{mock_session['session_id']}/approve",
                json={"tool": "test_tool", "args": {"param": "value"}}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "approval_id" in data
        assert data["tool"] == "test_tool"
        assert data["args"] == {"param": "value"}
        
        # 承認要求が登録されているか確認
        approval_id = data["approval_id"]
        with pending_approvals_lock:
            assert approval_id in pending_approvals
            assert pending_approvals[approval_id]["tool"] == "test_tool"
            assert pending_approvals[approval_id]["args"] == {"param": "value"}
            assert pending_approvals[approval_id]["decision"] == False

    def test_create_approval_session_not_found(self, client):
        """承認要求の作成 - セッション不存在"""
        # セッション不存在をモック
        with patch.object(session_logger, 'get_session', return_value=None):
            response = client.post(
                "/api/sessions/non-existent-session/approve",
                json={"tool": "test_tool"}
            )
        
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    def test_create_approval_without_args(self, client, mock_session):
        """承認要求の作成 - argsなし"""
        with patch.object(session_logger, 'get_session', return_value=mock_session):
            response = client.post(
                f"/api/sessions/{mock_session['session_id']}/approve",
                json={"tool": "test_tool"}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["args"] == {}

    def test_create_approval_invalid_tool(self, client, mock_session):
        """承認要求の作成 - toolパラメータ必須"""
        with patch.object(session_logger, 'get_session', return_value=mock_session):
            response = client.post(
                f"/api/sessions/{mock_session['session_id']}/approve",
                json={"args": {"param": "value"}}  # toolなし
            )
        
        assert response.status_code == 422  # Validation error

    # === 2. 承認/拒否のレスポンス処理 ===

    def test_respond_approval_approve(self, client, setup_approval):
        """承認要求への承認レスポンス"""
        approval_id, _ = setup_approval
        
        response = client.post(
            f"/api/approvals/{approval_id}/respond",
            json={"approved": True}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == True
        
        # 承認要求のdecisionがTrueになっているか確認
        with pending_approvals_lock:
            assert pending_approvals[approval_id]["decision"] == True
            assert pending_approvals[approval_id]["event"].is_set()

    def test_respond_approval_reject(self, client, setup_approval):
        """承認要求への拒否レスポンス"""
        approval_id, _ = setup_approval
        
        response = client.post(
            f"/api/approvals/{approval_id}/respond",
            json={"approved": False}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == False
        
        # 承認要求のdecisionがFalseになっているか確認
        with pending_approvals_lock:
            assert pending_approvals[approval_id]["decision"] == False
            assert pending_approvals[approval_id]["event"].is_set()

    def test_respond_approval_twice(self, client, setup_approval):
        """承認要求への二重レスポンス"""
        approval_id, _ = setup_approval
        
        # 1回目
        response1 = client.post(
            f"/api/approvals/{approval_id}/respond",
            json={"approved": True}
        )
        assert response1.status_code == 200
        
        # 2回目
        response2 = client.post(
            f"/api/approvals/{approval_id}/respond",
            json={"approved": False}
        )
        assert response2.status_code == 200
        # 2回目のレスポンスも成功する（上書きされる）

    # === 3. エラーケース ===

    def test_respond_approval_not_found(self, client):
        """無効な承認IDでのレスポンス"""
        invalid_approval_id = "non-existent-approval-id"
        
        response = client.post(
            f"/api/approvals/{invalid_approval_id}/respond",
            json={"approved": True}
        )
        
        assert response.status_code == 404
        assert "Approval request not found or expired" in response.json()["detail"]

    def test_respond_approval_expired(self, client, setup_approval):
        """承認要求の期限切れ"""
        approval_id, _ = setup_approval
        
        # 承認要求を削除（期限切れをシミュレート）
        with pending_approvals_lock:
            if approval_id in pending_approvals:
                del pending_approvals[approval_id]
        
        response = client.post(
            f"/api/approvals/{approval_id}/respond",
            json={"approved": True}
        )
        
        assert response.status_code == 404

    def test_create_approval_missing_required_fields(self, client, mock_session):
        """承認要求作成 - 必須フィールド欠損"""
        with patch.object(session_logger, 'get_session', return_value=mock_session):
            # toolフィールドなし
            response = client.post(
                f"/api/sessions/{mock_session['session_id']}/approve",
                json={}  # toolなし
            )
            assert response.status_code == 422

    def test_respond_approval_missing_approved_field(self, client, setup_approval):
        """承認レスポンス - approvedフィールド欠損"""
        approval_id, _ = setup_approval
        
        response = client.post(
            f"/api/approvals/{approval_id}/respond",
            json={}  # approvedなし
        )
        
        assert response.status_code == 422


class TestApprovalManager:
    """ApprovalManagerの単体テスト"""

    @pytest.fixture
    def approval_mgr(self):
        """新しいApprovalManagerインスタンス"""
        return approval_manager

    @pytest.mark.asyncio
    async def test_create_and_wait_for_decision_approve(self, approval_mgr):
        """承認要求作成と承認待機（承認）"""
        approval_id = str(uuid.uuid4())
        session_id = "test-session"
        
        # 承認要求作成（WebSocket送信はモック）
        with patch.object(approval_mgr, '_send_to_session', return_value=True):
            result = await approval_mgr.create_approval_request(
                approval_id, "test_tool", {"param": "value"}, session_id
            )
            assert result == True
        
        # 別タスクで承認応答
        async def respond():
            await asyncio.sleep(0.1)
            await approval_mgr.respond_to_approval(approval_id, True)
        
        # 承認待機
        respond_task = asyncio.create_task(respond())
        decision = await approval_mgr.wait_for_decision(approval_id, timeout=1.0)
        
        assert decision == True
        assert approval_id not in approval_mgr.pending_approvals  # クリーンアップ済み

    @pytest.mark.asyncio
    async def test_create_and_wait_for_decision_reject(self, approval_mgr):
        """承認要求作成と承認待機（拒否）"""
        approval_id = str(uuid.uuid4())
        session_id = "test-session"
        
        with patch.object(approval_mgr, '_send_to_session', return_value=True):
            await approval_mgr.create_approval_request(
                approval_id, "test_tool", {}, session_id
            )
        
        # 拒否応答
        async def respond():
            await asyncio.sleep(0.1)
            await approval_mgr.respond_to_approval(approval_id, False)
        
        respond_task = asyncio.create_task(respond())
        decision = await approval_mgr.wait_for_decision(approval_id, timeout=1.0)
        
        assert decision == False

    @pytest.mark.asyncio
    async def test_wait_for_decision_timeout(self, approval_mgr):
        """承認待機のタイムアウト"""
        approval_id = str(uuid.uuid4())
        session_id = "test-session"
        
        with patch.object(approval_mgr, '_send_to_session', return_value=True):
            await approval_mgr.create_approval_request(
                approval_id, "test_tool", {}, session_id
            )
        
        # 応答せずに待機（タイムアウト）
        decision = await approval_mgr.wait_for_decision(approval_id, timeout=0.1)
        
        # タイムアウト時はFalse（却下扱い）
        assert decision == False

    @pytest.mark.asyncio
    async def test_respond_to_nonexistent_approval(self, approval_mgr):
        """存在しない承認要求への応答"""
        result = await approval_mgr.respond_to_approval("non-existent", True)
        assert result == False

    @pytest.mark.asyncio
    async def test_wait_for_nonexistent_approval(self, approval_mgr):
        """存在しない承認要求の待機"""
        decision = await approval_mgr.wait_for_decision("non-existent", timeout=0.1)
        assert decision == False

    @pytest.mark.asyncio
    async def test_websocket_registration(self, approval_mgr):
        """WebSocket登録/解除"""
        session_id = "test-session"
        
        # モックWebSocket
        class MockWebSocket:
            pass
        
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        
        # 登録
        await approval_mgr.register_websocket(session_id, ws1)
        await approval_mgr.register_websocket(session_id, ws2)
        
        assert session_id in approval_mgr.session_websockets
        assert len(approval_mgr.session_websockets[session_id]) == 2
        
        # 解除
        await approval_mgr.unregister_websocket(session_id, ws1)
        assert len(approval_mgr.session_websockets[session_id]) == 1
        
        # 全解除
        await approval_mgr.unregister_websocket(session_id, ws2)
        assert session_id not in approval_mgr.session_websockets

    @pytest.mark.asyncio
    async def test_send_to_session_no_websocket(self, approval_mgr):
        """WebSocketがないセッションへの送信"""
        result = await approval_mgr._send_to_session(
            "non-existent-session",
            {"type": "test"}
        )
        assert result == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
