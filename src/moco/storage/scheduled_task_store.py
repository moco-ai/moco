import sqlite3
import os
from datetime import datetime
from typing import List, Optional, Dict
from croniter import croniter

class ScheduledTaskStore:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # 作業ディレクトリ基準でDBパスを決定
            base_dir = os.environ.get("MOCO_WORKING_DIRECTORY", os.getcwd())
            db_path = os.path.join(base_dir, "tasks.db")
        
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """テーブルの初期化（存在しない場合のみ）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    cron TEXT NOT NULL,
                    profile TEXT,
                    next_run TEXT,
                    last_run TEXT,
                    enabled INTEGER DEFAULT 1,
                    working_dir TEXT
                )
            """)
            conn.commit()

    def add_task(self, task_id: str, description: str, cron: str, profile: str = "default") -> bool:
        """新規予約タスクの追加"""
        working_dir = os.environ.get("MOCO_WORKING_DIRECTORY", os.getcwd())
        
        # 次回実行時刻の計算
        now = datetime.now()
        iter = croniter(cron, now)
        next_run = iter.get_next(datetime).isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO scheduled_tasks (id, description, cron, profile, working_dir, enabled, next_run)
                VALUES (?, ?, ?, ?, ?, 1, ?)
            """, (task_id, description, cron, profile, working_dir, next_run))
            conn.commit()
        return True

    def get_enabled_tasks(self) -> List[Dict]:
        """有効なタスク一覧を取得"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM scheduled_tasks WHERE enabled = 1")
            return [dict(row) for row in cursor.fetchall()]

    def get_due_tasks(self) -> List[Dict]:
        """実行時刻が到来している、有効なタスクを取得"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM scheduled_tasks 
                WHERE enabled = 1 AND next_run <= ?
            """, (now,))
            return [dict(row) for row in cursor.fetchall()]

    def complete_task(self, task_id: str):
        """タスク完了時の処理。last_runを更新し、次回実行時刻を再計算する"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT cron FROM scheduled_tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            if not row:
                return

            cron = row["cron"]
            now = datetime.now()
            iter = croniter(cron, now)
            next_run = iter.get_next(datetime).isoformat()
            last_run = now.isoformat()

            conn.execute("""
                UPDATE scheduled_tasks 
                SET last_run = ?, next_run = ? 
                WHERE id = ?
            """, (last_run, next_run, task_id))
            conn.commit()

    def update_next_run(self, task_id: str, next_run: datetime):
        """次回実行予定時刻の更新"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE scheduled_tasks SET next_run = ? WHERE id = ?", 
                        (next_run.isoformat(), task_id))
            conn.commit()

    def delete_task(self, task_id: str) -> bool:
        """タスクを削除する"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    def set_task_enabled(self, task_id: str, enabled: bool) -> bool:
        """タスクの有効/無効を切り替える"""
        val = 1 if enabled else 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("UPDATE scheduled_tasks SET enabled = ? WHERE id = ?", (val, task_id))
            conn.commit()
            return cursor.rowcount > 0
