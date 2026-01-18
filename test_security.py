import sys
import os

# プロジェクトルートをパスに追加
sys.path.append(os.path.join(os.getcwd(), "src"))

from moco.tools.base import execute_bash
from moco.tools.process import start_background

def test_security():
    print("--- Testing execute_bash ---")
    
    # 危険なコマンド (rm -rf /)
    print("Testing 'rm -rf /' (should be blocked)...")
    result = execute_bash("rm -rf /")
    print(f"Result: {result}")
    
    # 危険なコマンド (ホームディレクトリ削除)
    print("\nTesting 'rm -rf ~' (should be blocked)...")
    result = execute_bash("rm -rf ~")
    print(f"Result: {result}")
    
    # 通常のコマンド
    print("\nTesting 'ls' (should pass)...")
    result = execute_bash("ls -l | head -n 5")
    print(f"Result: {result}")

    print("\n--- Testing start_background ---")
    
    # 危険なコマンド
    print("Testing 'rm -rf /' in background (should be blocked)...")
    result = start_background("rm -rf /")
    print(f"Result: {result}")
    
    # 明示的に許可
    print("\nTesting 'rm -rf /' with allow_dangerous=True (should attempt to run, but we won't actually do it for safety in this test script)...")
    # 代わりに、パターンにマッチするが無害な文字列を使って、allow_dangerous=True の挙動を確認
    # (実際には base.py の DANGEROUS_PATTERNS にマッチすればブロックされる)
    
    # ここでは 'ls' を allow_dangerous=True で呼んでも普通に動くことを確認
    result = start_background("ls", allow_dangerous=True)
    print(f"Result: {result}")

if __name__ == "__main__":
    test_security()
