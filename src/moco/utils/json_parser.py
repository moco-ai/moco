import json
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

class SmartJSONParser:
    """
    LLM が生成した不完全な JSON をパースするための頑健なパーサー。
    Markdown ブロックの抽出、末尾カンマの除去、引用符の補完などを試みる。
    """
    
    @staticmethod
    def parse(text: str, default: Optional[Any] = None) -> Any:
        """
        LLM の出力から JSON を抽出・解析する
        
        Args:
            text: LLM の出力テキスト
            default: パース失敗時に返すデフォルト値（None の場合は None を返す）
        
        Returns:
            パースされた JSON オブジェクト、または default
        """
        if not text or not text.strip():
            return default
            
        # 1. Markdown コードブロックの抽出
        # ```json ... ``` または ``` ... ```
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if json_match:
            clean_text = json_match.group(1).strip()
        else:
            clean_text = text.strip()
        
        # 空になった場合は default を返す
        if not clean_text:
            return default
            
        # 2. 最初と最後の括弧を探して抽出 (JSON オブジェクトまたは配列のみ)
        start_obj = clean_text.find('{')
        start_arr = clean_text.find('[')
        
        start_idx = -1
        if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
            start_idx = start_obj
            end_idx = clean_text.rfind('}')
        elif start_arr != -1:
            start_idx = start_arr
            end_idx = clean_text.rfind(']')
            
        if start_idx != -1 and end_idx != -1:
            clean_text = clean_text[start_idx:end_idx+1]
            
        # 3. 標準的な json.loads を試行
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            pass
            
        # 4. 基本的な修正を試みる
        try:
            # 末尾のカンマを除去 (], または },)
            fixed = re.sub(r",\s*([\]}])", r"\1", clean_text)
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON even after cleanup. Error: {e}")
            return default

    @staticmethod
    def extract_and_parse(text: str, key: str = None) -> Any:
        """
        テキストから JSON を抽出し、特定のキーがあればその値を、なければ全体を返す。
        """
        data = SmartJSONParser.parse(text)
        if data and key and isinstance(data, dict):
            return data.get(key)
        return data
