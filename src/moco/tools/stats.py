"""
エージェント統計ツール - Orchestrator が委譲判断に使用
"""

import os
from pathlib import Path
from typing import Optional

# 現在のセッションID（todo.py と同様のパターン）
_current_session_id: Optional[str] = None


def set_current_session(session_id: str) -> None:
    """現在のセッションIDを設定（Orchestrator から呼ばれる）"""
    global _current_session_id
    _current_session_id = session_id


def _get_tracker():
    """QualityTracker インスタンスを取得"""
    from ..core.optimizer.quality_tracker import QualityTracker
    
    # data ディレクトリのパスを解決
    data_dir = os.environ.get("MOCO_DATA_DIR")
    if not data_dir:
        data_dir = Path.cwd() / "data"
    else:
        data_dir = Path(data_dir)
    
    db_path = data_dir / "optimizer" / "metrics.db"
    return QualityTracker(db_path=str(db_path))


def get_agent_stats(days: int = 7) -> str:
    """
    各エージェントの統計情報を取得します。
    委譲先を決める際の参考にしてください。

    Args:
        days: 集計期間（デフォルト7日）

    Returns:
        エージェント別の統計（タスク数、成功率、平均トークン、平均時間）
    
    統計の見方：
    - total: タスク数（多い = よく使われている）
    - success_rate: 成功率（高い = 信頼できる）
    - avg_tokens: 平均トークン消費（低い = 効率的）
    - avg_time_ms: 平均処理時間
    - error_rate: エラー率（低い = 安定）
    
    委譲の判断基準：
    - 成功率が高く、負荷（total）が低いエージェントに優先的に振る
    - エラー率が高いエージェントは避けるか、簡単なタスクに限定
    """
    try:
        tracker = _get_tracker()
        stats = tracker.get_agent_stats(days=days)
        
        if not stats:
            return "統計データがありません。"
        
        lines = ["## エージェント統計（直近{}日）\n".format(days)]
        lines.append("| エージェント | タスク数 | 成功率 | 平均トークン | 平均時間 | エラー率 |")
        lines.append("|-------------|---------|--------|-------------|---------|---------|")
        
        for agent_name, data in stats.items():
            lines.append(
                f"| {agent_name} | {data['total']} | {data['success_rate']}% | "
                f"{data['avg_tokens']:,} | {data['avg_time_ms']/1000:.1f}s | {data['error_rate']}% |"
            )
        
        # 推奨コメント
        lines.append("\n### 💡 委譲の推奨")
        
        # 成功率が高く負荷が低いエージェントを探す
        available = []
        for name, data in stats.items():
            if data['success_rate'] >= 50 and data['total'] < 20:
                available.append((name, data['success_rate'], data['total']))
        
        if available:
            available.sort(key=lambda x: (-x[1], x[2]))  # 成功率高い順、タスク少ない順
            lines.append("- 推奨委譲先: " + ", ".join([f"**{a[0]}**({a[1]}%)" for a in available[:3]]))
        
        # 避けるべきエージェント
        avoid = [name for name, data in stats.items() if data['error_rate'] > 20 or data['success_rate'] < 30]
        if avoid:
            lines.append(f"- ⚠️ 注意が必要: {', '.join(avoid)}")
        
        return "\n".join(lines)
        
    except Exception as e:
        return f"統計取得エラー: {e}"


def get_session_stats() -> str:
    """
    現在のセッション内でのエージェント活動状況を取得します。
    このタスク内で誰が何回呼ばれたか、成功/失敗を確認できます。

    Returns:
        現在のセッション内での委譲状況
    
    使い方：
    - どのエージェントにすでに委譲したか確認
    - 同じエージェントに何度も振っていないかチェック
    - 失敗したエージェントを避ける
    """
    global _current_session_id
    
    if not _current_session_id:
        return "セッションが開始されていません。"
    
    try:
        # data ディレクトリのパスを解決
        data_dir = os.environ.get("MOCO_DATA_DIR")
        if not data_dir:
            data_dir = Path.cwd() / "data"
        else:
            data_dir = Path(data_dir)
        
        # セッション内のメッセージからエージェント活動を集計
        import sqlite3
        conn = sqlite3.connect(str(data_dir / "sessions.db"))
        conn.row_factory = sqlite3.Row
        
        # 現在のセッションとサブセッションの agent_id を集計
        query = """
            SELECT 
                agent_id,
                COUNT(*) as message_count,
                SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END) as responses
            FROM agent_messages
            WHERE session_id = ? AND agent_id IS NOT NULL
            GROUP BY agent_id
            ORDER BY message_count DESC
        """
        
        cursor = conn.execute(query, (_current_session_id,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return "このセッションではまだエージェント活動がありません。"
        
        lines = ["## 現在のセッション内活動状況\n"]
        lines.append("| エージェント | メッセージ数 | 応答数 |")
        lines.append("|-------------|-------------|--------|")
        
        for row in rows:
            agent = row["agent_id"] or "orchestrator"
            lines.append(f"| {agent} | {row['message_count']} | {row['responses']} |")
        
        return "\n".join(lines)
        
    except Exception as e:
        return f"セッション統計取得エラー: {e}"

