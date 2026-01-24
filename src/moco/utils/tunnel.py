import os
import subprocess
import logging
import time

logger = logging.getLogger(__name__)

# トンネルプロセスを保持
_tunnel_process = None

def setup_tunnel(port: int):
    """
    環境変数 MOCO_TUNNEL_TYPE に基づいてトンネルを開始する。
    
    Supported types:
    - tailscale: tailscale funnel {port}
    - cloudflare: cloudflared tunnel --url http://localhost:{port}
    - none: 何もしない
    """
    global _tunnel_process
    tunnel_type = os.getenv("MOCO_TUNNEL_TYPE", "none").lower()
    
    if tunnel_type == "none":
        logger.info("Tunnel type is 'none'. Skipping tunnel setup.")
        return

    command = []
    if tunnel_type == "tailscale":
        command = ["tailscale", "funnel", str(port)]
    elif tunnel_type == "cloudflare":
        command = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
    else:
        logger.warning(f"Unsupported tunnel type: {tunnel_type}")
        return

    logger.info(f"Starting {tunnel_type} tunnel on port {port}...")
    try:
        # バックグラウンドで実行
        _tunnel_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        # 少し待って即座にエラーが出ていないか確認
        time.sleep(2)
        if _tunnel_process.poll() is not None:
            stdout, stderr = _tunnel_process.communicate()
            logger.error(f"Tunnel process exited immediately with code {_tunnel_process.returncode}")
            logger.error(f"STDOUT: {stdout}")
            logger.error(f"STDERR: {stderr}")
            _tunnel_process = None
        else:
            logger.info(f"{tunnel_type} tunnel started in background (PID: {_tunnel_process.pid})")
            
    except Exception as e:
        logger.error(f"Failed to start tunnel: {e}")
        _tunnel_process = None

def stop_tunnel():
    """
    起動中のトンネルプロセスを終了する。
    """
    global _tunnel_process
    if _tunnel_process:
        logger.info(f"Stopping tunnel process (PID: {_tunnel_process.pid})...")
        try:
            _tunnel_process.terminate()
            _tunnel_process.wait(timeout=5)
            logger.info("Tunnel process terminated.")
        except subprocess.TimeoutExpired:
            logger.warning("Tunnel process did not terminate in time. Killing it...")
            _tunnel_process.kill()
            _tunnel_process.wait()
            logger.info("Tunnel process killed.")
        except Exception as e:
            logger.error(f"Error stopping tunnel: {e}")
        finally:
            _tunnel_process = None

