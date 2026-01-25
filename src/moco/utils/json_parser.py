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
        end_idx = -1
        if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
            start_idx = start_obj
            end_idx = clean_text.rfind('}')
        elif start_arr != -1:
            start_idx = start_arr
            end_idx = clean_text.rfind(']')
            
        if start_idx != -1 and end_idx == -1:
            # Opening brace/bracket exists but closing is missing: incomplete JSON fragment
            return default
        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            clean_text = clean_text[start_idx:end_idx+1]
        elif start_idx == -1:
            # { も [ もない場合は JSON ではない
            logger.debug(f"No JSON structure found in text: {clean_text[:100]}")
            return default
            
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
        except json.JSONDecodeError:
            pass
        
        # 5. 各種表記の正規化
        try:
            # キー部分のシングルクォートをダブルクォートに変換
            # 例: {'key': 'value'} -> {"key": 'value'}
            fixed = re.sub(r"([{,])\s*'([^'\" ]+)'\s*:", r'\1"\2":', clean_text)
            
            # 値部分のシングルクォートをダブルクォートに変換（前後に : と , または } がある場合）
            # 例: {"key": 'value'} -> {"key": "value"}
            fixed = re.sub(r':\s*\'([^\']*)\'\s*([,}])', r': "\1"\2', fixed)
            
            # 値部分のうち、クォートされていない True/False/None を Python 形式から JSON 形式へ
            fixed = re.sub(r':\s*True\b', ': true', fixed)
            fixed = re.sub(r':\s*False\b', ': false', fixed)
            fixed = re.sub(r':\s*None\b', ': null', fixed)
            
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        # 6. Python リテラルとしてパースを試みる (最終手段に近い)
        try:
            import ast
            # ast.literal_eval は Python のリテラル（辞書、リスト、文字列、数値、True/False/None）を安全に評価できる
            data = ast.literal_eval(clean_text)
            # JSON 互換の型のみであることを確認（念のため）
            return json.loads(json.dumps(data))
        except (ValueError, SyntaxError, TypeError):
            pass

        # 7. クォートされていないキー名にダブルクォートを追加
        # 例: {path: "file.md"} → {"path": "file.md"}
        try:
            fixed = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', clean_text)
            fixed = re.sub(r'\bTrue\b', 'true', fixed)
            fixed = re.sub(r'\bFalse\b', 'false', fixed)
            fixed = re.sub(r'\bNone\b', 'null', fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        # 7. 複合的な修正（再帰的ではないが、よくあるパターンを網羅）
        try:
            # 1. キーをダブルクォート
            fixed = re.sub(r"([{,])\s*'?([a-zA-Z_][a-zA-Z0-9_]*)'?\s*:", r'\1"\2":', clean_text)
            # 2. 値の True/False/None を変換
            fixed = re.sub(r':\s*True\b', ': true', fixed)
            fixed = re.sub(r':\s*False\b', ': false', fixed)
            fixed = re.sub(r':\s*None\b', ': null', fixed)
            # 3. リスト内の True/False/None を変換
            fixed = re.sub(r'([\[,])\s*True\b', r'\1 true', fixed)
            fixed = re.sub(r'([\[,])\s*False\b', r'\1 false', fixed)
            fixed = re.sub(r'([\[,])\s*None\b', r'\1 null', fixed)
            # 4. 末尾カンマを除去
            fixed = re.sub(r",\s*([\]}])", r"\1", fixed)
            
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse JSON even after cleanup. Error: {e}")
            logger.debug(f"Original text: {clean_text[:200]}")
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
