"""
FastAPI backend for Moco Web UI
ChatGPT-like interface
"""
import os
import re
import asyncio
import queue
import threading
import time
import sys
from typing import Optional

# moco imports - sys.path must be set before importing moco modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from moco.common.schemas import ChatRequest, SessionCreate, FileResponse
from moco.common.errors import setup_exception_handlers
import json
import sqlite3
import logging
from datetime import datetime, date
from dotenv import load_dotenv, find_dotenv

# .env を読み込む（親方向に自動探索）
load_dotenv(find_dotenv())

from moco.core.orchestrator import Orchestrator
from moco.storage.session_logger import SessionLogger
from moco.tools.discovery import _find_profiles_dir
from moco.utils.json_parser import SmartJSONParser
from moco.cancellation import (
    create_cancel_event,
    request_cancel,
    clear_cancel_event,
    OperationCancelled
)


def filter_response_for_display(response: str, verbose: bool = False) -> str:
    """レスポンスをフィルタリング（verboseでない場合は最後のエージェントだけ）"""
    if not response:
        return ""
    if verbose:
        return response

    # @agent: 応答 のパターンで分割
    sections = re.split(r'(@[\w-]+):\s*', response)

    if len(sections) > 1:
        # 最後のエージェントの結果だけを取得
        last_agent = sections[-2] if len(sections) >= 2 else ""
        last_content = sections[-1].strip() if sections[-1] else ""

        # orchestrator の最終回答は省略しない
        if last_agent == "@orchestrator":
            return last_content
        else:
            # 中間エージェントの場合も全文返す（UIでの表示は別途対応）
            return f"{last_agent}: {last_content}"

    return response

app = FastAPI(title="Moco", version="1.0.0")
setup_exception_handlers(app)

# 静的ファイルのマウント
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# グローバル状態
session_logger = SessionLogger()
logger = logging.getLogger(__name__)


def get_orchestrator(profile: str, provider: str = "gemini", verbose: bool = False, working_directory: str = None) -> Orchestrator:
    """Orchestratorインスタンスを新規生成"""
    # 作業ディレクトリ: 引数 > 環境変数 > カレントディレクトリ
    work_dir = working_directory or os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd()
    return Orchestrator(
        profile=profile,
        provider=provider,
        session_logger=session_logger,
        verbose=verbose,
        working_directory=work_dir
    )



# === Routes ===

@app.get("/", response_class=HTMLResponse)
async def root():
    """メインページ"""
    with open(os.path.join(static_dir, "index.html"), "r") as f:
        return f.read()


@app.get("/api/browse-directories")
async def browse_directories(path: str = None):
    """
    ディレクトリ一覧を取得（フォルダ選択UI用）
    path が None の場合は作業ディレクトリを起点とする
    """
    # ベースディレクトリの決定
    # realpath を使用してシンボリックリンクを解決
    base_dir = os.path.realpath(os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd())

    if path is None:
        # デフォルト: 作業ディレクトリ
        base_paths = [
            {"path": base_dir, "name": "Workspace", "icon": "🏠"},
        ]
        # サブディレクトリがあれば追加
        for sub in ["src", "profiles", "workspace"]:
            full = os.path.join(base_dir, sub)
            if os.path.isdir(full):
                base_paths.append({"path": full, "name": sub, "icon": "📁"})
        
        return {"directories": base_paths, "current": base_dir}

    # パスの正規化と検証
    try:
        requested_path = os.path.normpath(path)
        if os.path.isabs(requested_path):
            target_path = os.path.realpath(requested_path)
        else:
            target_path = os.path.realpath(os.path.join(base_dir, requested_path))

        # ディレクトリトラバーサル対策: target_path が base_dir の配下にあるか確認
        if os.path.commonpath([base_dir, target_path]) != base_dir:
            return {
                "error": f"Access denied: {path} is outside the working directory",
                "directories": [],
                "current": path
            }
    except ValueError:
        # Windowsのドライブ跨ぎなどの場合に発生する可能性がある
        return {
            "error": f"Invalid path access: {path}",
            "directories": [],
            "current": path
        }

    if not os.path.exists(target_path):
        return {"error": f"Path not found: {path}", "directories": [], "current": path}

    if not os.path.isdir(target_path):
        return {"error": f"Not a directory: {path}", "directories": [], "current": path}

    try:
        items = os.listdir(target_path)
        directories = []
        for item in sorted(items):
            if item.startswith('.'):
                continue  # 隠しファイルをスキップ
            full_item_path = os.path.join(target_path, item)
            if os.path.isdir(full_item_path):
                # プロジェクトかどうかを判定
                is_project = any(
                    os.path.exists(os.path.join(full_item_path, marker))
                    for marker in [".git", "package.json", "pyproject.toml", "requirements.txt"]
                )
                directories.append({
                    "path": full_item_path,
                    "name": item,
                    "icon": "📦" if is_project else "📁",
                    "is_project": is_project
                })

        parent = os.path.dirname(target_path)
        # 親ディレクトリも制限内である場合のみ返す
        try:
            if os.path.commonpath([base_dir, parent]) != base_dir:
                parent = None
        except ValueError:
            parent = None

        return {
            "directories": directories,
            "current": target_path,
            "parent": parent if parent != target_path else None
        }
    except Exception as e:
        logger.exception(f"Error browsing directory: {target_path}")
        return {"error": str(e), "directories": [], "current": target_path}


