import subprocess
import os
import sys
import signal
from pathlib import Path
from datetime import datetime
from typing import Optional
from ..storage.task_store import TaskStore, TaskStatus

class TaskRunner:
    def __init__(self, task_store: Optional[TaskStore] = None):
        self.store = task_store or TaskStore()
        self.log_dir = Path.home() / ".moco" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run_task(self, task_id: str, profile: str, description: str, working_dir: Optional[str] = None):
        """
        タスクをバックグラウンドで実行する。
        実際には、自分自身を別のプロセスとして起動し、そこでタスクを実行させる。
        """
        log_file = self.log_dir / f"{task_id}.log"
        
        # 内部的に実行するためのコマンド
        # moco tasks _exec <task_id> <profile> <description> のような形を想定
        cmd = [
            sys.executable, "-m", "moco.cli", 
            "tasks", "_exec", 
            task_id, 
            profile, 
            description
        ]
        
        # 作業ディレクトリが指定されている場合は引数として追加
        if working_dir:
            cmd.extend(["--working-dir", working_dir])
        
        # PYTHONPATH を確実に引き継ぐ (開発環境用)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        current_pythonpath = env.get("PYTHONPATH", "")
        # src ディレクトリがあれば開発中とみなして追加
        src_path = Path(__file__).parent.parent.parent
        if (src_path / "moco").exists():
            src_path_str = str(src_path)
            if current_pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([src_path_str, current_pythonpath])
            else:
                env["PYTHONPATH"] = src_path_str

        # stdout/stderr をログファイルにリダイレクト
        # buffering=1 (行バッファリング) を指定
        log_f = open(log_file, "w", buffering=1)
        try:
            process = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env
            )
            
            self.store.update_task(
                task_id,
                pid=process.pid,
                status=TaskStatus.RUNNING,
                started_at=datetime.now().isoformat()
            )
        finally:
            # 親プロセス側では log_f を閉じて良い（子プロセスが引き継ぐ）
            log_f.close()

    def cancel_task(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if not task or not task.get("pid"):
            return False
        
        pid = task["pid"]
        try:
            # プロセスグループ全体を終了させる
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            self.store.update_task(
                task_id,
                status=TaskStatus.CANCELLED,
                completed_at=datetime.now().isoformat()
            )
            return True
        except ProcessLookupError:
            # 既に終了している場合
            self.store.update_task(task_id, status=TaskStatus.FAILED, error="Process not found")
            return False
        except Exception as e:
            self.store.update_task(task_id, error=str(e))
            return False

    def _find_log_file(self, task_id: str) -> Optional[Path]:
        """短縮IDまたは完全IDからログファイルを検索"""
        # task_id が英数字（とハイフン）のみであることを確認（パストラバーサル対策）
        if not all(c.isalnum() or c == '-' for c in task_id):
            return None

        # 完全一致を試す
        log_file = (self.log_dir / f"{task_id}.log").resolve()
        if log_file.exists() and str(log_file).startswith(str(self.log_dir.resolve())):
            return log_file

        # 前方一致で検索（短縮ID対応）
        for f in self.log_dir.glob(f"{task_id}*.log"):
            resolved = f.resolve()
            if str(resolved).startswith(str(self.log_dir.resolve())):
                return resolved

        return None

    def get_logs(self, task_id: str, max_bytes: int = 10000) -> str:
        log_file = self._find_log_file(task_id)
        if log_file is None:
            return "Log file not found."
        
        file_size = log_file.stat().st_size
        with open(log_file, "r") as f:
            if file_size > max_bytes:
                f.seek(file_size - max_bytes)
                return "...(truncated)...\n" + f.read()
            return f.read()

    def tail_logs(self, task_id: str):
        """
        ログを末尾まで表示し、更新を監視し続ける (tail -f 相当)
        """
        import time

        log_file = self._find_log_file(task_id)
        if log_file is None:
            print(f"Log file not found for task: {task_id}")
            return

        print(f"--- Following logs for task: {task_id} (Ctrl+C to stop) ---")

        with open(log_file, "r") as f:
            # 既存の内容を表示
            print(f.read(), end="")

            # 監視ループ
            try:
                while True:
                    line = f.readline()
                    if not line:
                        # まだ書き込みがあるかもしれないので少し待機
                        time.sleep(0.1)
                        continue
                    print(line, end="", flush=True)
            except KeyboardInterrupt:
                print("\nStopped.")

