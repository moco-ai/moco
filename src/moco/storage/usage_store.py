"""
Moco usage storage module.
SQLite を使用してトークン使用量とコストを永続化する。
"""

import sqlite3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
# 依存関係を最小限にするため、循環インポートを避ける
import os

def _get_default_storage_dir() -> Path:
    storage_dir = os.environ.get("MOCO_STORAGE_DIR")
    if storage_dir:
        return Path(storage_dir)
    return Path.home() / ".moco" / "storage"

class UsageStore:
    """トークン使用量とコストを SQLite に保存・集計するクラス"""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = _get_default_storage_dir() / "usage.db"
        
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """データベースの初期化"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    session_id TEXT,
                    agent_name TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    metadata TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_logs(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_session_id ON usage_logs(session_id)")

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        session_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None
    ):
        """使用量を記録する"""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        total_tokens = input_tokens + output_tokens
        metadata_json = json.dumps(metadata) if metadata else None

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO usage_logs (
                    timestamp, session_id, agent_name, provider, model,
                    input_tokens, output_tokens, total_tokens, cost_usd, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp.isoformat(),
                    session_id,
                    agent_name,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    cost_usd,
                    metadata_json
                )
            )

    def get_session_usage(self, session_id: str) -> Dict[str, Any]:
        """特定のセッションの使用量を取得"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT 
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(cost_usd) as cost_usd
                FROM usage_logs
                WHERE session_id = ?
                """,
                (session_id,)
            )
            row = cursor.fetchone()
            if row and row["total_tokens"]:
                return dict(row)
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}

    def get_usage_summary(self, days: int = 7) -> List[Dict[str, Any]]:
        """過去 N 日間の日別集計を取得"""
        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT 
                    date(timestamp) as date,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(cost_usd) as cost_usd
                FROM usage_logs
                WHERE date(timestamp) >= ?
                GROUP BY date(timestamp)
                ORDER BY date(timestamp) ASC
                """,
                (start_date.isoformat(),)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_breakdown(self, days: int = 7, group_by: str = "provider") -> List[Dict[str, Any]]:
        """過去 N 日間の内訳（プロバイダ別、モデル別）を取得"""
        if group_by not in ("provider", "model", "agent_name"):
            group_by = "provider"
            
        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f"""
                SELECT 
                    {group_by} as label,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(cost_usd) as cost_usd
                FROM usage_logs
                WHERE date(timestamp) >= ?
                GROUP BY {group_by}
                ORDER BY cost_usd DESC
                """,
                (start_date.isoformat(),)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_usage(self, limit: int = 10) -> List[Dict[str, Any]]:
        """最近のログを取得"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM usage_logs
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

_global_store: Optional[UsageStore] = None

def get_usage_store() -> UsageStore:
    """グローバルな UsageStore インスタンスを取得"""
    global _global_store
    if _global_store is None:
        _global_store = UsageStore()
    return _global_store
