#!/usr/bin/env python3
"""
OpenRouter + moonshotai/kimi-k2.5 のツールコールテスト（複雑なタスク版）
"""
import asyncio
import os
from openai import AsyncOpenAI

# OpenRouter設定
client = AsyncOpenAI(
    api_key=os.environ.get("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)

# より多くのツール定義
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "ファイルを読み込む",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "ファイルパス"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "ディレクトリ内容を一覧表示",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "ディレクトリパス"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "ファイル内をパターン検索",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "検索パターン"},
                    "path": {"type": "string", "description": "検索対象パス"}
                },
                "required": ["pattern", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": "ファイルのメタ情報を取得",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "ファイルパス"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "websearch",
            "description": "Web検索を実行",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ"}
                },
                "required": ["query"]
            }
        }
    }
]

async def test_complex_parallel(model: str):
    print("=" * 70)
    print(f"Test 1: 複雑なタスク（複数ディレクトリ + ファイル読み込み）")
    print(f"Model: {model}")
    print("=" * 70)
    
    messages = [
        {"role": "system", "content": """あなたはコードベース分析のエキスパートです。
効率的に作業するため、独立したタスクは並列でツールを呼び出してください。"""},
        {"role": "user", "content": """このプロジェクトを分析して：
1. src, tests, docs フォルダの構造を確認
2. README.md と pyproject.toml を読む
3. "def " と "class " がどこで使われているか検索"""}
    ]
    
    print(f"\n[User Request]\n{messages[-1]['content']}\n")
    
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        parallel_tool_calls=True,
        temperature=0.7
    )
    
    message = response.choices[0].message
    
    print(f"[Response]")
    print(f"Content: {message.content or '(none)'}")
    print(f"Tool calls: {len(message.tool_calls) if message.tool_calls else 0}")
    
    if message.tool_calls:
        for i, tc in enumerate(message.tool_calls):
            print(f"  [{i+1}] {tc.function.name}: {tc.function.arguments}")
    
    return len(message.tool_calls) if message.tool_calls else 0


async def test_multi_search(model: str):
    print("\n" + "=" * 70)
    print(f"Test 2: 複数の検索タスク")
    print(f"Model: {model}")
    print("=" * 70)
    
    messages = [
        {"role": "system", "content": """あなたはリサーチアシスタントです。
複数の独立した検索は同時に実行してください。"""},
        {"role": "user", "content": """以下について調べて：
1. Python asyncio のベストプラクティス
2. OpenAI API の最新情報
3. FastAPI のパフォーマンスチューニング
4. pytest のフィクスチャの使い方"""}
    ]
    
    print(f"\n[User Request]\n{messages[-1]['content']}\n")
    
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        parallel_tool_calls=True,
        temperature=0.7
    )
    
    message = response.choices[0].message
    
    print(f"[Response]")
    print(f"Content: {message.content or '(none)'}")
    print(f"Tool calls: {len(message.tool_calls) if message.tool_calls else 0}")
    
    if message.tool_calls:
        for i, tc in enumerate(message.tool_calls):
            print(f"  [{i+1}] {tc.function.name}: {tc.function.arguments}")
    
    return len(message.tool_calls) if message.tool_calls else 0


async def test_file_analysis(model: str):
    print("\n" + "=" * 70)
    print(f"Test 3: 複数ファイルの同時分析")
    print(f"Model: {model}")
    print("=" * 70)
    
    messages = [
        {"role": "system", "content": """あなたはコードレビュアーです。
複数ファイルを同時に読んで効率的にレビューしてください。"""},
        {"role": "user", "content": """以下のファイルを読んで比較分析して：
- src/main.py
- src/utils.py  
- src/config.py
- tests/test_main.py
- tests/test_utils.py"""}
    ]
    
    print(f"\n[User Request]\n{messages[-1]['content']}\n")
    
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        parallel_tool_calls=True,
        temperature=0.7
    )
    
    message = response.choices[0].message
    
    print(f"[Response]")
    print(f"Content: {message.content or '(none)'}")
    print(f"Tool calls: {len(message.tool_calls) if message.tool_calls else 0}")
    
    if message.tool_calls:
        for i, tc in enumerate(message.tool_calls):
            print(f"  [{i+1}] {tc.function.name}: {tc.function.arguments}")
    
    return len(message.tool_calls) if message.tool_calls else 0


