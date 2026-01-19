import json
import hashlib
import pytest
from moco.core.runtime import ToolCallTracker, StreamPrintState, _ensure_jsonable

def test_tool_call_tracker_sha256():
    tracker = ToolCallTracker()
    tool_name = "test_tool"
    # 100文字を超える引数
    args = {"data": "a" * 101}
    args_str = json.dumps(args, sort_keys=True, default=str)
    expected_hash = hashlib.sha256(args_str.encode()).hexdigest()
    
    key = tracker._make_key(tool_name, args)
    assert key == f"{tool_name}:hash:{expected_hash}"

def test_tool_call_tracker_loop_detection():
    tracker = ToolCallTracker(max_repeats=2, window_size=5)
    tool_name = "test_tool"
    args = {"x": 1}
    
    # 1回目: 許可
    allowed, msg = tracker.check_and_record(tool_name, args)
    assert allowed is True
    
    # 2回目: 許可
    allowed, msg = tracker.check_and_record(tool_name, args)
    assert allowed is True
    
    # 3回目: ブロック
    allowed, msg = tracker.check_and_record(tool_name, args)
    assert allowed is False
    assert "ループ検出" in msg

def test_stream_print_state_reset():
    StreamPrintState.broken = True
    StreamPrintState.reset()
    assert StreamPrintState.broken is False

def test_ensure_jsonable():
    # JSON化可能な場合
    assert _ensure_jsonable({"a": 1}) == {"a": 1}
    
    # JSON化不可能な場合（例：セット）
    val = {1, 2, 3}
    result = _ensure_jsonable(val)
    assert isinstance(result, str)
    assert str(val) == result

def test_tool_call_tracker_exception_handling():
    tracker = ToolCallTracker()
    # json.dumps が失敗するような引数（再帰的な構造など）
    a = []
    a.append(a)
    args = {"circular": a}
    
    key = tracker._make_key("circular_tool", args)
    assert key == f"circular_tool:{str(args)}"
