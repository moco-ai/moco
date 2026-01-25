import os
import sys
import logging
import importlib.util
import inspect
from typing import Dict, Callable, Optional, List, Any
import yaml
import glob
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# mocoパッケージのルートを取得（moco/tools/discovery.py から2階層上）
_MOCO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# プロジェクトルート（server_monitor_ai）
_PROJECT_ROOT = os.path.dirname(_MOCO_ROOT)

# Tools that should be available to all agents implicitly.
# NOTE:
# - AgentRuntime only enables tools listed in each agent config (`config.tools`).
# - These are safe, non-destructive meta-tools for skills discovery/usage.
_IMPLICIT_SKILL_TOOLS = [
    "search_skills",
    "load_skill",
    "list_loaded_skills",
    "execute_skill",
]


def _with_implicit_skill_tools(tools: List[str]) -> List[str]:
    """Add implicit skill tools while preserving order and avoiding duplicates."""
    # Normalize
    tools = [t for t in (tools or []) if isinstance(t, str) and t.strip()]
    seen = set()
    merged: List[str] = []

    for t in tools:
        if t not in seen:
            merged.append(t)
            seen.add(t)

    for t in _IMPLICIT_SKILL_TOOLS:
        if t not in seen:
            merged.append(t)
            seen.add(t)

    return merged


@dataclass
class AgentConfig:
    name: str
    description: str
    system_prompt: str
    tools: List[str]
    mode: str = "chat"

# --- Helper Functions ---

def _find_profiles_dir() -> str:
    """プロファイルディレクトリを探す（環境変数 > cwd > パッケージ内）"""
    # 1. 環境変数
    env_dir = os.getenv("MOCO_PROFILES_DIR")
    if env_dir and os.path.exists(env_dir):
        return env_dir
    # 2. カレントディレクトリ
    cwd_dir = os.path.join(os.getcwd(), "profiles")
    if os.path.exists(cwd_dir):
        return cwd_dir
    # 3. パッケージ内（フォールバック）
    return os.path.join(_MOCO_ROOT, "profiles")


# --- Tool Discovery ---

def discover_tools(profile: str, additional_mcp: Optional[List[Any]] = None) -> Dict[str, Callable]:
    """
    指定されたプロファイルの tools/ ディレクトリと、
    設定に応じてベースツールを読み込む
    """
    tool_map = {}
    
    # プロファイル設定を読み込む（環境変数 > cwd > パッケージ内）
    profiles_dir = _find_profiles_dir()
    profile_dir = os.path.join(profiles_dir, profile)
    profile_path = os.path.join(profile_dir, "profile.yaml")
    profile_config = {}
    if os.path.exists(profile_path):
        with open(profile_path, 'r') as f:
            profile_config = yaml.safe_load(f)

    # 1. プロファイル固有のツールを読み込む
    profile_tools_dir = os.path.join(profile_dir, "tools")
    if os.path.isdir(profile_tools_dir):
        tool_map.update(_load_tools_from_dir(profile_tools_dir))

    # 2. ベースツールを含めるかチェック（デフォルトtrue）
    if profile_config.get('include_base_tools', True):
        base_tools_dir = os.path.join(_MOCO_ROOT, "tools")
        tool_map.update(_load_tools_from_dir(base_tools_dir))
        
        # todo.py はグローバル状態を持つため、静的インポートしたものを使用
        from .todo import todowrite, todoread, todoread_all
        tool_map["todowrite"] = todowrite
        tool_map["todoread"] = todoread
        tool_map["todoread_all"] = todoread_all
        
        # skill_tools も静的インポート（グローバルキャッシュを持つ）
        from .skill_tools import search_skills, load_skill, list_loaded_skills, execute_skill
        tool_map["search_skills"] = search_skills
        tool_map["load_skill"] = load_skill
        tool_map["list_loaded_skills"] = list_loaded_skills
        tool_map["execute_skill"] = execute_skill
        
        # project_context
        from .project_context import get_project_context
        tool_map["get_project_context"] = get_project_context
        
    # 3. MCP ツールを読み込む
    mcp_servers_config = profile_config.get("mcp_servers", []) if isinstance(profile_config, dict) else []
    
    env_mcp = os.getenv("MOCO_MCP_SERVERS")
    if env_mcp:
        try:
            import json
            mcp_servers_config.extend(json.loads(env_mcp))
        except Exception as e:
            logger.error(f"Error parsing MOCO_MCP_SERVERS env: {e}")

    # 集約した設定から MCP サーバーを構築
    servers = []
    from ..core.mcp_client import MCPServerConfig
    for s in mcp_servers_config:
        if isinstance(s, dict) and s.get("name") and s.get("command"):
            servers.append(
                MCPServerConfig(
                    name=str(s["name"]),
                    command=str(s["command"]),
                    args=list(s.get("args", []) or []),
                    env=dict(s.get("env", {}) or {}),
                )
            )
    
    if additional_mcp:
        servers.extend(additional_mcp)

    if servers:
        try:
            from ..core.mcp_client import get_mcp_client, MCPConfig
            mcp_config = MCPConfig(enabled=True, servers=servers)
            mcp_client = get_mcp_client(mcp_config)
            mcp_tools = mcp_client.create_tool_functions()
            tool_map.update(mcp_tools)
            logger.info(f"Loaded {len(mcp_tools)} MCP tools from {len(servers)} servers")
        except Exception as e:
            logger.error(f"Error loading MCP tools: {e}")

    # 4. Skills のロジック型ツールを読み込む
    from .skill_loader import SkillLoader
    from .skill_tools import execute_skill
    
    loader = SkillLoader(profile=profile)
    skills = loader.load_skills()
    
    def _wrap_declared_skill_tool(skill_name: str, tool_name: str, description: str = ""):
        def _tool(**kwargs):
            return execute_skill(skill_name=skill_name, tool_name=tool_name, arguments=kwargs)
        _tool.__name__ = tool_name
        _tool.__doc__ = description or f"Skill tool: {skill_name}.{tool_name}"
        return _tool

    for skill in skills.values():
        if skill.is_logic and skill.exposed_tools:
            for tool_name, tool_def in skill.exposed_tools.items():
                desc = tool_def.get("description", "")
                tool_map[tool_name] = _wrap_declared_skill_tool(skill.name, tool_name, desc)

    return tool_map

