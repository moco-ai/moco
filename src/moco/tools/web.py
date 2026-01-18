# -*- coding: utf-8 -*-
"""
Web関連ツール - Gemini Grounding ベース

BeautifulSoup や Google Custom Search API を使わず、
Gemini の Google Search Grounding 機能で Web 検索を実行
"""
import os
from typing import List, Optional

try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    genai = None
    types = None
    HAS_GENAI = False


def websearch(query: str, site_filter: Optional[str] = None) -> str:
    """
    Gemini の Google Search Grounding を使用して Web 検索を実行します。
    
    Args:
        query: 検索クエリ
        site_filter: 検索を制限するドメイン (例: "nta.go.jp")
        
    Returns:
        検索結果を含む回答（参照元 URL 付き）
    """
    if not HAS_GENAI:
        return "Error: google-genai がインストールされていません。pip install google-genai を実行してください。"
    
    api_key = os.getenv("GENAI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    
    if not api_key:
        return "Error: API キーが設定されていません (GENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY)"
    
    # サイト制限がある場合、クエリを修正
    search_query = query
    if site_filter:
        search_query = f"site:{site_filter} {query}"
    
    try:
        client = genai.Client(api_key=api_key)
        
        # Google Search Grounding を有効にして生成
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=search_query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        
        result_parts = []
        
        # メイン回答
        result_parts.append(response.text)
        
        # 参照元 URL を抽出
        sources = _extract_grounding_sources(response)
        if sources:
            result_parts.append("\n\n📚 参照元:")
            for source in sources[:5]:  # 最大5件
                result_parts.append(f"  - {source['title']}: {source['url']}")
        
        return "\n".join(result_parts)
        
    except Exception as e:
        return f"Error: Web検索に失敗しました: {e}"


def _extract_grounding_sources(response) -> List[dict]:
    """Grounding のソース情報を抽出"""
    sources = []
    
    try:
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'grounding_metadata') and candidate.grounding_metadata:
                metadata = candidate.grounding_metadata
                if hasattr(metadata, 'grounding_chunks') and metadata.grounding_chunks:
                    for chunk in metadata.grounding_chunks:
                        if hasattr(chunk, 'web') and chunk.web:
                            # Vertex AI のリダイレクト URL から実際の URL を取得するのは難しいので、
                            # タイトルとリダイレクト URL をそのまま使用
                            sources.append({
                                'title': chunk.web.title if hasattr(chunk.web, 'title') else 'Unknown',
                                'url': chunk.web.uri if hasattr(chunk.web, 'uri') else ''
                            })
    except Exception:
        pass  # メタデータ取得に失敗しても回答は返す
    
    return sources


def webfetch(url: str, question: Optional[str] = None) -> str:
    """
    指定した URL の内容を Gemini で要約して取得します。
    
    Args:
        url: 取得する URL
        question: URL の内容に対する質問（省略時は要約）
        
    Returns:
        URL の内容の要約または質問への回答
    """
    if not HAS_GENAI:
        return "Error: google-genai がインストールされていません。"
    
    api_key = os.getenv("GENAI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    
    if not api_key:
        return "Error: API キーが設定されていません"
    
    prompt = question or "この URL の内容を日本語で簡潔に要約してください。"
    full_prompt = f"以下の URL の内容について回答してください。\n\nURL: {url}\n\n質問: {prompt}"
    
    try:
        client = genai.Client(api_key=api_key)
        
        # Grounding で URL の内容を取得
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=full_prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        
        return f"URL: {url}\n\n{response.text}"
        
    except Exception as e:
        return f"Error: URL の取得に失敗しました: {e}"
