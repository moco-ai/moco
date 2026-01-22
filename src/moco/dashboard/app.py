"""
moco ダッシュボード FastAPI アプリケーション。

セッション、コスト、エージェントの状態を可視化するWeb UIを提供する。
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Set
from collections import defaultdict

logger = logging.getLogger(__name__)

# FastAPI（オプション依存）
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse
    from fastapi.templating import Jinja2Templates
    from moco.common.schemas import LogEntry
    from moco.common.errors import setup_exception_handlers
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPI = None
    Jinja2Templates = None

# Uvicorn（オプション依存）
try:
    import uvicorn
    UVICORN_AVAILABLE = True
except ImportError:
    UVICORN_AVAILABLE = False


@dataclass
class DashboardConfig:
    """ダッシュボード設定"""
    title: str = "moco Dashboard"
    refresh_interval: int = 5  # 秒
    max_log_lines: int = 1000
    enable_websocket: bool = True
    session_db_path: Optional[str] = None
    
    def __post_init__(self):
        if self.session_db_path is None:
            self.session_db_path = os.getenv("SESSION_DB_PATH", "data/sessions.db")


class LogBuffer:
    """リングバッファ形式のログ保持"""
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._logs: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
    
    async def add(self, log_entry: Dict[str, Any]) -> None:
        async with self._lock:
            self._logs.append(log_entry)
            if len(self._logs) > self.max_size:
                self._logs = self._logs[-self.max_size:]
    
    async def get_recent(self, count: int = 100) -> List[Dict[str, Any]]:
        async with self._lock:
            return self._logs[-count:]
    
    async def clear(self) -> None:
        async with self._lock:
            self._logs.clear()


class WebSocketManager:
    """WebSocket接続管理"""
    
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info(f"WebSocket connected. Total: {len(self._connections)}")
    
    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self._connections)}")
    
    async def broadcast(self, message: Dict[str, Any]) -> None:
        """全接続にブロードキャスト"""
        async with self._lock:
            dead_connections = set()
            for ws in self._connections:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead_connections.add(ws)
            self._connections -= dead_connections
    
    @property
    def connection_count(self) -> int:
        return len(self._connections)


def create_dashboard_app(
    cost_tracker: Optional[Any] = None,
    session_logger: Optional[Any] = None,
    config: Optional[DashboardConfig] = None,
) -> "FastAPI":
    """
    ダッシュボード FastAPI アプリを作成する。
    
    Args:
        cost_tracker: CostTracker インスタンス（オプション）
        session_logger: SessionLogger インスタンス（オプション）
        config: ダッシュボード設定
        
    Returns:
        FastAPI アプリケーション
        
    Raises:
        ImportError: FastAPI がインストールされていない場合
    """
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI is required for dashboard. "
            "Install with: pip install fastapi uvicorn"
        )
    
    config = config or DashboardConfig()
    
    app = FastAPI(
        title=config.title,
        description="moco フレームワーク監視ダッシュボード",
        version="1.0.0",
    )
    
    # 例外ハンドラーの設定
    setup_exception_handlers(app)
    
    # テンプレートの設定
    template_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(template_dir))
    
    # 状態管理
    log_buffer = LogBuffer(max_size=config.max_log_lines)
    ws_manager = WebSocketManager()
    
    # SessionLogger の遅延初期化
    _session_logger = session_logger
    
    def get_session_logger():
        nonlocal _session_logger
        if _session_logger is None:
            try:
                from moco.storage import SessionLogger
                _session_logger = SessionLogger(db_path=config.session_db_path)
            except Exception as e:
                logger.warning(f"Failed to initialize SessionLogger: {e}")
        return _session_logger
    
    # CostTracker の遅延初期化
    _cost_tracker = cost_tracker
    
    def get_cost_tracker():
        nonlocal _cost_tracker
        if _cost_tracker is None:
            try:
                from moco.core import get_cost_tracker
                _cost_tracker = get_cost_tracker()
            except Exception:
                pass
        return _cost_tracker
    
    # ==========================================================================
    # ルート
    # ==========================================================================
    
    @app.get("/", response_class=HTMLResponse)
    async def dashboard_home(request: Request):
        """ダッシュボード UI"""
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "title": config.title,
                "refresh_interval": config.refresh_interval
            }
        )
    
    @app.get("/api/sessions")
    async def list_sessions(
        limit: int = Query(default=20, ge=1, le=100),
        profile: Optional[str] = None,
    ):
        """セッション一覧"""
        sl = get_session_logger()
        if sl is None:
            return {"sessions": [], "error": "SessionLogger not available"}
        
        try:
            sessions = sl.list_sessions(limit=limit, profile=profile)
            return {"sessions": sessions}
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return {"sessions": [], "error": str(e)}
    
    @app.get("/api/sessions/{session_id}")
    async def get_session_detail(session_id: str):
        """セッション詳細"""
        sl = get_session_logger()
        if sl is None:
            raise HTTPException(status_code=503, detail="SessionLogger not available")
        
        try:
            # セッション情報取得
            sessions = sl.list_sessions(limit=1000)
            session_info = next(
                (s for s in sessions if s["session_id"] == session_id),
                None
            )
            if session_info is None:
                raise HTTPException(status_code=404, detail="Session not found")
            
            # 会話履歴取得
            history = sl.get_history(session_id)
            
            # 要約取得
            summary = sl.get_summary(session_id)
            
            return {
                "session": session_info,
                "history": history,
                "summary": summary,
                "message_count": len(history),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get session detail: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/costs")
    async def get_costs():
        """コスト集計"""
        ct = get_cost_tracker()
        if ct is None:
            return {
                "total_cost": 0,
                "total_tokens": {"input": 0, "output": 0, "total": 0},
                "by_model": {},
                "by_session": {},
                "by_agent": {},
                "record_count": 0,
            }
        
        try:
            summary = ct.get_summary()
            return {
                "total_cost": summary.total_cost,
                "total_tokens": {
                    "input": summary.total_usage.input_tokens,
                    "output": summary.total_usage.output_tokens,
                    "total": summary.total_usage.total_tokens,
                },
                "by_model": {
                    model: {"cost": data["cost"], "calls": data["calls"]}
                    for model, data in summary.by_model.items()
                },
                "by_session": dict(summary.by_session),
                "by_agent": dict(summary.by_agent),
                "record_count": summary.record_count,
                "budget_limit": ct.budget_limit,
                "budget_status": ct.check_budget().status.value if ct.budget_limit else None,
            }
        except Exception as e:
            logger.error(f"Failed to get costs: {e}")
            return {"error": str(e)}
    
    @app.get("/api/costs/history")
    async def get_cost_history(
        hours: int = Query(default=24, ge=1, le=168),
        interval: str = Query(default="hour", pattern="^(minute|hour|day)$"),
    ):
        """コスト履歴（時系列）"""
        ct = get_cost_tracker()
        if ct is None:
            return {"history": [], "error": "CostTracker not available"}
        
        try:
            # 時間範囲でフィルタ
            now = datetime.now()
            start_time = now - timedelta(hours=hours)
            
            # 間隔ごとに集計
            history = defaultdict(lambda: {"cost": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0})
            
            for record in ct.get_records():
                if record.timestamp < start_time:
                    continue
                
                if interval == "minute":
                    key = record.timestamp.strftime("%Y-%m-%d %H:%M")
                elif interval == "hour":
                    key = record.timestamp.strftime("%Y-%m-%d %H:00")
                else:  # day
                    key = record.timestamp.strftime("%Y-%m-%d")
                
                history[key]["cost"] += record.cost_usd
                history[key]["input_tokens"] += record.usage.input_tokens
                history[key]["output_tokens"] += record.usage.output_tokens
                history[key]["calls"] += 1
            
            # ソートして返す
            sorted_history = [
                {"timestamp": k, **v}
                for k, v in sorted(history.items())
            ]
            
            return {"history": sorted_history, "interval": interval}
        except Exception as e:
            logger.error(f"Failed to get cost history: {e}")
            return {"history": [], "error": str(e)}
    
    @app.get("/api/agents")
    async def list_agents():
        """エージェント一覧"""
        try:
            # AgentLoader から取得を試みる
            from moco.tools.discovery import AgentLoader
            loader = AgentLoader()
            agents = loader.list_agents()
            
            return {
                "agents": [
                    {
                        "name": a.name,
                        "description": a.description,
                        "model": a.model,
                        "tool_count": len(a.tools) if a.tools else 0,
                    }
                    for a in agents
                ]
            }
        except Exception as e:
            logger.warning(f"Failed to list agents: {e}")
            return {"agents": [], "error": str(e)}
    
    @app.get("/api/traces")
    async def list_traces(
        limit: int = Query(default=50, ge=1, le=500),
    ):
        """トレース一覧（簡易版）"""
        # Telemetry からトレースを取得する場合はここで実装
        # 現在は LogBuffer からログを返す
        logs = await log_buffer.get_recent(limit)
        return {"traces": logs}
    
    @app.get("/api/stats")
    async def get_stats():
        """統計情報"""
        sl = get_session_logger()
        ct = get_cost_tracker()
        
        stats = {
            "websocket_connections": ws_manager.connection_count,
            "log_buffer_size": len(log_buffer._logs),
        }
        
        if sl:
            try:
                sessions = sl.list_sessions(limit=1000)
                stats["total_sessions"] = len(sessions)
                stats["open_sessions"] = sum(1 for s in sessions if s.get("status") == "OPEN")
            except Exception:
                pass
        
        if ct:
            try:
                summary = ct.get_summary()
                stats["total_cost"] = summary.total_cost
                stats["total_calls"] = summary.record_count
            except Exception:
                pass
        
        return stats
    
    # ==========================================================================
    # WebSocket
    # ==========================================================================
    
    if config.enable_websocket:
        @app.websocket("/ws/logs")
        async def websocket_logs(websocket: WebSocket):
            """リアルタイムログストリーム"""
            await ws_manager.connect(websocket)
            try:
                # 最新ログを送信
                recent_logs = await log_buffer.get_recent(50)
                await websocket.send_json({
                    "type": "initial",
                    "logs": recent_logs,
                })
                
                # 接続維持（クライアントからのメッセージを待機）
                while True:
                    data = await websocket.receive_text()
                    # ping/pong対応
                    if data == "ping":
                        await websocket.send_text("pong")
            except WebSocketDisconnect:
                pass
            finally:
                await ws_manager.disconnect(websocket)
    
    # ==========================================================================
    # ログ追加API（外部から呼び出し用）
    # ==========================================================================
    
    @app.post("/api/logs")
    async def add_log(log_entry: LogEntry):
        """ログエントリを追加"""
        entry_dict = log_entry.model_dump()
        entry_dict["timestamp"] = entry_dict["timestamp"].isoformat()
        
        await log_buffer.add(entry_dict)
        await ws_manager.broadcast({
            "type": "log",
            "data": entry_dict,
        })
        return {"status": "ok"}
    
    # アプリに状態を保持（外部からアクセス用）
    app.state.log_buffer = log_buffer
    app.state.ws_manager = ws_manager
    app.state.config = config
    
    return app



def run_dashboard(
    host: str = "127.0.0.1",
    port: int = 8080,
    cost_tracker: Optional[Any] = None,
    session_logger: Optional[Any] = None,
    config: Optional[DashboardConfig] = None,
    log_level: str = "info",
) -> None:
    """
    ダッシュボードを起動する。
    
    Args:
        host: バインドするホスト
        port: ポート番号
        cost_tracker: CostTracker インスタンス
        session_logger: SessionLogger インスタンス
        config: ダッシュボード設定
        log_level: ログレベル
        
    Raises:
        ImportError: uvicorn がインストールされていない場合
    """
    if not UVICORN_AVAILABLE:
        raise ImportError(
            "uvicorn is required to run dashboard. "
            "Install with: pip install uvicorn"
        )
    
    app = create_dashboard_app(
        cost_tracker=cost_tracker,
        session_logger=session_logger,
        config=config,
    )
    
    print(f"🚀 moco Dashboard starting at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=log_level)


# =============================================================================
# CLI エントリポイント
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="moco Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8080, help="Port number")
    parser.add_argument("--title", default="moco Dashboard", help="Dashboard title")
    parser.add_argument("--db-path", help="Session database path")
    parser.add_argument("--log-level", default="info", help="Log level")
    
    args = parser.parse_args()
    
    config = DashboardConfig(
        title=args.title,
        session_db_path=args.db_path,
    )
    
    run_dashboard(
        host=args.host,
        port=args.port,
        config=config,
        log_level=args.log_level,
    )