def _load_tools_from_dir(tools_dir: str) -> Dict[str, Callable]:
    """
    指定されたディレクトリからツールを読み込むヘルパー関数
    相対インポートが動作するよう、パッケージとして読み込む
    """
    tool_map = {}
    if not os.path.isdir(tools_dir):
        return tool_map
    
    # ディレクトリ名からユニークなプレフィックスを生成
    dir_hash = abs(hash(tools_dir)) % 10000
    
    # 動的ロードから除外するファイル（グローバル状態を持つもの、または静的インポートされるもの）
    exclude_files = {"discovery.py", "todo.py", "skill_tools.py", "skill_loader.py"}
    
    # 相対インポートを動作させるため、親ディレクトリを sys.path に追加
    parent_dir = os.path.dirname(tools_dir)
    package_name = os.path.basename(tools_dir)
    
    # sys.path に追加（重複チェック）
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    
    # __init__.py があればパッケージとして扱う
    init_file = os.path.join(tools_dir, "__init__.py")
    is_package = os.path.exists(init_file)
        
    for filename in os.listdir(tools_dir):
        if filename.endswith(".py") and not filename.startswith("__") and filename not in exclude_files:
            filepath = os.path.join(tools_dir, filename)
            module_basename = filename[:-3]
            
            if is_package:
                # パッケージとして読み込む（相対インポートが動作する）
                full_module_name = f"{package_name}.{module_basename}"
                try:
                    # 既にロード済みなら再利用
                    if full_module_name in sys.modules:
                        module = sys.modules[full_module_name]
                    else:
                        spec = importlib.util.spec_from_file_location(
                            full_module_name, 
                            filepath,
                            submodule_search_locations=[tools_dir]
                        )
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            module.__package__ = package_name
                            sys.modules[full_module_name] = module
                            spec.loader.exec_module(module)
                    
                    for name, func in inspect.getmembers(module, inspect.isfunction):
                        if not name.startswith("_"):
                            tool_map[name] = func
                except Exception as e:
                    logger.warning(f"Error loading module from {filepath}: {e}")
            else:
                # 従来の方法（相対インポートなし）
                module_name = f"_discovered_tools_{dir_hash}_{module_basename}"
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    try:
                        spec.loader.exec_module(module)
                        for name, func in inspect.getmembers(module, inspect.isfunction):
                            if not name.startswith("_"):
                                tool_map[name] = func
                    except Exception as e:
                        logger.warning(f"Error loading module from {filepath}: {e}")
    return tool_map


