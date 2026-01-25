import os
from pathlib import Path
from dotenv import set_key, find_dotenv

class EnvManager:
    """.env ファイルの読み書きを管理するクラス"""
    
    def __init__(self, env_path: str = None):
        if env_path:
            self.env_path = Path(env_path)
        else:
            # プロジェクトルートにある .env を優先的に探す
            # 1. カレントディレクトリ
            # 2. find_dotenv (上位ディレクトリへの遡り)
            found = find_dotenv(usecwd=True)
            if found:
                self.env_path = Path(found)
            else:
                # 明示的にカレントディレクトリの .env をデフォルトとする
                self.env_path = Path.cwd() / ".env"
        
        if not self.env_path.exists():
            self.env_path.touch()

    def update(self, key: str, value: str):
        """指定されたキーの値を更新または追加する。"""
        set_key(str(self.env_path), key, value)
        # プロセス内の環境変数も更新
        os.environ[key] = value

    def get(self, key: str, default: str = None) -> str:
        return os.environ.get(key, default)

    def is_configured(self) -> bool:
        """必須設定が存在するか確認"""
        required = ["MOCO_WORKING_DIRECTORY", "LLM_PROVIDER"]
        
        # 1. 環境変数をチェック
        if all(os.environ.get(k) for k in required):
            return True
            
        # 2. 環境変数になければ、.env ファイルを直接パースしてチェック
        if self.env_path.exists():
            from dotenv import dotenv_values
            env_dict = dotenv_values(str(self.env_path))
            return all(env_dict.get(k) for k in required)

        return False
