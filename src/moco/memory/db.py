from __future__ import annotations

import sqlite3


def init_db(db_path: str) -> None:
    """DBテーブルを初期化"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                run_id TEXT,
                content TEXT NOT NULL,
                type TEXT DEFAULT 'knowledge',
                keywords TEXT,
                questions TEXT,
                source TEXT,
                embedding TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
    )
    # Backward compatible migration: add run_id column for existing DBs
    try:
        cursor.execute("PRAGMA table_info(memories)")
        cols = [r[1] for r in cursor.fetchall()]
        if "run_id" not in cols:
            cursor.execute("ALTER TABLE memories ADD COLUMN run_id TEXT")
        if "questions" not in cols:
            cursor.execute("ALTER TABLE memories ADD COLUMN questions TEXT")
        if "router_id" not in cols:
            cursor.execute("ALTER TABLE memories ADD COLUMN router_id TEXT DEFAULT ''")
        if "worker_id" not in cols:
            cursor.execute("ALTER TABLE memories ADD COLUMN worker_id TEXT DEFAULT ''")
    except Exception:
        # If migration fails, keep running; run_id is optional.
        pass

    # Task run event table for precise correlation (no dedupe)
    cursor.execute(
        """
            CREATE TABLE IF NOT EXISTS task_run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                channel_id TEXT,
                router_id TEXT,
                skill_id TEXT,
                tool_name TEXT,
                params_json TEXT,
                result_preview TEXT,
                success INTEGER,
                error_type TEXT,
                recovery_hint TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
    )

    # インデックス作成
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel ON memories(channel_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_type ON memories(type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_id ON memories(run_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_router_id ON memories(router_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_worker_id ON memories(worker_id)")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_run_events_run_id ON task_run_events(run_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_run_events_created_at ON task_run_events(created_at)")

    # グラフ関係性テーブル
    cursor.execute(
        """
            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
            )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_memory_id ON relations(memory_id)")

    conn.commit()
    conn.close()


def get_conn(db_path: str) -> sqlite3.Connection:
    """DB接続を取得"""
    return sqlite3.connect(db_path)


