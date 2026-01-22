import subprocess
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def execute_bash_in_sandbox(
    command: str,
    image: str = "python:3.12-slim",
    working_dir: Optional[str] = None,
    read_only: bool = False,
    network_disabled: bool = True,
    timeout: int = 60,
    memory_limit: str = "512m",
    cpu_limit: str = "0.5"
) -> str:
    """
    Dockerコンテナ内でBashコマンドを実行します。

    Args:
        command: 実行するコマンド
        image: Dockerイメージ名
        working_dir: ホスト側のディレクトリ（/workspaceにマウント）
        read_only: 読み取り専用でマウント
        network_disabled: ネットワーク無効化
        timeout: タイムアウト（秒）
        memory_limit: メモリ制限（例: "512m"）
        cpu_limit: CPU制限（例: "0.5"）

    Returns:
        実行結果の標準出力/標準エラー
    """
    if not working_dir:
        working_dir = os.getcwd()

    # ワークスペース外のマウントを禁止（セキュリティ）
    abs_working_dir = os.path.abspath(working_dir)
    try:
        from ..utils.path import get_working_directory
        allowed_dir = os.path.abspath(get_working_directory())
        if os.path.commonpath([abs_working_dir, allowed_dir]) != allowed_dir:
            return f"Error: Access to directory outside of workspace is denied: {abs_working_dir}"
    except ImportError:
        pass # テスト環境など

    # Dockerコマンドの構築
    docker_cmd = ["docker", "run", "--rm", "-i", "--init"]

    # リソース制限
    docker_cmd.extend(["--memory", memory_limit, "--cpus", cpu_limit])

    # セキュリティ硬化
    docker_cmd.extend(["--cap-drop", "ALL", "--security-opt", "no-new-privileges"])

    # 実行ユーザーの指定 (Linux/macOS)
    if os.name != 'nt' and hasattr(os, 'getuid'):
        docker_cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])

    # ネットワーク設定
    if network_disabled:
        docker_cmd.extend(["--network", "none"])

    # ワークスペースのマウント
    mode = "ro" if read_only else "rw"
    docker_cmd.extend(["-v", f"{abs_working_dir}:/workspace:{mode}"])
    docker_cmd.extend(["-w", "/workspace"])

    # イメージと実行コマンド
    docker_cmd.append(image)
    docker_cmd.extend(["bash", "-c", command])

    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"

        if result.returncode != 0:
            output += f"\nReturn Code: {result.returncode}"

        return output.strip() if output else "Command executed successfully in sandbox (no output)."

    except subprocess.TimeoutExpired:
        return f"Error: Sandbox command execution timed out ({timeout}s)."
    except FileNotFoundError:
        return "Error: 'docker' command not found. Please ensure Docker is installed and running."
    except Exception as e:
        return f"Error in sandbox execution: {e}"
