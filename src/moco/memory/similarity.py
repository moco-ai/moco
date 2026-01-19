from __future__ import annotations

import math
from typing import List


def cos_sim(a: List[float], b: List[float]) -> float:
    """コサイン類似度を計算"""
    if not a or not b:
        return 0.0
    d = sum(x * y for x, y in zip(a, b))
    m1 = math.sqrt(sum(x * x for x in a))
    m2 = math.sqrt(sum(y * y for y in b))
    return d / (m1 * m2) if m1 and m2 else 0.0












