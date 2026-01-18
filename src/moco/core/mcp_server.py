"""
MCP (Model Context Protocol) サーバー

moco で定義したツールを MCP プロトコルで外部に公開する。
stdio モードと HTTP モードの両方をサポート。
"""

import asyncio
import inspect
import json
import logging
import os
import sys
import hashlib
import hmac
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union, get_type_hints, TYPE_CHECKING
from functools import wraps

logger = logging.getLogger(__name__)

# MCP SDK のインポート（オプション依存）
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Tool as MCPTool,
        TextContent,
        CallToolResult,
        ListToolsResult,
        Resource,
        ResourceTemplate,
        Prompt,
        PromptMessage,
        GetPromptResult,
    )
    MCP_SERVER_AVAILABLE = True
except ImportError:
    MCP_SERVER_AVAILABLE = False
    Server = None
    MCPTool = None

# HTTP サーバー用（aiohttp）
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None

if TYPE_CHECKING:
    from ..core.runtime import AgentRuntime


# ========================================
# データ構造
# ========================================

class TransportMode(Enum):
    """トランスポートモード"""
    STDIO = "stdio"
    HTTP = "http"


@dataclass
class ToolDefinition:
    """
    MCP サーバーに登録するツール定義
    """
    name: str
    description: str
    handler: Callable[..., Any]
    input_schema: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        """防御的コピーとスキーマ自動生成"""
        self.tags = list(self.tags)
        self.input_schema = dict(self.input_schema)
        
        # input_schema が空の場合、ハンドラから自動生成
        if not self.input_schema:
            self.input_schema = self._generate_schema_from_handler()

    def _generate_schema_from_handler(self) -> Dict[str, Any]:
        """ハンドラの型ヒントからJSONスキーマを生成"""
        schema = {
            "type": "object",
            "properties": {},
            "required": []
        }
        
        try:
            sig = inspect.signature(self.handler)
            hints = get_type_hints(self.handler) if hasattr(self.handler, '__annotations__') else {}
            
            for param_name, param in sig.parameters.items():
                if param_name in ('self', 'cls'):
                    continue
                
                # 型からJSONスキーマ型を推定
                param_type = hints.get(param_name, Any)
                json_type = self._python_type_to_json_type(param_type)
                
                prop_def = {"type": json_type}
                
                # docstring からパラメータ説明を抽出（簡易版）
                if self.handler.__doc__:
                    doc_lines = self.handler.__doc__.split('\n')
                    for line in doc_lines:
                        line = line.strip()
                        if line.startswith(f'{param_name}') or line.startswith(f'{param_name} '):
                            # "param_name: description" or "param_name (type): description"
                            if ':' in line:
                                desc = line.split(':', 1)[1].strip()
                                prop_def["description"] = desc
                            break
                
                schema["properties"][param_name] = prop_def
                
                # デフォルト値がない場合は必須
                if param.default is inspect.Parameter.empty:
                    schema["required"].append(param_name)
                    
        except Exception as e:
            logger.debug(f"Failed to generate schema for {self.name}: {e}")
        
        return schema

    @staticmethod
    def _python_type_to_json_type(python_type: type) -> str:
        """Python型をJSONスキーマ型に変換"""
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }
        
        # typing モジュールの型を処理
        origin = getattr(python_type, '__origin__', None)
        if origin is not None:
            if origin in (list, List):
                return "array"
            if origin in (dict, Dict):
                return "object"
            if origin is Union:
                # Optional は Union[X, None] なので最初の型を使用
                args = getattr(python_type, '__args__', ())
                for arg in args:
                    if arg is not type(None):
                        return ToolDefinition._python_type_to_json_type(arg)
        
        return type_map.get(python_type, "string")


@dataclass
class ResourceDefinition:
    """
    MCP リソース定義
    """
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"
    handler: Optional[Callable[[], str]] = None


