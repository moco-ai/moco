"""
コンテキスト圧縮モジュール。

会話履歴がトークン上限に近づいた際に、古いメッセージを要約して圧縮する。
システムプロンプトと直近のメッセージは保持し、中間部分をLLMで要約する。
"""

import os
import logging
from typing import List, Dict, Any, Tuple, Optional

# Gemini
from google import genai
from google.genai import types

# OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

logger = logging.getLogger(__name__)


class ContextCompressor:
    """
    会話履歴の自動圧縮を行うクラス。

    トークン数がしきい値を超えた場合、古いメッセージを要約して
    コンテキストウィンドウ内に収める。

    圧縮戦略:
    - システムメッセージは常に保持
    - 直近 preserve_recent 件のメッセージは保持
    - それより古いメッセージは要約して1つの assistant メッセージに圧縮
    """

    # トークン推定用の係数（日本語は1文字≒1.5トークン、英語は1単語≒1.3トークン）
    # 安全マージンを含めて文字数の1.5倍で推定
    TOKEN_ESTIMATE_RATIO = 1.5

    def __init__(
        self,
        max_tokens: int = 200000,
        preserve_recent: int = 10,
        summary_model: Optional[str] = None,
        compression_ratio: float = 0.5
    ):
        """
        Args:
            max_tokens: 圧縮を開始するトークン数のしきい値
            preserve_recent: 常に保持する直近メッセージ数
            summary_model: 要約に使用するモデル名（省略時は自動選択）
            compression_ratio: 圧縮後の目標サイズ比率（元の何割に圧縮するか）
        """
        from .llm_provider import get_analyzer_model
        self.max_tokens = max_tokens
        self.preserve_recent = preserve_recent
        self.summary_model = summary_model or get_analyzer_model()
        self.compression_ratio = compression_ratio

        # 要約用クライアントの初期化（遅延初期化）
        self._gemini_client: Optional[genai.Client] = None
        self._openai_client: Optional["OpenAI"] = None

    def _get_gemini_client(self) -> genai.Client:
        """Geminiクライアントを取得（遅延初期化）"""
        if self._gemini_client is None:
            api_key = (
                os.environ.get("GENAI_API_KEY") or
                os.environ.get("GEMINI_API_KEY") or
                os.environ.get("GOOGLE_API_KEY")
            )
            if not api_key:
                raise ValueError(
                    "Gemini API key not found. Set GENAI_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY"
                )
            self._gemini_client = genai.Client(api_key=api_key)
        return self._gemini_client

    def _get_openai_client(self) -> "OpenAI":
        """OpenAIクライアントを取得（遅延初期化）"""
        if self._openai_client is None:
            if not OPENAI_AVAILABLE:
                raise ImportError(
                    "OpenAI package not installed. Run: pip install openai"
                )
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OpenAI API key not found. Set OPENAI_API_KEY environment variable"
                )
            self._openai_client = OpenAI(api_key=api_key)
        return self._openai_client

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        メッセージリストのトークン数を推定する。

        正確なトークンカウントにはトークナイザーが必要だが、
        ここでは文字数ベースの簡易推定を行う。

        Args:
            messages: メッセージのリスト（OpenAI形式またはGemini形式）

        Returns:
            推定トークン数
        """
        total_chars = 0

        for msg in messages:
            # OpenAI形式: {"role": "...", "content": "..."}
            if "content" in msg:
                content = msg.get("content", "")
                if content:
                    total_chars += len(str(content))

            # Gemini形式: {"role": "...", "parts": [...]}
            if "parts" in msg:
                parts = msg.get("parts", [])
                for part in parts:
                    if isinstance(part, str):
                        total_chars += len(part)
                    elif isinstance(part, dict) and "text" in part:
                        total_chars += len(part["text"])
                    elif hasattr(part, "text") and part.text:
                        total_chars += len(part.text)

            # ツール呼び出し結果なども考慮
            if "tool_calls" in msg:
                for tc in msg.get("tool_calls", []):
                    if isinstance(tc, dict) and "function" in tc:
                        args = tc["function"].get("arguments", "")
                        total_chars += len(str(args))

        return int(total_chars * self.TOKEN_ESTIMATE_RATIO)

    def _extract_content(self, msg: Dict[str, Any]) -> str:
        """メッセージからテキストコンテンツを抽出"""
        # OpenAI形式
        if "content" in msg and msg["content"]:
            return str(msg["content"])

        # Gemini形式
        if "parts" in msg:
            parts = msg.get("parts", [])
            texts = []
            for part in parts:
                if isinstance(part, str):
                    texts.append(part)
                elif isinstance(part, dict) and "text" in part:
                    texts.append(part["text"])
                elif hasattr(part, "text") and part.text:
                    texts.append(part.text)
            return "\n".join(texts)

        return ""

    def _is_system_message(self, msg: Dict[str, Any]) -> bool:
        """システムメッセージかどうかを判定"""
        role = msg.get("role", "")
        return role == "system"

    def _format_messages_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        """要約用にメッセージを整形"""
        formatted = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = self._extract_content(msg)
            if content:
                # 長すぎるメッセージは切り詰め
                if len(content) > 2000:
                    content = content[:2000] + "...(省略)"
                formatted.append(f"[{role}]: {content}")
        return "\n\n".join(formatted)

    def _summarize_with_gemini(self, text: str) -> str:
        """Geminiで要約を生成"""
        client = self._get_gemini_client()

        prompt = f"""以下の会話履歴を簡潔に要約してください。
