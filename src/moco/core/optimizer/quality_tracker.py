"""
QualityTracker - メトリクス記録

セッションのメトリクスを記録し、分析用データを蓄積する。
全プロファイル共通のDBで管理し、プロファイル間比較を可能にする。
"""

import os
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

from .task_analyzer import TaskScores
from .agent_selector import SelectionResult


@dataclass
class ExecutionMetrics:
    """実行メトリクス"""
    tokens: int = 0                  # 使用トークン数
    duration: float = 0.0            # 実行時間（秒）
    tool_calls: int = 0              # ツール呼び出し回数
    errors: int = 0                  # エラー数
    retries: int = 0                 # リトライ回数
    has_apology: bool = False        # 謝罪文が含まれるか
    exit_code: int = 0               # 終了コード
    has_negative_keywords: bool = False  # ネガティブなキーワードが含まれるか


@dataclass
class AgentExecutionMetrics:
    """エージェント単位の実行メトリクス"""
    agent_name: str
    parent_agent: Optional[str] = None    # 委譲元エージェント
    tokens_input: int = 0                 # 入力トークン数
    tokens_output: int = 0                # 出力トークン数
    execution_time_ms: int = 0            # 実行時間（ミリ秒）
    tool_calls: int = 0                   # ツール呼び出し回数
    inline_score: Optional[float] = None  # インライン評価スコア (0-1) ※互換性維持
    # インライン評価の詳細項目
    eval_completion: Optional[int] = None      # タスク完遂度 (0-10)
    eval_quality: Optional[int] = None         # コード/回答の質 (0-10)
    eval_task_complexity: Optional[int] = None # タスク複雑性 (1-10)
    eval_prompt_specificity: Optional[int] = None  # 指示の具体性 (0-10)
    # 要約・コンテキスト関連
    summary_depth: int = 0                # このエージェントの要約回数
    history_turns: int = 0                # 履歴ターン数
    error_message: Optional[str] = None   # エラーメッセージ


