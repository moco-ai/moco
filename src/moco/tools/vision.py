# -*- coding: utf-8 -*-
"""画像解析ツール（Vision API統一インターフェース）"""
import base64
import ipaddress
import os
import re
import socket
from typing import Optional
from urllib.parse import urlparse

# オプショナル依存の遅延インポート
_GENAI_AVAILABLE = None
_OPENAI_AVAILABLE = None


def _check_genai() -> bool:
    """google.generativeaiが利用可能か確認"""
    global _GENAI_AVAILABLE
    if _GENAI_AVAILABLE is None:
        try:
            from google import genai  # noqa: F401
            _GENAI_AVAILABLE = True
        except ImportError:
            _GENAI_AVAILABLE = False
    return _GENAI_AVAILABLE


def _check_openai() -> bool:
    """openaiが利用可能か確認"""
    global _OPENAI_AVAILABLE
    if _OPENAI_AVAILABLE is None:
        try:
            from openai import OpenAI  # noqa: F401
            _OPENAI_AVAILABLE = True
        except ImportError:
            _OPENAI_AVAILABLE = False
    return _OPENAI_AVAILABLE


# 画像の拡張子
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# MIMEタイプマッピング
EXTENSION_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _is_url(source: str) -> bool:
    """URLかどうか判定"""
    return source.startswith(("http://", "https://"))


def _is_private_ip(url: str) -> bool:
    """URLがプライベートIPを指しているか判定（SSRF対策）"""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True

        # localhost チェック
        if hostname in ("localhost", "127.0.0.1", "::1"):
            return True

        # IP アドレスの場合
        try:
            ip = ipaddress.ip_address(hostname)
            return ip.is_private or ip.is_loopback or ip.is_reserved
        except ValueError:
            # ホスト名の場合は DNS 解決
            try:
                ip_str = socket.gethostbyname(hostname)
                ip = ipaddress.ip_address(ip_str)
                return ip.is_private or ip.is_loopback or ip.is_reserved
            except socket.gaierror:
                return False
    except Exception:
        return True  # 判定できない場合は安全側に倒す


def _is_base64(source: str) -> bool:
    """Base64文字列かどうか判定"""
    # data URI scheme
    if source.startswith("data:image/"):
        return True
    # 純粋なBase64（長い文字列で、Base64文字のみ）
    if len(source) > 100:
        base64_pattern = re.compile(r"^[A-Za-z0-9+/=]+$")
        # 最初の1000文字で判定（パフォーマンス考慮）
        return bool(base64_pattern.match(source[:1000]))
    return False


def _is_file_path(source: str) -> bool:
    """ファイルパスかどうか判定"""
    return os.path.isfile(source)


def _get_mime_type_from_path(file_path: str) -> str:
    """ファイルパスからMIMEタイプを取得"""
    ext = os.path.splitext(file_path)[1].lower()
    return EXTENSION_TO_MIME.get(ext, "image/png")