要約には以下を**必ず**含めてください：
1. 重要な決定事項や結論
2. 議論されたコンテキストや背景
3. 未完了のタスクや継続中の話題
4. **発見・使用されたファイルパスやディレクトリ構造**（フルパスで記載）
5. **プロジェクトのルートディレクトリ**

特に、ファイルパスは要約から省略しないでください。後続の作業で必要になります。

会話履歴:
---
{text}
---

要約（箇条書きで簡潔に）:"""

        try:
            response = client.models.generate_content(
                model=self.summary_model,
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=types.GenerateContentConfig(temperature=0.3)
            )

            if response.candidates and response.candidates[0].content:
                parts = response.candidates[0].content.parts or []
                texts = [p.text for p in parts if p.text]
                return "\n".join(texts)
        except Exception as e:
            logger.warning(f"Failed to summarize with Gemini: {e}")

        return ""

    def _summarize_with_openai(self, text: str) -> str:
        """OpenAIで要約を生成"""
        client = self._get_openai_client()

        prompt = f"""以下の会話履歴を簡潔に要約してください。
要約には以下を**必ず**含めてください：
1. 重要な決定事項や結論
2. 議論されたコンテキストや背景
3. 未完了のタスクや継続中の話題
4. **発見・使用されたファイルパスやディレクトリ構造**（フルパスで記載）
5. **プロジェクトのルートディレクトリ**

特に、ファイルパスは要約から省略しないでください。後続の作業で必要になります。

会話履歴:
---
{text}
---

要約（箇条書きで簡潔に）:"""

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",  # 要約には軽量モデルを使用
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"Failed to summarize with OpenAI: {e}")

        return ""

    def _generate_summary(self, messages: List[Dict[str, Any]], provider: str) -> str:
        """
        メッセージリストの要約を生成する。

        Args:
            messages: 要約対象のメッセージリスト
            provider: 使用するLLMプロバイダ ("gemini", "openai", "openrouter")

        Returns:
            要約テキスト
        """
        text = self._format_messages_for_summary(messages)

        if not text.strip():
            return ""

        # プロバイダに応じて要約を生成
        if provider in ("openai", "openrouter"):
            return self._summarize_with_openai(text)
        else:
            return self._summarize_with_gemini(text)

    def compress_if_needed(
        self,
        messages: List[Dict[str, Any]],
        provider: str = "gemini"
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        必要に応じてメッセージリストを圧縮する。

        Args:
            messages: 元のメッセージリスト
            provider: LLMプロバイダ名

        Returns:
            (圧縮後のメッセージリスト, 圧縮が行われたかどうか)
        """
        if not messages:
            return messages, False

        # トークン数を推定
        estimated_tokens = self.estimate_tokens(messages)

        if estimated_tokens <= self.max_tokens:
            logger.debug(f"No compression needed: {estimated_tokens} tokens <= {self.max_tokens}")
            return messages, False

        logger.info(f"Compressing context: {estimated_tokens} tokens > {self.max_tokens}")

        # システムメッセージを分離
        system_messages = []
        non_system_messages = []

        for msg in messages:
            if self._is_system_message(msg):
                system_messages.append(msg)
            else:
                non_system_messages.append(msg)

        # 保持するメッセージ数が全体より多い場合は圧縮しない
        if len(non_system_messages) <= self.preserve_recent:
            logger.debug("Not enough messages to compress")
            return messages, False

        # 直近のメッセージを保持
        recent_messages = non_system_messages[-self.preserve_recent:]
        messages_to_compress = non_system_messages[:-self.preserve_recent]

        # 圧縮対象が少なすぎる場合はスキップ
        if len(messages_to_compress) < 3:
            logger.debug("Too few messages to compress")
            return messages, False

        # 要約を生成
        summary = self._generate_summary(messages_to_compress, provider)

        if not summary:
            logger.warning("Failed to generate summary, returning original messages")
            return messages, False

        # 圧縮後のメッセージリストを構築
        compressed_messages = list(system_messages)

        # 要約を assistant メッセージとして追加
        summary_prefix = "[以前の会話の要約]\n"
        compressed_messages.append({
            "role": "assistant",
            "content": summary_prefix + summary
        })

        # 直近のメッセージを追加
        compressed_messages.extend(recent_messages)

        # 圧縮後のトークン数をログ
        new_token_count = self.estimate_tokens(compressed_messages)
        logger.info(
            f"Compressed: {estimated_tokens} -> {new_token_count} tokens "
            f"({len(messages)} -> {len(compressed_messages)} messages)"
        )

        return compressed_messages, True
