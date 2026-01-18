"""バックグラウンドプロセス管理ツール"""
import subprocess
import threading
import time
import re
import os
from collections import deque
from typing import Dict, Optional, List
from dataclasses import dataclass, field
try:
    from .base import is_dangerous_command
except ImportError:
    from moco.tools.base import is_dangerous_command

# プロセス出力バッファの最大行数。
PROCESS_OUTPUT_BUFFER_SIZE = 1000

@dataclass
class ProcessInfo:
    pid: int
    name: str
    process: subprocess.Popen
    output: deque = field(default_factory=lambda: deque(maxlen=PROCESS_OUTPUT_BUFFER_SIZE))
    status: str = "running"
    lock: threading.Lock = field(default_factory=threading.Lock)

_processes: Dict[int, ProcessInfo] = {}

def _read_output(proc_info: ProcessInfo):
    """プロセス出力を非同期で読み取るスレッド"""
    process = proc_info.process
    for line in iter(process.stdout.readline, b''):
        with proc_info.lock:
            proc_info.output.append(line.decode('utf-8', errors='replace').rstrip())
    process.wait()
    with proc_info.lock:
        proc_info.status = "stopped"

def start_background(command: str, name: str = None, cwd: str = None, allow_dangerous: bool = False) -> dict:
    """コマンドをバックグラウンドで実行
    
    Args:
        command: 実行するコマンド
        name: プロセスの識別名（省略時はコマンドのベース名）
        cwd: 作業ディレクトリ
        allow_dangerous: Trueの場合、危険なコマンドの実行を許可する
    """
    # 危険なコマンドのチェック
    if not allow_dangerous:
        is_dangerous, reason = is_dangerous_command(command)
        if is_dangerous:
            return {"error": f"Command blocked for security reasons. {reason}"}

    process = subprocess.Popen(
        command,
        shell=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
    )
    
    # プロセス名の機密情報漏洩対策：コマンドの先頭30文字ではなく、最初の単語かベース名を使用
    default_name = os.path.basename(command.split()[0]) if command else "process"
    
    proc_info = ProcessInfo(
        pid=process.pid,
        name=name or default_name,
        process=process,
    )
    _processes[process.pid] = proc_info
    thread = threading.Thread(target=_read_output, args=(proc_info,), daemon=True)
    thread.start()
    return {"pid": process.pid, "name": proc_info.name, "status": "running"}

def stop_process(pid: int) -> dict:
    """プロセスを停止"""
    if pid not in _processes:
        return {"error": f"Process {pid} not found"}
    proc_info = _processes[pid]
    proc_info.process.terminate()
    try:
        proc_info.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc_info.process.kill()
    with proc_info.lock:
        proc_info.status = "stopped"
    return {"pid": pid, "status": "stopped"}

def list_processes() -> list:
    """実行中のバックグラウンドプロセス一覧"""
    result = []
    for pid, info in _processes.items():
        poll = info.process.poll()
        status = "stopped" if poll is not None else "running"
        result.append({"pid": pid, "name": info.name, "status": status})
    return result

def get_output(pid: int, lines: int = 50) -> str:
    """プロセスの出力を取得（最新N行）"""
    if pid not in _processes:
        return f"Process {pid} not found"
    with _processes[pid].lock:
        output_lines = list(_processes[pid].output)[-lines:]
    return "\n".join(output_lines)

def wait_for_pattern(pid: int, pattern: str, timeout: int = 30) -> dict:
    """特定のパターンが出力されるまで待機"""
    if pid not in _processes:
        return {"found": False, "error": f"Process {pid} not found"}
    start = time.time()
    while time.time() - start < timeout:
        with _processes[pid].lock:
            for line in _processes[pid].output:
                if pattern in line:
                    return {"found": True, "line": line, "timeout": False}
        time.sleep(0.1)
    return {"found": False, "line": None, "timeout": True}


def wait_for_exit(pid: int, timeout: int = 300) -> dict:
    """プロセスが終了するまで待機する"""
    if pid not in _processes:
        return {"error": f"Process {pid} not found"}

    proc_info = _processes[pid]
    start = time.time()

    while time.time() - start < timeout:
        exit_code = proc_info.process.poll()
        if exit_code is not None:
            with proc_info.lock:
                proc_info.status = "stopped"
            return {"exited": True, "exit_code": exit_code, "timeout": False}
        time.sleep(0.5)

    return {"exited": False, "exit_code": None, "timeout": True}


def send_input(pid: int, text: str) -> dict:
    """バックグラウンドプロセスの stdin に入力を送る"""
    if pid not in _processes:
        return {"error": f"Process {pid} not found"}

    proc_info = _processes[pid]

    # プロセスがまだ動いているか確認
    if proc_info.process.poll() is not None:
        return {"error": f"Process {pid} has already terminated"}

    # stdin が利用可能か確認
    if proc_info.process.stdin is None:
        return {"error": f"Process {pid} does not have stdin available"}

    try:
        # テキストを送信（改行を追加）
        input_bytes = (text + "\n").encode('utf-8')
        proc_info.process.stdin.write(input_bytes)
        proc_info.process.stdin.flush()
        return {"sent": True, "text": text}
    except Exception as e:
        return {"error": f"Failed to send input: {e}"}