def _load_image_as_base64(file_path: str) -> tuple[str, str]:
    """ファイルを読み込んでBase64とMIMEタイプを返す"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Image file not found: {file_path}")

    mime_type = _get_mime_type_from_path(file_path)

    with open(file_path, "rb") as f:
        image_data = f.read()

    return base64.b64encode(image_data).decode("utf-8"), mime_type


def _parse_base64_source(source: str) -> tuple[str, str]:
    """Base64ソースをパースしてデータとMIMEタイプを返す"""
    # data URI scheme: data:image/png;base64,xxxxx
    if source.startswith("data:"):
        match = re.match(r"data:(image/[^;]+);base64,(.+)", source)
        if match:
            return match.group(2), match.group(1)
    # 純粋なBase64
    return source, "image/png"  # デフォルトはPNG


def _detect_provider() -> Optional[str]:
    """環境変数からプロバイダを自動検出"""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GENAI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    return None


def _analyze_with_gemini(
    image_source: str,
    question: str,
    base64_data: Optional[str] = None,
    mime_type: str = "image/png",
) -> str:
    """Gemini Vision APIで画像を解析"""
    if not _check_genai():
        return "Error: google-generativeai is not installed. Install with: pip install google-generativeai"

    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GENAI_API_KEY")
    if not api_key:
        return "Error: GEMINI_API_KEY, GOOGLE_API_KEY, or GENAI_API_KEY environment variable not set"

    client = genai.Client(api_key=api_key)
    model = os.environ.get("GEMINI_VISION_MODEL", "gemini-2.0-flash")

    # コンテンツを構築
    parts = []

    if _is_url(image_source) and not base64_data:
        # URLの場合: Gemini はURLを直接処理できないため、ダウンロードが必要
        # SSRF対策
        if _is_private_ip(image_source):
            return "Error: Access to private/internal URLs is not allowed"
        try:
            import urllib.request
            with urllib.request.urlopen(image_source, timeout=30) as response:
                image_data = response.read()
                base64_data = base64.b64encode(image_data).decode("utf-8")
                # URLから拡張子を推測
                parsed = urlparse(image_source)
                ext = os.path.splitext(parsed.path)[1].lower()
                mime_type = EXTENSION_TO_MIME.get(ext, "image/png")
        except Exception as e:
            return f"Error downloading image from URL: {e}"

    if base64_data:
        parts = [
            types.Part(
                inline_data=types.Blob(
                    mime_type=mime_type,
                    data=base64.b64decode(base64_data),
                )
            ),
            types.Part(text=question),
        ]
    else:
        # base64_dataがない場合（通常は発生しない）
        parts = [types.Part(text=question)]

    try:
        response = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
        )

        if response.candidates and response.candidates[0].content:
            texts = [
                part.text
                for part in response.candidates[0].content.parts
                if part.text
            ]
            return "\n".join(texts)
        return "Error: No response from Gemini"

    except Exception as e:
        return f"Error calling Gemini Vision API: {e}"


def _analyze_with_openai(
    image_source: str,
    question: str,
    base64_data: Optional[str] = None,
    mime_type: str = "image/png",
    is_openrouter: bool = False,
) -> str:
    """OpenAI/OpenRouter Vision APIで画像を解析"""
    if not _check_openai():
        return "Error: openai is not installed. Install with: pip install openai"

    from openai import OpenAI

    if is_openrouter:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        base_url = "https://openrouter.ai/api/v1"
        model = os.environ.get("OPENROUTER_VISION_MODEL", "openai/gpt-4o")
        if not api_key:
            return "Error: OPENROUTER_API_KEY environment variable not set"
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        base_url = None
        model = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")
        if not api_key:
            return "Error: OPENAI_API_KEY environment variable not set"

    client = OpenAI(api_key=api_key, base_url=base_url)

    # コンテンツを構築
    content = []

    # 画像部分
    if _is_url(image_source):
        # SSRF対策
        if _is_private_ip(image_source):
            return "Error: Access to private/internal URLs is not allowed"
        content.append({
            "type": "image_url",
            "image_url": {"url": image_source},
        })
    elif base64_data:
        data_url = f"data:{mime_type};base64,{base64_data}"
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url},
        })

    # テキスト部分
    content.append({
        "type": "text",
        "text": question,
    })

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
            max_tokens=1024,
        )

        if response.choices and response.choices[0].message:
            return response.choices[0].message.content or ""
        return "Error: No response from API"

    except Exception as e:
        provider_name = "OpenRouter" if is_openrouter else "OpenAI"
        return f"Error calling {provider_name} Vision API: {e}"


def analyze_image(
    image_source: str,
    question: str = "この画像を詳しく説明してください",
    provider: Optional[str] = None,
) -> str:
    """
    画像を解析し、質問に対する回答を返す。

    対応プロバイダ:
    - gemini: Gemini Vision API (gemini-2.0-flash)
    - openai: OpenAI Vision API (gpt-4o)
    - openrouter: OpenRouter経由のVision API

    Args:
        image_source: 画像のパス、URL、またはBase64文字列
        question: 画像に対する質問
        provider: 使用するプロバイダ（gemini/openai/openrouter）
                  Noneの場合は環境変数から自動選択

    Returns:
        str: 画像の分析結果

    Examples:
        # ファイルパスから
        analyze_image("screenshot.png", "この画面の内容を説明してください")

        # URLから
        analyze_image("https://example.com/image.jpg", "何が写っていますか？")

        # Base64から
        analyze_image("data:image/png;base64,iVBOR...", "この図の意味は？")

        # プロバイダ指定
        analyze_image("image.png", provider="openai")
    """
    # 入力バリデーション
    if not image_source:
        return "Error: image_source is required"

    if not question:
        question = "この画像を詳しく説明してください"

    # プロバイダの決定
    if provider is None:
        provider = _detect_provider()
        if provider is None:
            return (
                "Error: No API key found. Please set one of: "
                "GEMINI_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY"
            )

    provider = provider.lower()
    if provider not in ("gemini", "openai", "openrouter"):
        return f"Error: Unsupported provider: {provider}. Use gemini, openai, or openrouter"

    # 画像ソースの処理
    base64_data: Optional[str] = None
    mime_type = "image/png"

    if _is_url(image_source):
        # URLはそのまま（OpenAI系）またはダウンロード（Gemini）
        pass
    elif _is_base64(image_source):
        # Base64文字列
        base64_data, mime_type = _parse_base64_source(image_source)
    elif _is_file_path(image_source):
        # ファイルパス
        try:
            base64_data, mime_type = _load_image_as_base64(image_source)
        except FileNotFoundError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading image file: {e}"
    else:
        return f"Error: Invalid image source. Not a valid URL, file path, or Base64 string: {image_source[:100]}..."

    # プロバイダ別の処理
    if provider == "gemini":
        return _analyze_with_gemini(image_source, question, base64_data, mime_type)
    elif provider == "openai":
        return _analyze_with_openai(image_source, question, base64_data, mime_type, is_openrouter=False)
    elif provider == "openrouter":
        return _analyze_with_openai(image_source, question, base64_data, mime_type, is_openrouter=True)

    return f"Error: Unknown provider: {provider}"