class QualityTracker:
    """メトリクスの記録と分析"""

    @staticmethod
    def _default_db_path() -> Path:
        """デフォルトのDBパスを取得（環境変数 > cwd）"""
        if os.environ.get("MOCO_DATA_DIR"):
            return Path(os.environ["MOCO_DATA_DIR"]) / "optimizer" / "metrics.db"
        return Path.cwd() / "data" / "optimizer" / "metrics.db"
    
    DB_PATH = None  # 遅延評価のためNone
    DB_TIMEOUT = 30.0  # SQLite接続タイムアウト（秒）

    def __init__(self, db_path: Optional[Path] = None, timeout: float = None):
        """
        Args:
            db_path: DBファイルパス（省略時はデフォルト）
            timeout: SQLite接続タイムアウト（秒）
        """
        self.db_path = db_path or self._default_db_path()
        self.timeout = timeout or self.DB_TIMEOUT
        self._init_db()

    def _init_db(self) -> None:
        """DBスキーマを初期化"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    session_id TEXT,

                    -- タスク情報
                    task_summary TEXT,
                    task_type TEXT,

                    -- スコア
                    score_scope INTEGER,
                    score_novelty REAL,
                    score_risk INTEGER,
                    score_complexity INTEGER,
                    score_dependencies INTEGER,
                    score_total INTEGER,

                    -- 選択結果
                    depth TEXT,
                    agents_selected TEXT,
                    agents_skipped TEXT,

                    -- 実行結果
                    tokens_used INTEGER,
                    duration_seconds REAL,
                    tool_calls INTEGER,
                    error_count INTEGER,
                    retry_count INTEGER,

                    -- 成功判定
                    success_inferred REAL,
                    success_user INTEGER,
                    ai_score REAL,

                    -- 閾値（記録時点）
                    thresholds_snapshot TEXT
                )
            ''')

            # カラム追加（既存DBへの対応）
            new_columns = [
                ('ai_score', 'REAL'),
                ('task_complexity', 'REAL'),
                ('todo_used', 'INTEGER'),
                ('delegation_count', 'INTEGER'),
                ('input_length', 'INTEGER'),
                ('output_length', 'INTEGER'),
                ('prompt_specificity', 'REAL'),
                ('history_turns', 'INTEGER'),
                ('summary_depth', 'INTEGER'),
            ]
            for col_name, col_type in new_columns:
                try:
                    conn.execute(f'ALTER TABLE metrics ADD COLUMN {col_name} {col_type}')
                except sqlite3.OperationalError:
                    pass  # 既に存在する場合

            # エージェント別実行メトリクステーブル（新規）
            conn.execute('''
                CREATE TABLE IF NOT EXISTS agent_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    parent_agent TEXT,
                    tokens_input INTEGER DEFAULT 0,
                    tokens_output INTEGER DEFAULT 0,
                    execution_time_ms INTEGER DEFAULT 0,
                    tool_calls INTEGER DEFAULT 0,
                    inline_score REAL,
                    eval_completion INTEGER,
                    eval_quality INTEGER,
                    eval_task_complexity INTEGER,
                    eval_prompt_specificity INTEGER,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (request_id) REFERENCES metrics(id)
                )
            ''')
            
            # 既存テーブルへのカラム追加（マイグレーション）
            migration_cols = [
                ('eval_completion', 'INTEGER'),
                ('eval_quality', 'INTEGER'),
                ('eval_task_complexity', 'INTEGER'),
                ('eval_prompt_specificity', 'INTEGER'),
                ('summary_depth', 'INTEGER DEFAULT 0'),
                ('history_turns', 'INTEGER DEFAULT 0'),
            ]
            for col, col_type in migration_cols:
                try:
                    conn.execute(f'ALTER TABLE agent_executions ADD COLUMN {col} {col_type}')
                except sqlite3.OperationalError:
                    pass  # 既に存在する場合

            # インデックス作成
            conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics(timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_profile ON metrics(profile)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_depth ON metrics(depth)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_task_type ON metrics(task_type)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_agent_request ON agent_executions(request_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_agent_name ON agent_executions(agent_name)')

            conn.commit()

    def record(
        self,
        profile: str,
        session_id: str,
        task_summary: str,
        scores: TaskScores,
        selection: SelectionResult,
        execution: ExecutionMetrics,
        thresholds: Dict[str, int],
        ai_score: Optional[float] = None,
        task_complexity: Optional[int] = None,
        prompt_specificity: Optional[float] = None,
        todo_used: Optional[int] = None,
        delegation_count: Optional[int] = None,
        input_length: Optional[int] = None,
        output_length: Optional[int] = None,
        history_turns: Optional[int] = None,
        summary_depth: Optional[int] = None
    ) -> int:
        """
        セッション結果を記録

        Returns:
            記録ID
        """
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO metrics (
                    timestamp, profile, session_id,
                    task_summary, task_type,
                    score_scope, score_novelty, score_risk,
                    score_complexity, score_dependencies, score_total,
                    depth, agents_selected, agents_skipped,
                    tokens_used, duration_seconds, tool_calls,
                    error_count, retry_count,
                    success_inferred, ai_score, thresholds_snapshot,
                    task_complexity, todo_used, delegation_count,
                    input_length, output_length, prompt_specificity,
                    history_turns, summary_depth
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now().isoformat(),
                profile,
                session_id,
                task_summary[:200],  # 長すぎる場合は切り詰め
                scores.get("task_type", "other"),
                scores.get("scope", 5),
                scores.get("novelty", 0.5),
                scores.get("risk", 5),
                scores.get("complexity", 5),
                scores.get("dependencies", 3),
                selection.total_score,
                selection.depth,
                json.dumps(selection.agents),
                json.dumps(selection.skipped),
                execution.tokens,
                execution.duration,
                execution.tool_calls,
                execution.errors,
                execution.retries,
                self._infer_success(execution),
                ai_score,
                json.dumps(thresholds),
                task_complexity,
                todo_used,
                delegation_count,
                input_length,
                output_length,
                prompt_specificity,
                history_turns,
                summary_depth
            ))

            record_id = cursor.lastrowid
            conn.commit()

        return record_id

    def record_agent_execution(
        self,
        request_id: int,
        agent: AgentExecutionMetrics
    ) -> int:
        """
        エージェント単位の実行メトリクスを記録

        Args:
            request_id: 親リクエストのID（metricsテーブルのid）
            agent: エージェント実行メトリクス

        Returns:
            記録ID
        """
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO agent_executions (
                    request_id, agent_name, parent_agent,
                    tokens_input, tokens_output, execution_time_ms,
                    tool_calls, inline_score,
                    eval_completion, eval_quality, eval_task_complexity, eval_prompt_specificity,
                    summary_depth, history_turns,
                    error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                request_id,
                agent.agent_name,
                agent.parent_agent,
                agent.tokens_input,
                agent.tokens_output,
                agent.execution_time_ms,
                agent.tool_calls,
                agent.inline_score,
                agent.eval_completion,
                agent.eval_quality,
                agent.eval_task_complexity,
                agent.eval_prompt_specificity,
                agent.summary_depth,
                agent.history_turns,
                agent.error_message,
                datetime.now().isoformat()
            ))
            record_id = cursor.lastrowid
            conn.commit()
        return record_id

    def record_agent_executions(
        self,
        request_id: int,
        agents: List[AgentExecutionMetrics]
    ) -> List[int]:
        """
        複数エージェントの実行メトリクスを一括記録

        Args:
            request_id: 親リクエストのID
            agents: エージェント実行メトリクスのリスト

        Returns:
            記録IDのリスト
        """
        record_ids = []
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            cursor = conn.cursor()
            for agent in agents:
                cursor.execute('''
                    INSERT INTO agent_executions (
                        request_id, agent_name, parent_agent,
                        tokens_input, tokens_output, execution_time_ms,
                        tool_calls, inline_score,
                        eval_completion, eval_quality, eval_task_complexity, eval_prompt_specificity,
                        summary_depth, history_turns,
                        error_message, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    request_id,
                    agent.agent_name,
                    agent.parent_agent,
                    agent.tokens_input,
                    agent.tokens_output,
                    agent.execution_time_ms,
                    agent.tool_calls,
                    agent.inline_score,
                    agent.eval_completion,
                    agent.eval_quality,
                    agent.eval_task_complexity,
                    agent.eval_prompt_specificity,
                    agent.summary_depth,
                    agent.history_turns,
                    agent.error_message,
                    datetime.now().isoformat()
                ))
                record_ids.append(cursor.lastrowid)
            conn.commit()
        return record_ids

    def get_agent_stats(
        self,
        days: int = 30,
        profile: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        エージェント別の統計を取得

        Returns:
            {agent_name: {total, avg_score, avg_tokens, avg_time, success_rate, ...}}
        """
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            conn.row_factory = sqlite3.Row
            since = (datetime.now() - timedelta(days=days)).isoformat()

            query = '''
                SELECT
                    ae.agent_name,
                    COUNT(*) as total,
                    AVG(ae.inline_score) as avg_score,
                    AVG(ae.tokens_input + ae.tokens_output) as avg_tokens,
                    AVG(ae.execution_time_ms) as avg_time_ms,
                    SUM(CASE WHEN ae.inline_score >= 0.7 THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN ae.error_message IS NOT NULL THEN 1 ELSE 0 END) as error_count
                FROM agent_executions ae
                JOIN metrics m ON ae.request_id = m.id
                WHERE ae.created_at >= ?
            '''
            params: List[Any] = [since]

            if profile:
                query += ' AND m.profile = ?'
                params.append(profile)

            query += ' GROUP BY ae.agent_name ORDER BY total DESC'

            cursor = conn.execute(query, params)
            result = {}
            for row in cursor.fetchall():
                total = row["total"]
                result[row["agent_name"]] = {
                    "total": total,
                    "avg_score": round(row["avg_score"] or 0, 2),
                    "avg_tokens": round(row["avg_tokens"] or 0),
                    "avg_time_ms": round(row["avg_time_ms"] or 0),
                    "success_rate": round((row["success_count"] / total * 100) if total > 0 else 0, 1),
                    "error_rate": round((row["error_count"] / total * 100) if total > 0 else 0, 1)
                }
            return result

    def get_delegation_chain(
        self,
        request_id: int
    ) -> List[Dict[str, Any]]:
        """
        特定リクエストの委譲チェーンを取得

        Returns:
            [{"agent": "orchestrator", "parent": None, "score": 0.8, ...}, ...]
        """
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM agent_executions
                WHERE request_id = ?
                ORDER BY id ASC
            ''', [request_id])

            return [dict(row) for row in cursor.fetchall()]

    def _infer_success(self, execution: ExecutionMetrics) -> float:
        """成功度を推定 (0-1)"""
        # キルスイッチ: 致命的な失敗がある場合は即座に 0.0
        if execution.exit_code != 0 or execution.has_negative_keywords:
            return 0.0

        score = 1.0

        # エラーによる減点（強化: 1つでもあれば成功とは見なさない）
        if execution.errors > 0:
            score -= 0.8 * min(execution.errors, 3)

        # リトライによる減点
        if execution.retries > 2:
            score -= 0.2

        # 謝罪による減点
        if execution.has_apology:
            score -= 0.2

        return max(0.0, score)

    def update_user_feedback(self, record_id: int, success: bool) -> None:
        """ユーザーからのフィードバックを記録"""
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            conn.execute(
                'UPDATE metrics SET success_user = ? WHERE id = ?',
                (1 if success else 0, record_id)
            )
            conn.commit()

    def get_stats(
        self,
        profile: Optional[str] = None,
        days: int = 30
    ) -> Dict[str, Any]:
        """統計情報を取得"""
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            conn.row_factory = sqlite3.Row

            since = (datetime.now() - timedelta(days=days)).isoformat()

            # 基本クエリ
            base_where = "WHERE timestamp >= ?"
            params = [since]

            if profile:
                base_where += " AND profile = ?"
                params.append(profile)

            # 総セッション数
            cursor = conn.execute(
                f'SELECT COUNT(*) as count FROM metrics {base_where}',
                params
            )
            total_sessions = cursor.fetchone()["count"]

            # 深度別統計
            cursor = conn.execute(f'''
                SELECT
                    depth,
                    COUNT(*) as count,
                    AVG(tokens_used) as avg_tokens,
                    AVG(duration_seconds) as avg_duration,
                    AVG(success_inferred) as avg_success
                FROM metrics
                {base_where}
                GROUP BY depth
            ''', params)

            depth_stats = {}
            for row in cursor.fetchall():
                depth_stats[row["depth"]] = {
                    "count": row["count"],
                    "avg_tokens": round(row["avg_tokens"] or 0),
                    "avg_duration": round(row["avg_duration"] or 0, 1),
                    "avg_success": round(row["avg_success"] or 0, 2)
                }

            # タスクタイプ別統計
            cursor = conn.execute(f'''
                SELECT
                    task_type,
                    COUNT(*) as count,
                    AVG(success_inferred) as avg_success
                FROM metrics
                {base_where}
                GROUP BY task_type
            ''', params)

            task_type_stats = {}
            for row in cursor.fetchall():
                task_type_stats[row["task_type"]] = {
                    "count": row["count"],
                    "avg_success": round(row["avg_success"] or 0, 2)
                }

        return {
            "total_sessions": total_sessions,
            "period_days": days,
            "profile": profile or "all",
            "by_depth": depth_stats,
            "by_task_type": task_type_stats
        }

    def get_all(
        self,
        days: int = 30,
        profile: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """全レコードを取得（分析用）"""
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            conn.row_factory = sqlite3.Row

            since = (datetime.now() - timedelta(days=days)).isoformat()

            query = 'SELECT * FROM metrics WHERE timestamp >= ?'
            params: List[Any] = [since]

            if profile:
                query += ' AND profile = ?'
                params.append(profile)

            query += ' ORDER BY timestamp DESC LIMIT ?'
            params.append(limit)

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def compare_profiles(
        self,
        task_type: Optional[str] = None,
        days: int = 30
    ) -> Dict[str, Dict[str, Any]]:
        """プロファイル間の比較"""
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            conn.row_factory = sqlite3.Row

            since = (datetime.now() - timedelta(days=days)).isoformat()

            query = '''
                SELECT
                    profile,
                    AVG(tokens_used) as avg_tokens,
                    AVG(duration_seconds) as avg_duration,
                    AVG(success_inferred) as avg_success,
                    COUNT(*) as count
                FROM metrics
                WHERE timestamp >= ?
            '''
            params: List[Any] = [since]

            if task_type:
                query += ' AND task_type = ?'
                params.append(task_type)

            query += ' GROUP BY profile'

            cursor = conn.execute(query, params)

            result = {}
            for row in cursor.fetchall():
                result[row["profile"]] = {
                    "avg_tokens": round(row["avg_tokens"] or 0),
                    "avg_duration": round(row["avg_duration"] or 0, 1),
                    "avg_success": round(row["avg_success"] or 0, 2),
                    "count": row["count"]
                }

        return result

    def get_tuning_stats(self, days: int = 30) -> Dict[str, Any]:
        """チューニング用の集約統計を取得（メモリ効率的）

        get_all() の代わりに SQL で直接集約し、大量データでもメモリを圧迫しない
        """
        with sqlite3.connect(self.db_path, timeout=self.timeout) as conn:
            conn.row_factory = sqlite3.Row
            since = (datetime.now() - timedelta(days=days)).isoformat()

            # 総レコード数
            cursor = conn.execute(
                'SELECT COUNT(*) as count FROM metrics WHERE timestamp >= ?',
                [since]
            )
            total_records = cursor.fetchone()["count"]

            # 深度別統計（スコアバケット付き）
            cursor = conn.execute('''
                SELECT
                    depth,
                    (score_total / 5) * 5 as score_bucket,
                    COUNT(*) as count,
                    AVG(success_inferred) as avg_success,
                    AVG(tokens_used) as avg_tokens
                FROM metrics
                WHERE timestamp >= ?
                GROUP BY depth, score_bucket
            ''', [since])

            by_depth_and_score = {}
            for row in cursor.fetchall():
                depth = row["depth"]
                bucket = row["score_bucket"]
                if bucket not in by_depth_and_score:
                    by_depth_and_score[bucket] = {}
                by_depth_and_score[bucket][depth] = {
                    "count": row["count"],
                    "avg_success": round(row["avg_success"] or 0, 3),
                    "avg_tokens": round(row["avg_tokens"] or 0)
                }

            # 深度別全体統計
            cursor = conn.execute('''
                SELECT
                    depth,
                    COUNT(*) as count,
                    AVG(success_inferred) as avg_success,
                    MIN(score_total) as min_score,
                    MAX(score_total) as max_score,
                    AVG(score_total) as avg_score
                FROM metrics
                WHERE timestamp >= ?
                GROUP BY depth
            ''', [since])

            depth_stats = {}
            for row in cursor.fetchall():
                depth_stats[row["depth"]] = {
                    "count": row["count"],
                    "avg_success": round(row["avg_success"] or 0, 3),
                    "min_score": row["min_score"],
                    "max_score": row["max_score"],
                    "avg_score": round(row["avg_score"] or 0, 1)
                }

        return {
            "total_records": total_records,
            "by_depth": depth_stats,
            "by_score_bucket": by_depth_and_score
        }
