import os
from pathlib import Path
from dotenv import set_key, find_dotenv

class EnvManager:
    """.env ファイルの読み書きを管理するクラス"""
    
    def __init__(self, env_path: str = None):
        if env_path:
            self.env_path = Path(env_path)
        else:
            # find_dotenv(usecwd=True) が見つからない場合は、デフォルトでプロジェクトルートに作成
            found = find_dotenv(usecwd=True)
            if found:
                self.env_path = Path(found)
            else:
                # 暫定的に src の 2つ上のディレクトリ（プロジェクトルート）を想定
                self.env_path = Path(__file__).parent.parent.parent.parent / ".env"
        
        if not self.env_path.exists():
            self.env_path.touch()

    def update(self, key: str, value: str):
        """指定されたキーの値を更新または追加する。コメントは保持される傾向にある。"""
        set_key(str(self.env_path), key, value)
        # プロセス内の環境変数も更新
        os.environ[key] = value

    def get(self, key: str, default: str = None) -> str:
        return os.environ.get(key, default)

    def is_configured(self) -> bool:
        """必須設定が存在するか確認"""
        # .env が存在しない、または必須項目が欠けている場合に False を返す
        required = ["MOCO_WORKING_DIRECTORY", "LLM_PROVIDER"]
        
        # 既にメモリ上にロードされているかチェック
        configured = all(os.environ.get(k) for k in required)
        if configured:
            return True
            
        # ロードされていない場合、.env の中身を直接覗いてみる（find_dotenv済み前提）
        return False