@app.get("/api/profiles")
async def list_profiles():
    """利用可能なプロファイル一覧"""
    profiles_dir = _find_profiles_dir()
    if not os.path.exists(profiles_dir):
        return {"profiles": ["default"]}

    profiles = [
        d for d in os.listdir(profiles_dir)
        if os.path.isdir(os.path.join(profiles_dir, d)) and d != "__pycache__"
    ]
    return {"profiles": sorted(profiles)}


@app.get("/api/sessions")
async def list_sessions(limit: int = 20, profile: str = None):
    """セッション一覧（プロファイルでフィルタ可能）"""
    sessions = session_logger.list_sessions(limit=limit, profile=profile)
    return {"sessions": sessions}


@app.post("/api/sessions")
async def create_session(req: SessionCreate):
    """新規セッション作成"""
    orchestrator = get_orchestrator(req.profile)
    session_id = orchestrator.create_session(title=req.title, profile=req.profile)
    return {"session_id": session_id, "title": req.title}


@app.post("/api/sessions/{session_id}/cancel")
async def cancel_task(session_id: str):
    """実行中のタスクをキャンセル"""
    success = request_cancel(session_id)
    return {"status": "success" if success else "not_found", "session_id": session_id}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """セッション詳細と履歴"""
    session = session_logger.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    history = session_logger._get_recent_messages(session_id, limit=100)
    return {
        "session": session,
        "messages": history
    }


