"""
MCP (Model Context Protocol) クライアント

外部 MCP サーバーと通信し、ツールを利用可能にする。
MCP SDK がインストールされていない場合は NoOp として動作する。
"""

import os
import asyncio
import logging
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)

# 許可されたコマンドのホワイトリスト
ALLOWED_COMMANDS = frozenset({"npx", "node", "python", "python3", "uvx", "deno"})

# MCP SDK のインポート（オプション依存）
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None


@dataclass
class MCPServerConfig:
    """
    MCP サーバー設定
    
    Attributes:
        name: サーバー識別名（ツール名のプレフィックスに使用）
        command: 起動コマンド（例: "npx", "python"）
        args: コマンド引数
        env: 環境変数（既存の環境変数にマージされる）
    """
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    
    def __post_init__(self):
        """防御的コピー"""
        self.args = list(self.args)
        self.env = dict(self.env)


@dataclass
class MCPConfig:
    """
    MCP クライアント設定
    
    Attributes:
        enabled: MCP 機能の有効/無効
        servers: MCP サーバー設定のリスト
    """
    enabled: bool = False
    servers: List[MCPServerConfig] = field(default_factory=list)
    
    def __post_init__(self):
        """防御的コピー"""
        self.servers = list(self.servers)


class MCPServerConnection:
    """
    単一の MCP サーバーへの接続を管理
    """
    
    def __init__(self, config: MCPServerConfig):
        """
        Args:
            config: サーバー設定
        """
        self.config = config
        self.session: Optional['ClientSession'] = None
        self.tools: List[Dict[str, Any]] = []
        self._context_manager = None
        self._connected = False
    
    async def connect(self) -> None:
        """
        MCP サーバーに接続
        
        Raises:
            RuntimeError: MCP SDK が利用できない場合
            Exception: 接続に失敗した場合
        """
        if not MCP_AVAILABLE:
            raise RuntimeError("MCP SDK is not installed. Run: pip install mcp")
        
        if self._connected:
            logger.debug(f"Already connected to MCP server: {self.config.name}")
            return

        # コマンドを検証（コマンドインジェクション対策）
        self._validate_command(self.config.command)

        # 環境変数をマージ
        env = os.environ.copy()
        env.update(self.config.env)
        
        # サーバーパラメータを作成
        server_params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=env
        )
        
        try:
            # stdio クライアントを開始
            self._context_manager = stdio_client(server_params)
            read, write = await self._context_manager.__aenter__()
            
            # セッションを作成
            self.session = ClientSession(read, write)
            await self.session.__aenter__()
            
            # 初期化
            await self.session.initialize()
            
            # ツール一覧を取得
            tools_response = await self.session.list_tools()
            self.tools = [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema if hasattr(tool, 'inputSchema') else {}
                }
                for tool in tools_response.tools
            ]
            
            self._connected = True
            logger.info(
                f"Connected to MCP server '{self.config.name}' "
                f"with {len(self.tools)} tools"
            )
            
        except Exception as e:
            logger.error(f"Failed to connect to MCP server '{self.config.name}': {e}")
            await self.disconnect()
            raise
    
    async def disconnect(self) -> None:
        """
        MCP サーバーから切断

        リソースは内側から外側の順で閉じる:
        1. session (内側)
        2. context_manager (外側: stdio transport)
        """
        # 内側から閉じる: session → context_manager
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing session for '{self.config.name}': {e}")
            finally:
                self.session = None

        if self._context_manager:
            try:
                await self._context_manager.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing context for '{self.config.name}': {e}")
            finally:
                self._context_manager = None

        self._connected = False
        self.tools = []
        logger.debug(f"Disconnected from MCP server: {self.config.name}")
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        ツールを呼び出す
        
        Args:
            tool_name: ツール名（プレフィックスなし）
            arguments: ツール引数
            
        Returns:
            ツールの実行結果
            
        Raises:
            RuntimeError: 接続されていない場合
            Exception: ツール呼び出しに失敗した場合
        """
        if not self._connected or not self.session:
            raise RuntimeError(f"Not connected to MCP server: {self.config.name}")
        
        try:
            result = await self.session.call_tool(tool_name, arguments)
            
            # 結果を抽出
            if hasattr(result, 'content') and result.content:
                # content は TextContent のリストの場合がある
                contents = []
                for content in result.content:
                    if hasattr(content, 'text'):
                        contents.append(content.text)
                    else:
                        contents.append(str(content))
                return "\n".join(contents) if contents else str(result)
            
            return str(result)
            
        except Exception as e:
            logger.error(
                f"Error calling tool '{tool_name}' on server '{self.config.name}': {e}"
            )
            raise
    
    @property
    def is_connected(self) -> bool:
        """接続状態を返す"""
        return self._connected

    def _validate_command(self, command: str) -> None:
        """
        コマンドを検証してコマンドインジェクションを防止

        Args:
            command: 検証するコマンド

        Raises:
            ValueError: 許可されていないコマンドの場合
        """
        base_command = os.path.basename(command)
        if base_command not in ALLOWED_COMMANDS:
            raise ValueError(
                f"Command '{command}' is not allowed. "
                f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
            )


class MCPClient:
    """
    MCP クライアント
    
    複数の MCP サーバーを管理し、ツールを統合的に利用可能にする。
    """
    
    def __init__(self, config: Optional[MCPConfig] = None):
        """
        Args:
            config: MCP 設定（省略時はデフォルト設定）
        """
        self.config = config or MCPConfig()
        self._connections: Dict[str, MCPServerConnection] = {}
        self._tool_map: Dict[str, str] = {}  # prefixed_name -> server_name
    
    @property
    def is_available(self) -> bool:
        """MCP SDK が利用可能かどうか"""
        return MCP_AVAILABLE
    
    @property
    def is_enabled(self) -> bool:
        """MCP 機能が有効かどうか"""
        return self.config.enabled and MCP_AVAILABLE
    
    async def connect(self) -> None:
        """
        すべての MCP サーバーに接続
        
        設定された各サーバーに順次接続を試みる。
        一部のサーバーへの接続が失敗しても、他のサーバーへの接続は継続する。
        """
        if not self.is_enabled:
            logger.debug("MCP is disabled or not available")
            return
        
        for server_config in self.config.servers:
            try:
                connection = MCPServerConnection(server_config)
                await connection.connect()
                self._connections[server_config.name] = connection
                
                # ツールマッピングを更新
                for tool in connection.tools:
                    prefixed_name = f"{server_config.name}_{tool['name']}"
                    self._tool_map[prefixed_name] = server_config.name
                    
            except Exception as e:
                logger.error(
                    f"Failed to connect to MCP server '{server_config.name}': {e}"
                )
                # 接続失敗しても他のサーバーは継続
                continue
        
        connected_count = len(self._connections)
        total_count = len(self.config.servers)
        logger.info(f"Connected to {connected_count}/{total_count} MCP servers")
    
    async def disconnect(self) -> None:
        """
        すべての MCP サーバーから切断
        """
        for name, connection in list(self._connections.items()):
            try:
                await connection.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting from '{name}': {e}")
        
        self._connections.clear()
        self._tool_map.clear()
        logger.debug("Disconnected from all MCP servers")
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """
        利用可能なツール一覧を取得
        
        Returns:
            ツール情報のリスト（プレフィックス付きの名前を含む）
        """
        tools = []
        for server_name, connection in self._connections.items():
            for tool in connection.tools:
                tools.append({
                    "server": server_name,
                    "name": tool["name"],
                    "prefixed_name": f"{server_name}_{tool['name']}",
                    "description": tool.get("description", ""),
                    "inputSchema": tool.get("inputSchema", {})
                })
        return tools
    
    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Any:
        """
        MCP サーバーのツールを呼び出す
        
        Args:
            server_name: サーバー識別名
            tool_name: ツール名（プレフィックスなし）
            arguments: ツール引数
            
        Returns:
            ツールの実行結果
            
        Raises:
            ValueError: サーバーが見つからない場合
            RuntimeError: サーバーに接続されていない場合
        """
        if server_name not in self._connections:
            raise ValueError(f"MCP server not found: {server_name}")
        
        connection = self._connections[server_name]
        return await connection.call_tool(tool_name, arguments)
    
    async def call_tool_by_prefixed_name(
        self,
        prefixed_name: str,
        arguments: Dict[str, Any]
    ) -> Any:
        """
        プレフィックス付きのツール名でツールを呼び出す
        
        Args:
            prefixed_name: プレフィックス付きツール名（例: "filesystem_read_file"）
            arguments: ツール引数
            
        Returns:
            ツールの実行結果
            
        Raises:
            ValueError: ツールが見つからない場合
        """
        if prefixed_name not in self._tool_map:
            raise ValueError(f"MCP tool not found: {prefixed_name}")
        
        server_name = self._tool_map[prefixed_name]
        # プレフィックスを除去してツール名を取得
        tool_name = prefixed_name[len(server_name) + 1:]
        
        return await self.call_tool(server_name, tool_name, arguments)
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        LLM 用のツール定義を取得（OpenAI/Gemini 形式）
        
        Returns:
            OpenAI function calling 形式のツール定義リスト
        """
        definitions = []
        
        for server_name, connection in self._connections.items():
            for tool in connection.tools:
                prefixed_name = f"{server_name}_{tool['name']}"
                
                # OpenAI function calling 形式に変換
                definition = {
                    "type": "function",
                    "function": {
                        "name": prefixed_name,
                        "description": f"[MCP:{server_name}] {tool.get('description', '')}",
                        "parameters": tool.get("inputSchema", {
                            "type": "object",
                            "properties": {},
                            "required": []
                        })
                    }
                }
                definitions.append(definition)
        
        return definitions
    
    def create_tool_functions(self) -> Dict[str, Callable]:
        """
        moco のツールマップに追加可能な関数を生成

        Returns:
            ツール名をキー、呼び出し関数を値とする辞書
        """
        tool_functions = {}

        for prefixed_name in self._tool_map:
            # クロージャで変数をキャプチャ
            def make_tool_func(name: str):
                def tool_func(**kwargs) -> str:
                    """MCP ツールを同期的に呼び出す"""
                    try:
                        # Python 3.10+ 対応: get_running_loop で実行中のループを検出
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None

                        if loop is not None:
                            # 既にイベントループが動いている場合は ThreadPoolExecutor を使用
                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                future = executor.submit(
                                    lambda: asyncio.run(
                                        self.call_tool_by_prefixed_name(name, kwargs)
                                    )
                                )
                                return future.result(timeout=60)
                        else:
                            # イベントループが動いていない場合は asyncio.run を使用
                            return asyncio.run(
                                self.call_tool_by_prefixed_name(name, kwargs)
                            )
                    except Exception as e:
                        return f"Error calling MCP tool '{name}': {e}"

                return tool_func

            tool_functions[prefixed_name] = make_tool_func(prefixed_name)

        return tool_functions
    
    def get_connected_servers(self) -> List[str]:
        """
        接続中のサーバー名一覧を取得
        
        Returns:
            サーバー名のリスト
        """
        return list(self._connections.keys())
    
    def get_server_tools(self, server_name: str) -> List[Dict[str, Any]]:
        """
        特定サーバーのツール一覧を取得
        
        Args:
            server_name: サーバー識別名
            
        Returns:
            ツール情報のリスト
        """
        if server_name not in self._connections:
            return []
        return self._connections[server_name].tools


# ========================================
# グローバルインスタンス管理
# ========================================

_global_mcp_client: Optional[MCPClient] = None


def get_mcp_client(config: Optional[MCPConfig] = None) -> MCPClient:
    """
    グローバル MCP クライアントを取得
    
    Args:
        config: 初回呼び出し時の設定（省略時はデフォルト）
        
    Returns:
        MCPClient インスタンス
    """
    global _global_mcp_client
    
    if _global_mcp_client is None:
        _global_mcp_client = MCPClient(config)
    
    return _global_mcp_client


def reset_mcp_client() -> None:
    """
    グローバル MCP クライアントをリセット
    
    テスト用途やクライアント再初期化時に使用。
    """
    global _global_mcp_client
    _global_mcp_client = None