async def test_tool_result_handling(model: str):
    """ツール結果を返した後のモデルの挙動をテスト"""
    print("\n" + "=" * 70)
    print(f"Test: Tool Result Handling (Full Flow)")
    print(f"Model: {model}")
    print("=" * 70)
    
    messages = [
        {"role": "system", "content": "あなたはアシスタントです。"},
        {"role": "user", "content": "srcフォルダの内容を見せて"}
    ]
    
    print(f"\n[Step 1] Initial request")
    
    # Step 1: Get tool call
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        parallel_tool_calls=True,
        temperature=0.7
    )
    
    message = response.choices[0].message
    print(f"  Tool calls: {len(message.tool_calls) if message.tool_calls else 0}")
    
    # Debug: check all attributes of message
    print(f"  [DEBUG] Message attributes: {[a for a in dir(message) if not a.startswith('_')]}")
    if hasattr(message, 'reasoning_content'):
        print(f"  [DEBUG] reasoning_content: {message.reasoning_content[:100] if message.reasoning_content else 'None'}...")
    if hasattr(message, 'reasoning'):
        print(f"  [DEBUG] reasoning: {message.reasoning[:100] if message.reasoning else 'None'}...")
    
    if not message.tool_calls:
        print("  No tool calls, model responded directly")
        print(f"  Content: {message.content}")
        return
    
    # Add assistant message with tool calls (including reasoning_content if present)
    assistant_msg = {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            }
            for tc in message.tool_calls
        ]
    }
    # Include reasoning_content for models like kimi-k2.5
    # Note: OpenRouter returns 'reasoning' but expects 'reasoning_content' in the request
    if hasattr(message, 'reasoning') and message.reasoning:
        assistant_msg["reasoning_content"] = message.reasoning
        print(f"  [Has reasoning: {len(message.reasoning)} chars]")
    elif hasattr(message, 'reasoning_content') and message.reasoning_content:
        assistant_msg["reasoning_content"] = message.reasoning_content
        print(f"  [Has reasoning_content: {len(message.reasoning_content)} chars]")
    messages.append(assistant_msg)
    
    # Add tool results
    for tc in message.tool_calls:
        print(f"  [{tc.function.name}] Simulating tool result...")
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": "main.py\nutils.py\nconfig.py\n__init__.py"
        })
    
    print(f"\n[Step 2] Sending tool results back to model...")
    
    # Step 2: Send tool results and get final response
    try:
        response2 = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            parallel_tool_calls=True,
            temperature=0.7
        )
        
        message2 = response2.choices[0].message
        print(f"  Content: {message2.content[:200] if message2.content else '(none)'}...")
        print(f"  More tool calls: {len(message2.tool_calls) if message2.tool_calls else 0}")
        print("\n✅ Model handled tool results correctly!")
        
    except Exception as e:
        print(f"\n❌ Error after sending tool results: {e}")


async def test_streaming_tool_result(model: str):
    """ストリーミングでのツール結果処理をテスト"""
    print("\n" + "=" * 70)
    print(f"Test: Streaming After Tool Results")
    print(f"Model: {model}")
    print("=" * 70)
    
    messages = [
        {"role": "system", "content": "あなたはアシスタントです。"},
        {"role": "user", "content": "今日のニュースを教えて"}
    ]
    
    # Step 1: Initial streaming request
    print(f"\n[Step 1] Streaming initial request...")
    
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        parallel_tool_calls=True,
        temperature=0.7,
        stream=True
    )
    
    collected_content = ""
    collected_reasoning = ""
    tool_calls_dict = {}
    
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if not delta:
            continue
        
        if delta.content:
            collected_content += delta.content
        
        # Collect reasoning from streaming chunks
        if hasattr(delta, 'reasoning') and delta.reasoning:
            collected_reasoning += delta.reasoning
        
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_calls_dict:
                    tool_calls_dict[idx] = {"id": "", "name": "", "arguments": ""}
                if tc.id:
                    tool_calls_dict[idx]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tool_calls_dict[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_calls_dict[idx]["arguments"] += tc.function.arguments
    
    print(f"  Content: {collected_content[:100] if collected_content else '(none)'}")
    print(f"  Reasoning: {len(collected_reasoning)} chars")
    print(f"  Tool calls: {len(tool_calls_dict)}")
    
    if not tool_calls_dict:
        print("  No tool calls in streaming")
        return
    
    for idx, tc in tool_calls_dict.items():
        print(f"    [{idx}] {tc['name']}: {tc['arguments'][:50]}...")
    
    # Build messages with tool results (including reasoning_content for kimi-k2.5)
    assistant_msg = {
        "role": "assistant",
        "content": collected_content,
        "tool_calls": [
            {
                "id": tc["id"] or f"call_{idx}",
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]}
            }
            for idx, tc in tool_calls_dict.items()
        ]
    }
    if collected_reasoning:
        assistant_msg["reasoning_content"] = collected_reasoning
    messages.append(assistant_msg)
    
    for idx, tc in tool_calls_dict.items():
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"] or f"call_{idx}",
            "content": "Mock search result: Today's news includes technology updates and sports news."
        })
    
    print(f"\n[Step 2] Streaming after tool results...")
    
    try:
        response2 = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            parallel_tool_calls=True,
            temperature=0.7,
            stream=True
        )
        
        final_content = ""
        async for chunk in response2:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                final_content += delta.content
                print(delta.content, end="", flush=True)
        
        print(f"\n\n✅ Streaming completed! ({len(final_content)} chars)")
        
    except Exception as e:
        print(f"\n❌ Streaming error: {e}")


if __name__ == "__main__":
    model = os.environ.get("TEST_MODEL", "moonshotai/kimi-k2.5")
    
    print("\n" + "🔧" * 35)
    print(f"  OpenRouter Tool Result Handling Test")
    print(f"  Model: {model}")
    print("🔧" * 35)
    
    # ツール結果処理のテスト
    asyncio.run(test_tool_result_handling(model))
    
    # ストリーミングでのツール結果処理テスト
    asyncio.run(test_streaming_tool_result(model))