@app.get("/api/file", response_model=FileResponse)
async def get_file_content(path: str):
    """
    指定されたファイルの情報を取得する。
    ディレクトリトラバーサル対策を施し、MOCO_WORKING_DIRECTORY配下のファイルのみアクセス可能。
    """
    # ベースディレクトリの決定
    base_dir = os.path.abspath(os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd())

    # パスの正規化と検証
    requested_path = os.path.normpath(path)
    if os.path.isabs(requested_path):
        # 絶対パスが指定された場合は、ベースディレクトリからの相対パスとして扱う
        requested_path = requested_path.lstrip(os.sep)

    target_path = os.path.abspath(os.path.join(base_dir, requested_path))

    # ディレクトリトラバーサル対策: target_path が base_dir の配下にあるか確認
    if os.path.commonpath([base_dir, target_path]) != base_dir:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: {path} is outside the working directory"
        )

    # ファイルの存在確認
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # ディレクトリでないことを確認
    if not os.path.isfile(target_path):
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")

    try:
        # ファイル情報の取得
        size = os.path.getsize(target_path)

        # テキストファイルとして読み込み
        with open(target_path, 'r', encoding='utf-8') as f:
            content = f.read()

        line_count = len(content.splitlines())

        return FileResponse(
            content=content,
            line_count=line_count,
            size=size,
            path=path
        )
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="File is not a valid UTF-8 text file"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error reading file: {str(e)}"
        )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """セッション削除"""
    try:
        session_logger.delete_session(session_id)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def get_stats(session_id: Optional[str] = None, scope: str = "all"):
    """統計データを取得"""
    try:
        from pathlib import Path
        db_path = Path(__file__).parent.parent.parent.parent / "data" / "optimizer" / "metrics.db"
        
        # デフォルトのレスポンス構造
        stats = {
            "today_avg_score": 0,
            "today_count": 0,
            "avg_score": 0,
            "count": 0,
            "success_rate": 0,
            "overall_metrics": {
                "avg_complexity": 0,
                "avg_delegation": 0,
                "todo_usage_rate": 0,
                "avg_history_turns": 0,
                "avg_summary_depth": 0,
                "avg_prompt_specificity": 0,
                "summaries": 0
            },
            "profile_stats": [],
            "recent_tasks": [],
            "score_trend": [],
            "agent_stats": {}
        }

        if scope == "session" and not session_id:
            return stats

        # ディレクトリ作成と初期化
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.exists():
            # 新規作成時は空の統計を返す（テーブル作成後にデータがない状態と同じ）
            conn = sqlite3.connect(str(db_path))
            # ここでテーブル作成
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, ai_score REAL, task_summary TEXT, task_complexity REAL, delegation_count INTEGER, todo_used INTEGER, history_turns INTEGER, summary_depth INTEGER, prompt_specificity REAL, profile TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS agent_executions (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, agent_name TEXT, inline_score REAL, tokens_input INTEGER, tokens_output INTEGER, execution_time_ms INTEGER, error_message TEXT, summary_depth INTEGER, history_turns INTEGER, FOREIGN KEY (request_id) REFERENCES metrics (id))")
            conn.commit()
            return stats
        
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # フィルタ条件の構築 (metricsテーブル)
        where_clause = "WHERE ai_score IS NOT NULL"
        params = []

        if scope == "session" and session_id:
            where_clause += " AND session_id = ?"
            params.append(session_id)
        elif scope == "today":
            where_clause += " AND timestamp >= date('now', 'localtime')"
        # "all" の場合は追加条件なし

        # フィルタ条件の構築 (agent_executionsテーブル)
        # agent_executionsにはsession_idやtimestampがないため、metricsとJOINが必要な場合がある
        ae_join = ""
        where_clause_ae = "WHERE 1=1"
        params_ae = []
        if scope == "session" and session_id:
            ae_join = "JOIN metrics m ON agent_executions.request_id = m.id"
            where_clause_ae = "WHERE m.session_id = ?"
            params_ae.append(session_id)
        elif scope == "today":
            ae_join = "JOIN metrics m ON agent_executions.request_id = m.id"
            where_clause_ae = "WHERE m.timestamp >= date('now', 'localtime')"

        # 今日の統計
        cursor.execute(f"""
            SELECT AVG(ai_score), COUNT(*),
                   SUM(CASE WHEN ai_score >= 0.7 THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
            FROM metrics
            {where_clause}
        """, params)
        row = cursor.fetchone()
        today_avg = row[0] or 0
        today_count = row[1] or 0
        success_rate = row[2] or 0

        # 全体メトリクス
        cursor.execute(f"""
            SELECT
                AVG(task_complexity),
                AVG(delegation_count),
                AVG(CASE WHEN todo_used = 1 THEN 1.0 ELSE 0 END) * 100,
                AVG(history_turns),
                AVG(summary_depth),
                AVG(prompt_specificity),
                SUM(CASE WHEN summary_depth > 0 THEN 1 ELSE 0 END)
            FROM metrics
            {where_clause}
        """, params)
        metrics_row = cursor.fetchone()
        overall_metrics = {
            "avg_complexity": round(metrics_row[0] or 0, 1),
            "avg_delegation": round(metrics_row[1] or 0, 1),
            "todo_usage_rate": round(metrics_row[2] or 0, 1),
            "avg_history_turns": round(metrics_row[3] or 0, 1),
            "avg_summary_depth": round(metrics_row[4] or 0, 1),
            "avg_prompt_specificity": round(metrics_row[5] or 0, 1),
            "summaries": metrics_row[6] or 0
        }

        # プロファイル別統計
        cursor.execute(f"""
            SELECT profile, AVG(ai_score), COUNT(*)
            FROM metrics
            {where_clause}
            GROUP BY profile
            ORDER BY COUNT(*) DESC
            LIMIT 5
        """, params)
        profile_stats = [
            {"profile": r[0], "avg_score": r[1] or 0, "count": r[2]}
            for r in cursor.fetchall()
        ]

        # 最新タスク
        cursor.execute(f"""
            SELECT task_summary, ai_score, task_complexity, timestamp
            FROM metrics
            {where_clause}
            ORDER BY id DESC
            LIMIT 5
        """, params)
        recent_tasks = [
            {
                "task": r[0][:40] + "..." if r[0] and len(r[0]) > 40 else (r[0] or ""),
                "score": r[1] or 0,
                "complexity": r[2] or 0,
                "time": r[3].split("T")[1][:5] if r[3] and "T" in r[3] else ""
            }
            for r in cursor.fetchall()
        ]

        # スコア推移（直近10件）
        cursor.execute(f"""
            SELECT ai_score FROM metrics
            {where_clause}
            ORDER BY id DESC
            LIMIT 10
        """, params)
        score_trend = [r[0] for r in cursor.fetchall()][::-1]

        # エージェント別統計（新テーブル優先、フォールバックあり）
        agent_stats = {}

        # 新しい agent_executions テーブルからデータを取得
        try:
            cursor.execute(f"""
                SELECT
                    agent_name,
                    COUNT(*) as total,
                    AVG(inline_score) as avg_score,
                    AVG(tokens_input + tokens_output) as avg_tokens,
                    AVG(execution_time_ms) as avg_time_ms,
                    SUM(CASE WHEN inline_score >= 0.7 THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as error_count,
                    AVG(agent_executions.summary_depth),
                    AVG(agent_executions.history_turns),
                    SUM(CASE WHEN agent_executions.summary_depth > 0 THEN 1 ELSE 0 END) as summaries
                FROM agent_executions
                {ae_join}
                {where_clause_ae}
                GROUP BY agent_name
                ORDER BY total DESC
                LIMIT 10
            """, params_ae)
            rows = cursor.fetchall()

            if rows:
                for row in rows:
                    agent_name = row[0]
                    total = row[1]
                    agent_stats[agent_name] = {
                        "total": total,
                        "success": row[5] or 0,
                        "avg_score": round(row[2] or 0, 2),
                        "avg_tokens": round(row[3] or 0),
                        "avg_time_ms": round(row[4] or 0),
                        "success_rate": round((row[5] / total * 100) if total > 0 else 0, 1),
                        "error_rate": round((row[6] / total * 100) if total > 0 else 0, 1),
                        "avg_summary_depth": round(row[7] or 0, 1),
                        "avg_history_turns": round(row[8] or 0, 1),
                        "summaries": row[9] or 0
                    }
        except sqlite3.OperationalError:
            pass  # テーブルがまだ存在しない場合

        # 新テーブルにデータがない場合は旧方式でフォールバック
        if not agent_stats:
            cursor.execute(f"""
                SELECT agents_selected, ai_score, task_complexity, delegation_count, todo_used
                FROM metrics
                {where_clause} AND agents_selected IS NOT NULL
            """, params)
            rows = cursor.fetchall()

            from collections import defaultdict
            agent_counts = defaultdict(int)
            agent_scores = defaultdict(float)
            agent_success = defaultdict(int)
            agent_complexity = defaultdict(float)
            agent_delegation = defaultdict(float)
            agent_todo = defaultdict(int)

            for agents_json, score, complexity, delegation, todo in rows:
                try:
                    agents = json.loads(agents_json)
                    if isinstance(agents, list):
                        for agent in agents:
                            agent_counts[agent] += 1
                            agent_scores[agent] += score
                            if score >= 0.7:
                                agent_success[agent] += 1
                            agent_complexity[agent] += (complexity or 0)
                            agent_delegation[agent] += (delegation or 0)
                            if todo:
                                agent_todo[agent] += 1
                except Exception:
                    continue

            for agent, count in agent_counts.items():
                avg_score = agent_scores[agent] / count
                agent_stats[agent] = {
                    "total": count,
                    "success": agent_success[agent],
                    "avg_score": round(avg_score, 2),
                    "avg_complexity": round(agent_complexity[agent] / count, 1),
                    "avg_delegation": round(agent_delegation[agent] / count, 1),
                    "todo_usage": round(agent_todo[agent] / count * 100, 1),
                    "summaries": 0,
                    "avg_history_turns": 0
                }

            # 上位10件に絞る（countの多い順）
            sorted_agents = sorted(agent_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]
            agent_stats = dict(sorted_agents)

        conn.close()
        
        return {
            "today_avg_score": round(today_avg, 2),
            "today_count": today_count,
            "avg_score": round(today_avg, 2),
            "count": today_count,
            "success_rate": round(success_rate * 100, 1),
            "overall_metrics": overall_metrics,
            "profile_stats": profile_stats,
            "recent_tasks": recent_tasks,
            "score_trend": score_trend,
            "agent_stats": agent_stats
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """チャット（非ストリーミング）"""
    orchestrator = get_orchestrator(
        req.profile,
        req.provider,
        req.verbose,
        req.working_directory
    )

    # セッションIDがない場合は新規作成
    session_id = req.session_id
    if not session_id:
        session_id = orchestrator.create_session(title=req.message[:50])

    # 実行
    response = orchestrator.run_sync(req.message, session_id=session_id)

    return {
        "response": response,
        "session_id": session_id
    }


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """チャット（ストリーミング）- Server-Sent Events with real-time tool updates"""

    # イベントキュー（スレッド間通信用）
    event_queue = queue.Queue()

    # thinking イベントのバッチ化用
    thinking_buffer = ""
    last_thinking_time = 0
    last_agent_name = "orchestrator"

    # 進捗コールバック
    def progress_callback(event_type: str, name: str = None, detail: str = "", agent_name: str = None, parent_agent: str = None, status: str = "running", tool_name: str = None, content: str = None, result: str = None, **kwargs):
        nonlocal thinking_buffer, last_thinking_time, last_agent_name
        
        current_agent = agent_name or last_agent_name or "orchestrator"
        last_agent_name = current_agent

        if event_type == "thinking":
            thinking_buffer += (content or "")
            current_time = time.time()
            # 100文字以上、または0.2秒経過したら送信
            if len(thinking_buffer) >= 100 or (current_time - last_thinking_time) >= 0.2:
                event_queue.put({
                    "type": "thinking",
                    "content": thinking_buffer,
                    "agent": current_agent
                })
                thinking_buffer = ""
                last_thinking_time = current_time
            return

        # 思考以外のイベントが発生した場合はバッファをフラッシュ（順序維持）
        if thinking_buffer:
            event_queue.put({
                "type": "thinking",
                "content": thinking_buffer,
                "agent": current_agent
            })
            thinking_buffer = ""
            
        if event_type == "flush":
            return

        if event_type == "chunk":
            event_queue.put({
                "type": "chunk",
                "content": content,
                "agent": current_agent
            })
            return

        # agent_name が明示的に None の場合をガード
        agent_name = agent_name or "orchestrator"
        
        # 名前が提供されていない場合はエージェント名を使用（recallなどの場合）
        display_name = name or agent_name or ""
        # アイコンを削除してツール名/エージェント名のみにする
        clean_name = display_name
        if display_name and " " in display_name:
            parts = display_name.split()
            if parts:
                clean_name = parts[-1]

        # インサイトパネル用のイベント送信
        if event_type == "recall":
            results = kwargs.get("results", [])
            for res in results:
                event_queue.put({
                    "type": "recall",
                    "recall_type": "Memory",
                    "query": detail or "Semantic Recall",
                    "details": res.get("content", "") if isinstance(res, dict) else str(res)
                })
        elif event_type == "delegate" and status == "running":
            event_queue.put({
                "type": "recall",
                "recall_type": "Delegation",
                "query": f"→ @{clean_name}",
                "details": detail
            })
        elif event_type == "tool" and status == "completed":
            # ツール実行結果もインサイトに表示
            event_queue.put({
                "type": "recall",
                "recall_type": "Tool",
                "query": f"🛠️ {tool_name or clean_name}",
                "details": result
            })

        # app.js が期待する形式（agent, parent, tool, event, status）に統一
        data = {
            "type": "progress",
            "agent": agent_name,
            "parent": parent_agent,
            "tool": tool_name or (clean_name if event_type == "tool" else None),
            "event": event_type,
            "status": status,
            "name": clean_name,
            "detail": detail
        }
        event_queue.put(data)

    # Orchestrator をコールバック付きで作成
    # 作業ディレクトリ: リクエスト > 環境変数 > カレントディレクトリ
    work_dir = req.working_directory or os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd()
    orchestrator = Orchestrator(
        profile=req.profile,
        provider=req.provider,
        model=req.model,  # OpenRouter用モデル名
        session_logger=session_logger,
        verbose=req.verbose,
        progress_callback=progress_callback,
        working_directory=work_dir
    )

    session_id = req.session_id
    if not session_id:
        session_id = orchestrator.create_session(title=req.message[:50])

    # キャンセルイベントを確実にクリアしてから新規登録
    # (過去のリクエストでキャンセル状態が残っているのを防ぐ)
    clear_cancel_event(session_id)
    create_cancel_event(session_id)

    # 結果を格納する変数
    result_holder = {"response": None, "error": None, "cancelled": False}
    stop_event = threading.Event()

    def run_orchestrator():
        try:
            result_holder["response"] = orchestrator.run_sync(req.message, session_id=session_id)
            # 完了直前に強制フラッシュ
            progress_callback(event_type="flush")
        except OperationCancelled:
            result_holder["cancelled"] = True
            # キャンセル時は特別なイベントを投げる
            event_queue.put({"type": "cancelled", "message": "Task was cancelled by user."})
        except Exception as e:
            result_holder["error"] = str(e)
        finally:
            # clear_cancel_event はバックエンド側の check_cancelled 内でも呼ばれる可能性があるが、
            # 万が一の漏れを防ぐためここでも呼ぶ。ただし二重呼び出しは問題ない設計。
            clear_cancel_event(session_id)
            if not stop_event.is_set():
                event_queue.put({"type": "done"})

    # バックグラウンドで実行
    thread = threading.Thread(target=run_orchestrator, daemon=True)
    thread.start()

    async def generate():
        # 開始イベント
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"
        await asyncio.sleep(0.01)

        has_sent_chunks = False

        try:
            while True:
                try:
                    # ノンブロッキングでキューをチェック
                    event = event_queue.get(timeout=0.1)

                    if event["type"] == "done":
                        # 完了 - チャンクが一度も送られていない場合のみ、最終結果を送信
                        if result_holder["cancelled"]:
                            # キャンセルメッセージは別途送信済み（type: cancelled）だが、
                            # クライアント側の処理確実化のために status: cancelled も送る
                            yield f"data: {json.dumps({'type': 'status', 'status': 'cancelled', 'content': 'Operation cancelled.'})}\n\n"
                        elif result_holder["error"]:
                            yield f"data: {json.dumps({'type': 'error', 'message': result_holder['error']})}\n\n"
                        elif not has_sent_chunks:
                            response = result_holder["response"] or ""
                            # verbose でない場合はフィルタリング
                            response = filter_response_for_display(response, req.verbose)
                            # 結果をチャンクで送信
                            chunk_size = 100
                            for i in range(0, len(response), chunk_size):
                                chunk = response[i:i+chunk_size]
                                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                                await asyncio.sleep(0.01)

                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        break
                    elif event["type"] == "chunk":
                        has_sent_chunks = True
                        yield f"data: {json.dumps(event)}\n\n"
                        await asyncio.sleep(0.01)
                    else:
                        # 進捗イベント
                        yield f"data: {json.dumps(event)}\n\n"
                        await asyncio.sleep(0.01)

                except queue.Empty:
                    # キューが空 - 少し待つ
                    await asyncio.sleep(0.05)
        finally:
            stop_event.set()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Nginxなどのバッファリングを無効化
        }
    )

@app.post("/api/debug/parse-json")
async def debug_parse_json(req: dict):
    """
    汚れた JSON 文字列をクリーンアップしてパースするデバッグ用エンドポイント
    """
    text = req.get("text", "")
    result = SmartJSONParser.parse(text)
    if result is None:
        raise HTTPException(status_code=400, detail="Failed to parse JSON")
    return {"result": result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
