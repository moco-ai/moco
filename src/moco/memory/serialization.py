from __future__ import annotations

import json
from typing import List


def serialize_embedding(emb: List[float]) -> str:
    """embeddingをJSON文字列に変換"""
    return json.dumps(emb) if emb else ""


def deserialize_embedding(emb_str: str) -> List[float]:
    """JSON文字列からembeddingを復元"""
    if not emb_str:
        return []
    try:
        return json.loads(emb_str)
    except Exception:
        return []


def deserialize_keywords(kw_str: str) -> List[str]:
    """JSON文字列からキーワードリストを復元"""
    if not kw_str:
        return []
    try:
        return json.loads(kw_str)
    except Exception:
        return []