@dataclass
class PromptDefinition:
    """
    MCP プロンプト定義
    """
    name: str
    description: str = ""
    arguments: List[Dict[str, Any]] = field(default_factory=list)
    template: str = ""


@dataclass
class AuthConfig:
    """
    認証設定
    """
    enabled: bool = False
    api_keys: List[str] = field(default_factory=list)
    hmac_secret: Optional[str] = None
    
    def __post_init__(self):
        """防御的コピー"""
        self.api_keys = list(self.api_keys)


# ========================================
# MCP サーバー実装
# ========================================

class MCPServer:
    """
    MCP サーバー実装
    
    moco のツールを MCP プロトコルで外部に公開する。
    stdio モードと HTTP モードの両方をサポート。
    
    Example:
        ```python
        server = MCPServer("my-tools")
        server.register_tool(ToolDefinition(
            name="greet",
            description="挨拶を返す",
            handler=lambda name: f"Hello, {name}!"
        ))
        await server.start_stdio()
        ```
    """
    
    def __init__(
        self,
        name: str = "moco-server",
        version: str = "1.0.0",
        auth_config: Optional[AuthConfig] = None
    ):
        """
        Args:
            name: サーバー名
            version: サーバーバージョン
            auth_config: 認証設定（オプション）
        """
        self.name = name
        self.version = version
        self.auth_config = auth_config or AuthConfig()
        
        self._tools: Dict[str, ToolDefinition] = {}
        self._resources: Dict[str, ResourceDefinition] = {}
        self._prompts: Dict[str, PromptDefinition] = {}
        
        self._mcp_server: Optional['Server'] = None
        self._http_app: Optional['web.Application'] = None
        self._running = False

    # ========================================
    # ツール管理
    # ========================================

    def register_tool(self, tool: ToolDefinition) -> None:
        """
        ツールを登録
        
        Args:
            tool: ツール定義
            
        Raises:
            ValueError: 同名のツールが既に登録されている場合
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def register_function(
        self,
        func: Callable[..., Any],
        name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> None:
        """
        Python関数をツールとして登録
        
        Args:
            func: 登録する関数
            name: ツール名（省略時は関数名）
            description: 説明（省略時はdocstring）
            tags: タグリスト
        """
        tool_name = name or func.__name__
        tool_description = description or (func.__doc__ or "").split('\n')[0].strip()
        
        tool = ToolDefinition(
            name=tool_name,
            description=tool_description,
            handler=func,
            tags=tags or []
        )
        self.register_tool(tool)

    def unregister_tool(self, name: str) -> bool:
        """
        ツールを登録解除
        
        Args:
            name: ツール名
            
        Returns:
            解除に成功した場合True
        """
        if name in self._tools:
            del self._tools[name]
            logger.debug(f"Unregistered tool: {name}")
            return True
        return False

    def register_agent_tools(self, agent: 'AgentRuntime') -> None:
        """
        Agent の全ツールを登録
        
        Args:
            agent: AgentRuntime インスタンス
        """
        if not hasattr(agent, 'tool_map') or not agent.tool_map:
            logger.warning(f"Agent has no tools to register")
            return
        
        for tool_name, tool_func in agent.tool_map.items():
            try:
                self.register_function(
                    func=tool_func,
                    name=tool_name,
                    tags=["agent"]
                )
            except ValueError as e:
                logger.warning(f"Skipping tool '{tool_name}': {e}")

    def register_tool_map(self, tool_map: Dict[str, Callable]) -> None:
        """
        ツールマップ（dict）から一括登録
        
        Args:
            tool_map: ツール名 -> 関数 のマッピング
        """
        for name, func in tool_map.items():
            try:
                self.register_function(func, name=name)
            except ValueError as e:
                logger.warning(f"Skipping tool '{name}': {e}")

    def list_tools(self) -> List[ToolDefinition]:
        """登録されているツール一覧を取得"""
        return list(self._tools.values())

    # ========================================
    # リソース管理
    # ========================================

    def register_resource(self, resource: ResourceDefinition) -> None:
        """
        リソースを登録
        
        Args:
            resource: リソース定義
        """
        self._resources[resource.uri] = resource
        logger.debug(f"Registered resource: {resource.uri}")

    def list_resources(self) -> List[ResourceDefinition]:
        """登録されているリソース一覧を取得"""
        return list(self._resources.values())

    # ========================================
    # プロンプト管理
    # ========================================

    def register_prompt(self, prompt: PromptDefinition) -> None:
        """
        プロンプトを登録
        
        Args:
            prompt: プロンプト定義
        """
        self._prompts[prompt.name] = prompt
        logger.debug(f"Registered prompt: {prompt.name}")

    def list_prompts(self) -> List[PromptDefinition]:
        """登録されているプロンプト一覧を取得"""
        return list(self._prompts.values())

    # ========================================
    # ツール実行
    # ========================================

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """
        ツールを呼び出す
        
        Args:
            name: ツール名
            arguments: 引数
            
        Returns:
            ツールの実行結果
            
        Raises:
            ValueError: ツールが見つからない場合
            Exception: ツール実行中のエラー
        """
        if name not in self._tools:
            raise ValueError(f"Tool not found: {name}")
        
        tool = self._tools[name]
        handler = tool.handler
        
        try:
            # 非同期関数かどうかを判定
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**arguments)
            else:
                # 同期関数はスレッドプールで実行
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: handler(**arguments)
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Error calling tool '{name}': {e}")
            raise

    # ========================================
    # stdio モード
    # ========================================

    async def start_stdio(self) -> None:
        """
        stdio モードで MCP サーバーを起動
        
        標準入出力を使用して MCP プロトコルで通信する。
        Claude Desktop や他の MCP クライアントとの連携に使用。
        
        Raises:
            RuntimeError: MCP SDK が利用できない場合
        """
        if not MCP_SERVER_AVAILABLE:
            raise RuntimeError(
                "MCP Server SDK is not installed. "
                "Run: pip install 'mcp[server]'"
            )
        
        self._mcp_server = Server(self.name)
        self._setup_mcp_handlers()
        
        logger.info(f"Starting MCP server '{self.name}' in stdio mode")
        self._running = True
        
        try:
            async with stdio_server() as (read_stream, write_stream):
                await self._mcp_server.run(
                    read_stream,
                    write_stream,
                    self._mcp_server.create_initialization_options()
                )
        finally:
            self._running = False

    def _setup_mcp_handlers(self) -> None:
        """MCP プロトコルハンドラを設定"""
        if not self._mcp_server:
            return
        
        server = self._mcp_server
        
        # tools/list ハンドラ
        @server.list_tools()
        async def handle_list_tools() -> list:
            tools = []
            for tool in self._tools.values():
                tools.append(MCPTool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.input_schema
                ))
            return tools
        
        # tools/call ハンドラ
        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict) -> list:
            try:
                result = await self.call_tool(name, arguments or {})
                
                # 結果を文字列に変換
                if isinstance(result, str):
                    text = result
                elif isinstance(result, (dict, list)):
                    text = json.dumps(result, ensure_ascii=False, indent=2)
                else:
                    text = str(result)
                
                return [TextContent(type="text", text=text)]
                
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"Error: {str(e)}"
                )]
        
        # resources/list ハンドラ
        @server.list_resources()
        async def handle_list_resources() -> list:
            resources = []
            for res in self._resources.values():
                resources.append(Resource(
                    uri=res.uri,
                    name=res.name,
                    description=res.description,
                    mimeType=res.mime_type
                ))
            return resources
        
        # resources/read ハンドラ
        @server.read_resource()
        async def handle_read_resource(uri: str) -> str:
            if uri not in self._resources:
                raise ValueError(f"Resource not found: {uri}")
            
            resource = self._resources[uri]
            if resource.handler:
                return resource.handler()
            return ""
        
        # prompts/list ハンドラ
        @server.list_prompts()
        async def handle_list_prompts() -> list:
            prompts = []
            for prompt in self._prompts.values():
                prompts.append(Prompt(
                    name=prompt.name,
                    description=prompt.description,
                    arguments=prompt.arguments
                ))
            return prompts
        
        # prompts/get ハンドラ
        @server.get_prompt()
        async def handle_get_prompt(name: str, arguments: dict = None) -> GetPromptResult:
            if name not in self._prompts:
                raise ValueError(f"Prompt not found: {name}")
            
            prompt = self._prompts[name]
            template = prompt.template
            
            # 引数を置換
            if arguments:
                for key, value in arguments.items():
                    template = template.replace(f"{{{key}}}", str(value))
            
            return GetPromptResult(
                description=prompt.description,
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=template)
                    )
                ]
            )

    # ========================================
    # HTTP モード
    # ========================================

    async def start_http(
        self,
        host: str = "localhost",
        port: int = 8080
    ) -> None:
        """
        HTTP モードで MCP サーバーを起動
        
        REST API として MCP プロトコルを公開する。
        
        Args:
            host: バインドするホスト
            port: バインドするポート
            
        Raises:
            RuntimeError: aiohttp が利用できない場合
        """
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError(
                "aiohttp is not installed. "
                "Run: pip install aiohttp"
            )
        
        self._http_app = web.Application(middlewares=[self._auth_middleware])
        self._setup_http_routes()
        
        logger.info(f"Starting MCP server '{self.name}' on http://{host}:{port}")
        self._running = True
        
        try:
            runner = web.AppRunner(self._http_app)
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()
            
            # サーバーが停止されるまで待機
            while self._running:
                await asyncio.sleep(1)
                
        finally:
            self._running = False
            await runner.cleanup()

    @web.middleware
    async def _auth_middleware(self, request: 'web.Request', handler):
        """認証ミドルウェア"""
        if not self.auth_config.enabled:
            return await handler(request)
        
        # API キー認証
        api_key = request.headers.get('X-API-Key')
        if api_key and api_key in self.auth_config.api_keys:
            return await handler(request)
        
        # HMAC 認証
        if self.auth_config.hmac_secret:
            signature = request.headers.get('X-Signature')
            timestamp = request.headers.get('X-Timestamp')
            
            if signature and timestamp:
                try:
                    # タイムスタンプの検証（5分以内）
                    ts = int(timestamp)
                    if abs(time.time() - ts) < 300:
                        # HMAC 検証
                        body = await request.read()
                        message = f"{timestamp}:{body.decode()}"
                        expected = hmac.new(
                            self.auth_config.hmac_secret.encode(),
                            message.encode(),
                            hashlib.sha256
                        ).hexdigest()
                        
                        if hmac.compare_digest(signature, expected):
                            return await handler(request)
                except (ValueError, TypeError):
                    pass
        
        return web.json_response(
            {"error": "Unauthorized"},
            status=401
        )

    def _setup_http_routes(self) -> None:
        """HTTP ルートを設定"""
        if not self._http_app:
            return
        
        app = self._http_app
        
        # ヘルスチェック
        async def health(request):
            return web.json_response({
                "status": "ok",
                "server": self.name,
                "version": self.version
            })
        
        # サーバー情報
        async def info(request):
            return web.json_response({
                "name": self.name,
                "version": self.version,
                "tools": len(self._tools),
                "resources": len(self._resources),
                "prompts": len(self._prompts)
            })
        
        # tools/list
        async def list_tools(request):
            tools = []
            for tool in self._tools.values():
                tools.append({
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                    "tags": tool.tags
                })
            return web.json_response({"tools": tools})
        
        # tools/call
        async def call_tool(request):
            try:
                data = await request.json()
                name = data.get("name")
                arguments = data.get("arguments", {})
                
                if not name:
                    return web.json_response(
                        {"error": "Missing 'name' field"},
                        status=400
                    )
                
                result = await self.call_tool(name, arguments)
                
                # 結果を適切な形式に変換
                if isinstance(result, str):
                    content = [{"type": "text", "text": result}]
                elif isinstance(result, (dict, list)):
                    content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                else:
                    content = [{"type": "text", "text": str(result)}]
                
                return web.json_response({
                    "content": content,
                    "isError": False
                })
                
            except ValueError as e:
                return web.json_response(
                    {"error": str(e), "isError": True},
                    status=404
                )
            except Exception as e:
                logger.exception(f"Error in call_tool: {e}")
                return web.json_response(
                    {"error": str(e), "isError": True},
                    status=500
                )
        
        # resources/list
        async def list_resources(request):
            resources = []
            for res in self._resources.values():
                resources.append({
                    "uri": res.uri,
                    "name": res.name,
                    "description": res.description,
                    "mimeType": res.mime_type
                })
            return web.json_response({"resources": resources})
        
        # resources/read
        async def read_resource(request):
            uri = request.query.get("uri")
            if not uri:
                return web.json_response(
                    {"error": "Missing 'uri' parameter"},
                    status=400
                )
            
            if uri not in self._resources:
                return web.json_response(
                    {"error": f"Resource not found: {uri}"},
                    status=404
                )
            
            resource = self._resources[uri]
            content = resource.handler() if resource.handler else ""
            
            return web.json_response({
                "contents": [{
                    "uri": uri,
                    "mimeType": resource.mime_type,
                    "text": content
                }]
            })
        
        # prompts/list
        async def list_prompts(request):
            prompts = []
            for prompt in self._prompts.values():
                prompts.append({
                    "name": prompt.name,
                    "description": prompt.description,
                    "arguments": prompt.arguments
                })
            return web.json_response({"prompts": prompts})
        
        # prompts/get
        async def get_prompt(request):
            try:
                data = await request.json()
                name = data.get("name")
                arguments = data.get("arguments", {})
                
                if not name:
                    return web.json_response(
                        {"error": "Missing 'name' field"},
                        status=400
                    )
                
                if name not in self._prompts:
                    return web.json_response(
                        {"error": f"Prompt not found: {name}"},
                        status=404
                    )
                
                prompt = self._prompts[name]
                template = prompt.template
                
                # 引数を置換
                for key, value in arguments.items():
                    template = template.replace(f"{{{key}}}", str(value))
                
                return web.json_response({
                    "description": prompt.description,
                    "messages": [{
                        "role": "user",
                        "content": {"type": "text", "text": template}
                    }]
                })
                
            except Exception as e:
                return web.json_response(
                    {"error": str(e)},
                    status=500
                )
        
        # ルート登録
        app.router.add_get("/health", health)
        app.router.add_get("/info", info)
        app.router.add_get("/tools/list", list_tools)
        app.router.add_post("/tools/call", call_tool)
        app.router.add_get("/resources/list", list_resources)
        app.router.add_get("/resources/read", read_resource)
        app.router.add_get("/prompts/list", list_prompts)
        app.router.add_post("/prompts/get", get_prompt)

    async def stop(self) -> None:
        """サーバーを停止"""
        self._running = False
        logger.info(f"Stopping MCP server '{self.name}'")

    @property
    def is_running(self) -> bool:
        """サーバーが実行中かどうか"""
        return self._running


# ========================================
# ヘルパー関数
# ========================================

def create_mcp_server_from_agent(
    agent: 'AgentRuntime',
    name: Optional[str] = None,
    auth_config: Optional[AuthConfig] = None
) -> MCPServer:
    """
    Agent から MCP サーバーを作成
    
    Args:
        agent: AgentRuntime インスタンス
        name: サーバー名（省略時は agent 名を使用）
        auth_config: 認証設定
        
    Returns:
        MCPServer インスタンス
        
    Example:
        ```python
        agent = AgentRuntime(config)
        server = create_mcp_server_from_agent(agent)
        await server.start_stdio()
        ```
    """
    server_name = name or getattr(agent, 'name', 'moco-agent')
    server = MCPServer(name=server_name, auth_config=auth_config)
    server.register_agent_tools(agent)
    return server


def create_mcp_server_from_tool_map(
    tool_map: Dict[str, Callable],
    name: str = "moco-tools",
    auth_config: Optional[AuthConfig] = None
) -> MCPServer:
    """
    ツールマップから MCP サーバーを作成
    
    Args:
        tool_map: ツール名 -> 関数 のマッピング
        name: サーバー名
        auth_config: 認証設定
        
    Returns:
        MCPServer インスタンス
        
    Example:
        ```python
        from moco.tools import TOOL_MAP
        server = create_mcp_server_from_tool_map(TOOL_MAP)
        await server.start_http("0.0.0.0", 8080)
        ```
    """
    server = MCPServer(name=name, auth_config=auth_config)
    server.register_tool_map(tool_map)
    return server


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None
):
    """
    関数を MCP ツールとしてマークするデコレータ
    
    Args:
        name: ツール名（省略時は関数名）
        description: 説明（省略時はdocstring）
        tags: タグリスト
        
    Example:
        ```python
        @tool(description="ファイルを読み込む")
        def read_file(path: str) -> str:
            ...
        
        server = MCPServer()
        server.register_function(read_file)
        ```
    """
    def decorator(func: Callable) -> Callable:
        func._mcp_tool_name = name or func.__name__
        func._mcp_tool_description = description or (func.__doc__ or "").split('\n')[0].strip()
        func._mcp_tool_tags = tags or []
        return func
    return decorator


# ========================================
# CLI エントリーポイント
# ========================================

async def run_stdio_server(tool_map: Optional[Dict[str, Callable]] = None) -> None:
    """
    stdio モードで MCP サーバーを実行（CLI用）
    
    Args:
        tool_map: ツールマップ（省略時は moco のデフォルトツール）
    """
    if tool_map is None:
        from ..tools import TOOL_MAP
        tool_map = TOOL_MAP
    
    server = create_mcp_server_from_tool_map(tool_map, name="moco")
    await server.start_stdio()


async def run_http_server(
    host: str = "localhost",
    port: int = 8080,
    tool_map: Optional[Dict[str, Callable]] = None,
    api_key: Optional[str] = None
) -> None:
    """
    HTTP モードで MCP サーバーを実行（CLI用）
    
    Args:
        host: バインドするホスト
        port: バインドするポート
        tool_map: ツールマップ（省略時は moco のデフォルトツール）
        api_key: API キー（認証を有効にする場合）
    """
    if tool_map is None:
        from ..tools import TOOL_MAP
        tool_map = TOOL_MAP
    
    auth_config = None
    if api_key:
        auth_config = AuthConfig(enabled=True, api_keys=[api_key])
    
    server = create_mcp_server_from_tool_map(
        tool_map,
        name="moco",
        auth_config=auth_config
    )
    await server.start_http(host, port)


def main():
    """CLI エントリーポイント"""
    import argparse
    
    parser = argparse.ArgumentParser(description="MCP Server for moco tools")
    parser.add_argument(
        "--mode",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode (default: stdio)"
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="HTTP host (default: localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP port (default: 8080)"
    )
    parser.add_argument(
        "--api-key",
        help="API key for authentication"
    )
    
    args = parser.parse_args()
    
    if args.mode == "stdio":
        asyncio.run(run_stdio_server())
    else:
        asyncio.run(run_http_server(
            host=args.host,
            port=args.port,
            api_key=args.api_key
        ))


if __name__ == "__main__":
    main()
