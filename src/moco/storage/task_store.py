import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from enum import Enum
import uuid

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TaskStore:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            # プロジェクトルートの .moco ディレクトリを使用するか、ホームディレクトリを使用するか
            # ここでは ~/.moco/tasks.db をデフォルトとする
            db_path = Path.home() / ".moco" / "tasks.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    task_description TEXT NOT NULL,
                    profile TEXT,
                    provider TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result TEXT,
                    error TEXT,
                    pid INTEGER,
                    working_dir TEXT
                )
            """)
            # working_dir カラムがない場合は追加（既存DB対応）
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN working_dir TEXT")
            except sqlite3.OperationalError:
                pass  # カラムが既に存在する場合は無視
            conn.commit()

    def add_task(self, description: str, profile: Optional[str] = None, provider: Optional[str] = None, working_dir: Optional[str] = None) -> str:
        task_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, task_description, profile, provider, status, created_at, working_dir) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, description, profile, provider, TaskStatus.PENDING.value, now, working_dir)
            )
            conn.commit()
        return task_id

    def update_task(self, task_id: str, **kwargs):
        allowed_fields = {
            "status", "started_at", "completed_at", "result", "error", "pid"
        }
        updates = []
        values = []
        for k, v in kwargs.items():
            if k in allowed_fields:
                updates.append(f"{k} = ?")
                if isinstance(v, (dict, list)):
                    values.append(json.dumps(v))
                elif isinstance(v, Enum):
                    values.append(v.value)
                else:
                    values.append(v)
        
        if not updates:
            return

        values.append(task_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?",
                tuple(values)
            )
            conn.commit()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]

    def delete_task(self, task_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            conn.commit()
