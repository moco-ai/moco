from typing import Optional, Dict, Any
import logging
from .base import MocoTool, read_file, write_file, edit_file, execute_bash
from ..storage.scheduled_task_store import ScheduledTaskStore

logger = logging.getLogger(__name__)

class ScheduleTaskTool(MocoTool):
    """
    タスクの定期実行（スケジュール）を予約・管理するためのツール。
    """
    name = "schedule_task"
    description = "タスクの定期実行を予約します。引数には指示内容(description)とCron形式のスケジュール(cron)を指定します。"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.store = ScheduledTaskStore()

    async def execute(self, description: str, cron: str, task_id: Optional[str] = None, profile: str = "default") -> str:
        """
        タスクを予約します。
        
        Args:
            description: 実行するタスクの内容（プロンプト）
            cron: Cron形式のスケジュール設定 (例: '0 8 * * *' は毎日8時, '*/15 * * * *' は15分ごと)
            task_id: 任意のタスクID。省略時は自動生成。
            profile: 使用するプロファイル名。
            
        Returns:
            予約完了メッセージ、またはエラーメッセージ。
        """
        import uuid
        if not task_id:
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            
        try:
            # Cron のバリデーション（croniter内部で行われるが、ここで軽くチェック）
            from croniter import croniter
            from datetime import datetime
            if not croniter.is_valid(cron):
                return f"❌ エラー: Cron形式 '{cron}' が正しくありません。"
                
            success = self.store.add_task(
                task_id=task_id,
                description=description,
                cron=cron,
                profile=profile
            )
            
            if success:
                iter = croniter(cron, datetime.now())
                next_run = iter.get_next(datetime).strftime("%Y-%m-%d %H:%M:%S")
                return f"✅ タスクを予約しました。\nID: {task_id}\n内容: {description}\nスケジュール: {cron}\n次回実行: {next_run}"
            else:
                return "❌ タスクの予約に失敗しました。"
        except Exception as e:
            logger.error(f"Error in ScheduleTaskTool: {e}")
            return f"❌ エラーが発生しました: {str(e)}"

class ListScheduledTasksTool(MocoTool):
    """
    現在予約されているタスクの一覧を表示します。
    """
    name = "list_scheduled_tasks"
    description = "現在予約されている定期実行タスクの一覧を表示します。"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.store = ScheduledTaskStore()

    async def execute(self) -> str:
        tasks = self.store.get_enabled_tasks()
        if not tasks:
            return "現在予約されているタスクはありません。"
            
        result = "📋 予約済みタスク一覧:\n"
        for t in tasks:
            status = "✅" if t['enabled'] else "❌"
            result += f"- [{t['id']}] {status} {t['description']} ({t['cron']}) | 次回: {t['next_run']}\n"
        return result

class RemoveScheduledTaskTool(MocoTool):
    """
    予約されているタスクを削除、または無効化します。
    """
    name = "remove_scheduled_task"
    description = "指定したIDの定期実行タスクを削除（または無効化）します。"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.store = ScheduledTaskStore()

    async def execute(self, task_id: str, action: str = "delete") -> str:
        """
        タスクを削除または無効化します。
        
        Args:
            task_id: 削除・操作対象のタスクID
            action: 'delete'（削除）, 'disable'（無効化）, 'enable'（有効化）
        """
        try:
            if action == "delete":
                success = self.store.delete_task(task_id)
                msg = "削除"
            elif action == "disable":
                success = self.store.set_task_enabled(task_id, False)
                msg = "無効化"
            elif action == "enable":
                success = self.store.set_task_enabled(task_id, True)
                msg = "有効化"
            else:
                return f"❌ エラー: アクション '{action}' は無効です。"

            if success:
                return f"✅ タスク {task_id} を{msg}しました。"
            else:
                return f"❌ タスク {task_id} が見つかりませんでした。"
        except Exception as e:
            logger.error(f"Error in RemoveScheduledTaskTool: {e}")
            return f"❌ エラーが発生しました: {str(e)}"

# --- Helper functions for discovery ---

async def schedule_task(description: str, cron: str, task_id: Optional[str] = None, profile: str = "default") -> str:
    """タスクの定期実行を予約します。"""
    return await ScheduleTaskTool().execute(description, cron, task_id, profile)

async def list_scheduled_tasks() -> str:
    """現在予約されている定期実行タスクの一覧を表示します。"""
    return await ListScheduledTasksTool().execute()

async def remove_scheduled_task(task_id: str, action: str = "delete") -> str:
    """指定したIDの定期実行タスクを削除（または無効化）します。"""
    return await RemoveScheduledTaskTool().execute(task_id, action)
