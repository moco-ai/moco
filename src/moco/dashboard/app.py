"""
moco ダッシュボード FastAPI アプリケーション。

セッション、コスト、エージェントの状態を可視化するWeb UIを提供する。
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Set
from collections import defaultdict

logger = logging.getLogger(__name__)

# FastAPI（オプション依存）
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPI = None

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
    # HTML テンプレート
    # ==========================================================================
    
    DASHBOARD_HTML = _get_dashboard_html(config)
    
    # ==========================================================================
    # ルート
    # ==========================================================================
    
    @app.get("/", response_class=HTMLResponse)
    async def dashboard_home():
        """ダッシュボード UI"""
        return DASHBOARD_HTML
    
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
    async def add_log(log_entry: Dict[str, Any]):
        """ログエントリを追加"""
        if "timestamp" not in log_entry:
            log_entry["timestamp"] = datetime.now().isoformat()
        
        await log_buffer.add(log_entry)
        await ws_manager.broadcast({
            "type": "log",
            "data": log_entry,
        })
        return {"status": "ok"}
    
    # アプリに状態を保持（外部からアクセス用）
    app.state.log_buffer = log_buffer
    app.state.ws_manager = ws_manager
    app.state.config = config
    
    return app


def _get_dashboard_html(config: DashboardConfig) -> str:
    """ダッシュボードHTMLを生成"""
    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{config.title}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --bg-card: #0f3460;
            --text-primary: #eaeaea;
            --text-secondary: #a0a0a0;
            --accent: #e94560;
            --accent-light: #ff6b6b;
            --success: #4ade80;
            --warning: #fbbf24;
            --border: #334155;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }}
        
        .header {{
            background: var(--bg-secondary);
            padding: 1rem 2rem;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .header h1 {{
            font-size: 1.5rem;
            color: var(--accent);
        }}
        
        .status-bar {{
            display: flex;
            gap: 1rem;
            font-size: 0.875rem;
        }}
        
        .status-item {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        
        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
        }}
        
        .status-dot.warning {{
            background: var(--warning);
        }}
        
        .status-dot.error {{
            background: var(--accent);
        }}
        
        .container {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
            padding: 1rem;
            max-width: 1800px;
            margin: 0 auto;
        }}
        
        .card {{
            background: var(--bg-card);
            border-radius: 8px;
            padding: 1rem;
            border: 1px solid var(--border);
        }}
        
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid var(--border);
        }}
        
        .card-title {{
            font-size: 1rem;
            font-weight: 600;
        }}
        
        .card-full {{
            grid-column: 1 / -1;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
        }}
        
        .stat-box {{
            background: var(--bg-secondary);
            padding: 1rem;
            border-radius: 6px;
            text-align: center;
        }}
        
        .stat-value {{
            font-size: 1.75rem;
            font-weight: bold;
            color: var(--accent-light);
        }}
        
        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 0.25rem;
        }}
        
        .table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.875rem;
        }}
        
        .table th, .table td {{
            padding: 0.75rem;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        
        .table th {{
            color: var(--text-secondary);
            font-weight: 500;
        }}
        
        .table tr:hover {{
            background: rgba(255,255,255,0.05);
        }}
        
        .badge {{
            display: inline-block;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 500;
        }}
        
        .badge-open {{
            background: rgba(74, 222, 128, 0.2);
            color: var(--success);
        }}
        
        .badge-closed {{
            background: rgba(160, 160, 160, 0.2);
            color: var(--text-secondary);
        }}
        
        .log-container {{
            height: 300px;
            overflow-y: auto;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 0.75rem;
            background: var(--bg-primary);
            padding: 0.5rem;
            border-radius: 4px;
        }}
        
        .log-entry {{
            padding: 0.25rem 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        
        .log-time {{
            color: var(--text-secondary);
            margin-right: 0.5rem;
        }}
        
        .log-level-info {{
            color: #60a5fa;
        }}
        
        .log-level-warn {{
            color: var(--warning);
        }}
        
        .log-level-error {{
            color: var(--accent);
        }}
        
        .chart-container {{
            height: 250px;
            position: relative;
        }}
        
        .refresh-btn {{
            background: var(--accent);
            color: white;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.875rem;
        }}
        
        .refresh-btn:hover {{
            background: var(--accent-light);
        }}
        
        .model-bar {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin: 0.5rem 0;
        }}
        
        .model-name {{
            width: 150px;
            font-size: 0.75rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .model-bar-fill {{
            height: 20px;
            background: linear-gradient(90deg, var(--accent), var(--accent-light));
            border-radius: 4px;
            min-width: 2px;
        }}
        
        .model-cost {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-left: auto;
        }}
        
        @media (max-width: 1200px) {{
            .container {{
                grid-template-columns: 1fr;
            }}
            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
        }}
    </style>
</head>
<body>
    <header class="header">
        <h1>🤖 {config.title}</h1>
        <div class="status-bar">
            <div class="status-item">
                <span class="status-dot" id="wsStatus"></span>
                <span id="wsStatusText">接続中...</span>
            </div>
            <div class="status-item">
                <span>最終更新: <span id="lastUpdate">-</span></span>
            </div>
            <button class="refresh-btn" onclick="refreshAll()">🔄 更新</button>
        </div>
    </header>
    
    <main class="container">
        <!-- 統計サマリー -->
        <div class="card card-full">
            <div class="card-header">
                <span class="card-title">📊 概要</span>
            </div>
            <div class="stats-grid">
                <div class="stat-box">
                    <div class="stat-value" id="totalCost">$0.00</div>
                    <div class="stat-label">総コスト</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value" id="totalTokens">0</div>
                    <div class="stat-label">総トークン</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value" id="totalSessions">0</div>
                    <div class="stat-label">セッション数</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value" id="totalCalls">0</div>
                    <div class="stat-label">API呼び出し</div>
                </div>
            </div>
        </div>
        
        <!-- コストグラフ -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">💰 コスト推移</span>
            </div>
            <div class="chart-container">
                <canvas id="costChart"></canvas>
            </div>
        </div>
        
        <!-- モデル別使用量 -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">🧠 モデル別コスト</span>
            </div>
            <div id="modelUsage"></div>
        </div>
        
        <!-- セッション一覧 -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">📁 最近のセッション</span>
            </div>
            <table class="table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>タイトル</th>
                        <th>ステータス</th>
                        <th>更新日時</th>
                    </tr>
                </thead>
                <tbody id="sessionsTable">
                </tbody>
            </table>
        </div>
        
        <!-- エージェント一覧 -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">🤖 エージェント</span>
            </div>
            <table class="table">
                <thead>
                    <tr>
                        <th>名前</th>
                        <th>モデル</th>
                        <th>ツール数</th>
                    </tr>
                </thead>
                <tbody id="agentsTable">
                </tbody>
            </table>
        </div>
        
        <!-- リアルタイムログ -->
        <div class="card card-full">
            <div class="card-header">
                <span class="card-title">📜 リアルタイムログ</span>
                <button class="refresh-btn" onclick="clearLogs()">クリア</button>
            </div>
            <div class="log-container" id="logContainer">
            </div>
        </div>
    </main>
    
    <script>
        // 設定
        const REFRESH_INTERVAL = {config.refresh_interval} * 1000;
        let costChart = null;
        let ws = null;
        
        // 初期化
        document.addEventListener('DOMContentLoaded', () => {{
            initChart();
            refreshAll();
            connectWebSocket();
            setInterval(refreshAll, REFRESH_INTERVAL);
        }});
        
        // Chart.js 初期化
        function initChart() {{
            const ctx = document.getElementById('costChart').getContext('2d');
            costChart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: [],
                    datasets: [{{
                        label: 'コスト ($)',
                        data: [],
                        borderColor: '#e94560',
                        backgroundColor: 'rgba(233, 69, 96, 0.1)',
                        fill: true,
                        tension: 0.4,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{
                            display: false,
                        }},
                    }},
                    scales: {{
                        x: {{
                            grid: {{ color: 'rgba(255,255,255,0.1)' }},
                            ticks: {{ color: '#a0a0a0' }},
                        }},
                        y: {{
                            grid: {{ color: 'rgba(255,255,255,0.1)' }},
                            ticks: {{ 
                                color: '#a0a0a0',
                                callback: (v) => '$' + v.toFixed(4),
                            }},
                        }},
                    }},
                }},
            }});
        }}
        
        // WebSocket 接続
        function connectWebSocket() {{
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${{protocol}}//${{location.host}}/ws/logs`);
            
            ws.onopen = () => {{
                document.getElementById('wsStatus').className = 'status-dot';
                document.getElementById('wsStatusText').textContent = '接続中';
            }};
            
            ws.onclose = () => {{
                document.getElementById('wsStatus').className = 'status-dot error';
                document.getElementById('wsStatusText').textContent = '切断';
                setTimeout(connectWebSocket, 3000);
            }};
            
            ws.onerror = () => {{
                document.getElementById('wsStatus').className = 'status-dot warning';
            }};
            
            ws.onmessage = (event) => {{
                const msg = JSON.parse(event.data);
                if (msg.type === 'initial') {{
                    msg.logs.forEach(addLogEntry);
                }} else if (msg.type === 'log') {{
                    addLogEntry(msg.data);
                }}
            }};
            
            // Ping/Pong
            setInterval(() => {{
                if (ws && ws.readyState === WebSocket.OPEN) {{
                    ws.send('ping');
                }}
            }}, 30000);
        }}
        
        // 全データ更新
        async function refreshAll() {{
            await Promise.all([
                refreshStats(),
                refreshCosts(),
                refreshCostHistory(),
                refreshSessions(),
                refreshAgents(),
            ]);
            document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
        }}
        
        // 統計更新
        async function refreshStats() {{
            try {{
                const res = await fetch('/api/stats');
                const data = await res.json();
                
                if (data.total_sessions !== undefined) {{
                    document.getElementById('totalSessions').textContent = data.total_sessions;
                }}
                if (data.total_calls !== undefined) {{
                    document.getElementById('totalCalls').textContent = data.total_calls.toLocaleString();
                }}
            }} catch (e) {{
                console.error('Stats refresh failed:', e);
            }}
        }}
        
        // コスト更新
        async function refreshCosts() {{
            try {{
                const res = await fetch('/api/costs');
                const data = await res.json();
                
                document.getElementById('totalCost').textContent = '$' + (data.total_cost || 0).toFixed(4);
                document.getElementById('totalTokens').textContent = (data.total_tokens?.total || 0).toLocaleString();
                
                // モデル別使用量
                const modelUsage = document.getElementById('modelUsage');
                const models = Object.entries(data.by_model || {{}});
                const maxCost = Math.max(...models.map(([_, v]) => v.cost), 0.0001);
                
                modelUsage.innerHTML = models
                    .sort((a, b) => b[1].cost - a[1].cost)
                    .slice(0, 8)
                    .map(([model, info]) => `
                        <div class="model-bar">
                            <span class="model-name" title="${{model}}">${{model}}</span>
                            <div class="model-bar-fill" style="width: ${{(info.cost / maxCost * 100).toFixed(1)}}%"></div>
                            <span class="model-cost">${{info.calls}} calls / $${{info.cost.toFixed(4)}}</span>
                        </div>
                    `).join('');
            }} catch (e) {{
                console.error('Costs refresh failed:', e);
            }}
        }}
        
        // コスト履歴更新
        async function refreshCostHistory() {{
            try {{
                const res = await fetch('/api/costs/history?hours=24&interval=hour');
                const data = await res.json();
                
                if (costChart && data.history) {{
                    costChart.data.labels = data.history.map(h => h.timestamp.split(' ')[1] || h.timestamp);
                    costChart.data.datasets[0].data = data.history.map(h => h.cost);
                    costChart.update();
                }}
            }} catch (e) {{
                console.error('Cost history refresh failed:', e);
            }}
        }}
        
        // セッション更新
        async function refreshSessions() {{
            try {{
                const res = await fetch('/api/sessions?limit=10');
                const data = await res.json();
                
                const tbody = document.getElementById('sessionsTable');
                tbody.innerHTML = (data.sessions || []).map(s => `
                    <tr>
                        <td><code>${{s.session_id.slice(0, 15)}}...</code></td>
                        <td>${{s.title || '-'}}</td>
                        <td><span class="badge badge-${{s.status?.toLowerCase() || 'open'}}">${{s.status || 'OPEN'}}</span></td>
                        <td>${{formatDate(s.last_updated)}}</td>
                    </tr>
                `).join('');
            }} catch (e) {{
                console.error('Sessions refresh failed:', e);
            }}
        }}
        
        // エージェント更新
        async function refreshAgents() {{
            try {{
                const res = await fetch('/api/agents');
                const data = await res.json();
                
                const tbody = document.getElementById('agentsTable');
                tbody.innerHTML = (data.agents || []).map(a => `
                    <tr>
                        <td>${{a.name}}</td>
                        <td><code>${{a.model || '-'}}</code></td>
                        <td>${{a.tool_count || 0}}</td>
                    </tr>
                `).join('');
            }} catch (e) {{
                console.error('Agents refresh failed:', e);
            }}
        }}
        
        // ログエントリ追加
        function addLogEntry(entry) {{
            const container = document.getElementById('logContainer');
            const div = document.createElement('div');
            div.className = 'log-entry';
            
            const level = (entry.level || 'info').toLowerCase();
            const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : '-';
            
            div.innerHTML = `
                <span class="log-time">${{time}}</span>
                <span class="log-level-${{level}}">[$${{level.toUpperCase()}}]</span>
                ${{entry.message || JSON.stringify(entry)}}
            `;
            
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
            
            // 最大行数制限
            while (container.children.length > 500) {{
                container.removeChild(container.firstChild);
            }}
        }}
        
        // ログクリア
        function clearLogs() {{
            document.getElementById('logContainer').innerHTML = '';
        }}
        
        // 日付フォーマット
        function formatDate(dateStr) {{
            if (!dateStr) return '-';
            const d = new Date(dateStr);
            return d.toLocaleDateString() + ' ' + d.toLocaleTimeString();
        }}
    </script>
</body>
</html>'''


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
