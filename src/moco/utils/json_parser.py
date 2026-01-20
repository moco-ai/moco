<<<<<<< HEAD
=======
"""
SmartJSONParser: LLM が生成した不正な JSON を修正して解析するユーティリティ

zai プロバイダーなど、一部の LLM は response_mime_type="application/json" を
指定しても正規の JSON を返さないことがあります。このパーサーは以下を処理します：

1. Markdown コードブロック（```json ... ```）の抽出
2. 末尾カンマの除去
3. JSON オブジェクト/配列の抽出
4. コメントの除去
"""

>>>>>>> bdea061 (fix: improve JSON parsing robustness and profile switching)
import json
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

<<<<<<< HEAD
class SmartJSONParser:
    """
    LLM が生成した不完全な JSON をパースするための頑健なパーサー。
    Markdown ブロックの抽出、末尾カンマの除去、引用符の補完などを試みる。
    """
    
    @staticmethod
    def parse(text: str) -> Optional[Any]:
        if not text:
            return None
            
        # 1. Markdown コードブロックの抽出
        # ```json ... ``` または ``` ... ```
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if json_match:
            clean_text = json_match.group(1).strip()
        else:
            clean_text = text.strip()
            
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
            return None

    @staticmethod
    def extract_and_parse(text: str, key: str = None) -> Any:
        """
        テキストから JSON を抽出し、特定のキーがあればその値を、なければ全体を返す。
        """
        data = SmartJSONParser.parse(text)
        if data and key and isinstance(data, dict):
            return data.get(key)
        return data
=======

class SmartJSONParser:
    """LLM が生成した不正な JSON を修正して解析するパーサー"""

    @staticmethod
    def parse(text: str, default: Optional[Any] = None) -> Any:
        """
        LLM の出力から JSON を抽出・解析する

        Args:
            text: LLM の出力テキスト
            default: パース失敗時に返すデフォルト値（None の場合は例外を投げる）

        Returns:
            パースされた JSON オブジェクト

        Raises:
            json.JSONDecodeError: パース失敗時（default が None の場合）
        """
        if not text or not text.strip():
            if default is not None:
                return default
            raise json.JSONDecodeError("Empty input", text or "", 0)

        original_text = text
        
        # 1. まず標準の json.loads を試す
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. Markdown コードブロックを抽出
        text = SmartJSONParser._extract_from_codeblock(text)

        # 3. 再度試す
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 4. JSON オブジェクト/配列を抽出
        text = SmartJSONParser._extract_json_structure(text)

        # 5. 末尾カンマを除去
        text = SmartJSONParser._remove_trailing_commas(text)

        # 6. コメントを除去
        text = SmartJSONParser._remove_comments(text)

        # 7. 最終パース
        try:
            result = json.loads(text)
            logger.debug(f"SmartJSONParser: Successfully parsed after cleanup")
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"SmartJSONParser: Failed to parse JSON: {e}")
            logger.debug(f"SmartJSONParser: Original text: {original_text[:500]}...")
            if default is not None:
                return default
            raise

    @staticmethod
    def _extract_from_codeblock(text: str) -> str:
        """Markdown コードブロックから JSON を抽出"""
        # ```json ... ``` または ``` ... ```
        patterns = [
            r'```json\s*\n?(.*?)\n?```',
            r'```\s*\n?(.*?)\n?```',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return text

    @staticmethod
    def _extract_json_structure(text: str) -> str:
        """テキストから JSON オブジェクトまたは配列を抽出"""
        text = text.strip()
        
        # 先頭の { または [ を探す
        obj_start = text.find('{')
        arr_start = text.find('[')

        if obj_start == -1 and arr_start == -1:
            return text

        # どちらが先か
        if arr_start == -1 or (obj_start != -1 and obj_start < arr_start):
            # オブジェクト
            start = obj_start
            open_char, close_char = '{', '}'
        else:
            # 配列
            start = arr_start
            open_char, close_char = '[', ']'

        # 対応する閉じ括弧を探す
        depth = 0
        in_string = False
        escape = False
        end = start

        for i, c in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == open_char:
                depth += 1
            elif c == close_char:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if depth == 0 and end > start:
            return text[start:end]
        return text

    @staticmethod
    def _remove_trailing_commas(text: str) -> str:
        """末尾カンマを除去"""
        # },] または },} または ],] または ],} の前のカンマ
        text = re.sub(r',\s*([}\]])', r'\1', text)
        return text

    @staticmethod
    def _remove_comments(text: str) -> str:
        """JavaScript スタイルのコメントを除去"""
        # 単一行コメント // ...
        text = re.sub(r'//[^\n]*', '', text)
        # 複数行コメント /* ... */
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        return text
>>>>>>> bdea061 (fix: improve JSON parsing robustness and profile switching)
