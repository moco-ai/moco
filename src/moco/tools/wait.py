"""シンプルな待機ツール"""
import time


def wait(seconds: float) -> str:
    """
    指定した秒数だけ待機します。
    
    APIレート制限の回避、外部システムの処理完了待ち、
    タイミング調整などに使用します。
    
    Args:
        seconds: 待機する秒数（最大300秒 = 5分）
    
    Returns:
        完了メッセージ
        
    Examples:
        wait(3)      # 3秒待つ
        wait(0.5)    # 0.5秒待つ
        wait(60)     # 1分待つ
    """
    try:
        seconds = float(seconds)
        # 上限チェック（5分まで）
        if seconds > 300:
            return f"Error: Maximum wait time is 300 seconds (5 minutes). Requested: {seconds}"
        if seconds < 0:
            return "Error: Wait time cannot be negative."
        
        time.sleep(seconds)
        return f"Waited {seconds} seconds."
    except (ValueError, TypeError) as e:
        return f"Error: Invalid seconds value: {e}"