# --- Agent Discovery ---

class AgentLoader:
    def __init__(self, profile: str = "default"):
        self.profile = profile
        self._refresh_agents_dir()

    def _refresh_agents_dir(self) -> None:
        """Recalculate agents directory for the current profile.

        Note: profile can be changed at runtime (e.g. via CLI /profile),
        so this must not be treated as immutable state.
        """
        profiles_dir = _find_profiles_dir()
        self.agents_dir = os.path.join(profiles_dir, self.profile, "agents")

    def load_agents(self) -> Dict[str, AgentConfig]:
        # Recalculate in case self.profile was changed after __init__
        self._refresh_agents_dir()
        agents = {}
        # Support both .md and .yaml files
        for ext in ["*.md", "*.yaml", "*.yml"]:
            search_path = os.path.join(self.agents_dir, ext)
            files = glob.glob(search_path)
            
            for file_path in files:
                try:
                    if file_path.endswith(".md"):
                        agent = self._parse_agent_file(file_path)
                    else:
                        agent = self._parse_yaml_agent(file_path)
                        
                    if agent:
                        agents[agent.name] = agent
                except Exception as e:
                    logger.warning(f"Failed to load agent from {file_path}: {e}")
        
        return agents

    def _parse_yaml_agent(self, file_path: str) -> Optional[AgentConfig]:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data:
            return None

        # Extract name from file or data
        agent_info = data.get("agent", {})
        name = agent_info.get("name") or os.path.splitext(os.path.basename(file_path))[0]
        
        # Construct system prompt from components
        prompt_parts = []
        if data.get("role"):
            prompt_parts.append(f"Role: {data['role']}")
        if data.get("goal"):
            prompt_parts.append(f"Goal: {data['goal']}")
        if data.get("backstory"):
            prompt_parts.append(f"Backstory:\n{data['backstory']}")
        if data.get("delegation_instructions"):
            prompt_parts.append(f"Delegation Instructions:\n{data['delegation_instructions']}")
        if data.get("decision_framework"):
            prompt_parts.append(f"Decision Framework:\n{data['decision_framework']}")
        if data.get("output_requirements"):
            prompt_parts.append(f"Output Requirements:\n{data['output_requirements']}")
        if data.get("process_role"):
            prompt_parts.append(f"Process Role:\n{data['process_role']}")
        if data.get("quality_standards"):
            qs = data.get("quality_standards", [])
            if isinstance(qs, list):
                prompt_parts.append("Quality Standards:\n- " + "\n- ".join(qs))
        
        system_prompt = "\n\n".join(prompt_parts)

        raw_tools = data.get("tools", [])
        if isinstance(raw_tools, dict):
            tools = [k for k, v in raw_tools.items() if v]
        elif isinstance(raw_tools, list):
            tools = raw_tools
        else:
            tools = []
        tools = _with_implicit_skill_tools(tools)
        
        return AgentConfig(
            name=name,
            description=data.get("description", f"Strategy agent: {name}"),
            system_prompt=system_prompt,
            tools=tools,
            mode=agent_info.get("type") or "chat"
        )

    def _parse_agent_file(self, file_path: str) -> Optional[AgentConfig]:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        parts = content.split('---', 2)
        if len(parts) < 3:
            return None

        frontmatter_yaml = parts[1]
        body = parts[2].strip()

        try:
            metadata = yaml.safe_load(frontmatter_yaml)
        except yaml.YAMLError:
            return None

        agent_name = os.path.splitext(os.path.basename(file_path))[0]
        
        raw_tools = metadata.get("tools", [])
        if isinstance(raw_tools, dict):
            tools = [k for k, v in raw_tools.items() if v]
        elif isinstance(raw_tools, list):
            tools = raw_tools
        else:
            tools = []
        tools = _with_implicit_skill_tools(tools)
        
        return AgentConfig(
            name=agent_name,
            description=metadata.get("description", ""),
            system_prompt=body,
            tools=tools,
            mode=metadata.get("mode", "chat")
        )
