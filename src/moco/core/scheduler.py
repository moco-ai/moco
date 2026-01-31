import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

import httpx

from ..storage.scheduled_task_store import ScheduledTaskStore

logger = logging.getLogger(__name__)

# moco ui のAPI URL
MOCO_API_URL = os.environ.get("MOCO_API_URL", "http://localhost:8000/api/chat")

class MocoScheduler:
    """
    Moco スケジュール実行エンジン。
    定期的にデータベースをチェックし、実行時刻が到来したタスクを Orchestrator に渡す。
    """

    def __init__(
        self,
        orchestrator_factory,
        interval_seconds: int = 60,
        db_path: Optional[str] = None,
        after_execute_callback = None
    ):
        """
        Args:
            orchestrator_factory: Orchestratorのインスタンスを生成する呼び出し可能オブジェクト、
                                 または既存のOrchestrator。
                                 タスクごとに異なるprofileを適用するため、factoryが望ましい。
            interval_seconds: チェック間隔（秒）
            db_path: タスクDBのパス
            after_execute_callback: タスク完了時に呼ばれるコールバック (task_dict, result_text)
        """
        self.orchestrator_factory = orchestrator_factory
        self.interval_seconds = interval_seconds
        self.store = ScheduledTaskStore(db_path=db_path)
        self.after_execute_callback = after_execute_callback
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """スケジューラーをバックグラウンドで開始する"""
        if self._running:
            logger.warning("Scheduler is already running.")
            return

        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Moco Scheduler started.")

    async def stop(self):
        """スケジューラーを停止する"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Moco Scheduler stopped.")

    async def _loop(self):
        """メイン実行ループ"""
        while self._running:
            try:
                await self._check_and_execute_tasks()
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)
            
            await asyncio.sleep(self.interval_seconds)

    async def _check_and_execute_tasks(self):
        """期限が来たタスクをチェックして実行する"""
        due_tasks = self.store.get_due_tasks()
        if not due_tasks:
            return

        logger.info(f"Found {len(due_tasks)} due tasks.")

        for task in due_tasks:
            task_id = task['id']
            description = task['description']
            profile = task.get('profile', 'default')
            
            logger.info(f"Executing task {task_id}: {description} (profile: {profile})")
            
            try:
                # moco ui の /api/chat にリクエストを投げる（WhatsAppと同じフロー）
                # セッションIDはタスクごとに固定（履歴を引き継ぐ）
                # None を渡すと api.py が新しいセッションを作成する
                session_id = None
                working_dir = task.get('working_dir') or os.getcwd()
                
                payload = {
                    "message": description,
                    "session_id": session_id,
                    "profile": profile,
                    "working_directory": working_dir
                }
                
                # タイムアウト: 5分（複雑なタスクでも5分以内に完了すべき）
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    response = await client.post(MOCO_API_URL, json=payload)
                
                if response.status_code == 200:
                    data = response.json()
                    result = data.get("response", "")
                    artifacts = data.get("artifacts", [])
                    logger.debug(f"Task {task_id} result: {result[:100] if result else '(empty)'}...")
                    if artifacts:
                        logger.info(f"Task {task_id} generated {len(artifacts)} artifacts")
                else:
                    result = f"Error: {response.status_code}"
                    logger.error(f"Task {task_id} failed: {response.text[:200]}")
                
                # 完了通知と次回予定の更新
                self.store.complete_task(task_id)
                logger.info(f"Task {task_id} completed successfully.")

                # コールバックの実行（モバイル等への通知）
                if self.after_execute_callback:
                    try:
                        if asyncio.iscoroutinefunction(self.after_execute_callback):
                            await self.after_execute_callback(task, result)
                        else:
                            self.after_execute_callback(task, result)
                    except Exception as callback_err:
                        logger.error(f"Error in scheduler callback: {callback_err}")
                
            except Exception as e:
                logger.error(f"Failed to execute task {task_id}: {e}", exc_info=True)
                self.store.complete_task(task_id)

if __name__ == "__main__":
    # スケジューラを永続的に実行（moco ui の API 経由）
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    async def main():
        scheduler = MocoScheduler(
            orchestrator_factory=None,  # API経由なので不要
            interval_seconds=60  # 1分ごとにチェック
        )
        
        logger.info("Starting Moco Scheduler (API mode)...")
        logger.info(f"API URL: {MOCO_API_URL}")
        
        await scheduler.start()
        
        # 永続的に実行（Ctrl+C で停止）
        try:
            while True:
                await asyncio.sleep(3600)  # 1時間ごとにログ出力
                logger.info("Scheduler is running...")
        except asyncio.CancelledError:
            pass
        finally:
            await scheduler.stop()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")
