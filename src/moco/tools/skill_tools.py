"""Skill management tools for Orchestrator.

These tools allow the Orchestrator to dynamically search and load skills
from local and remote registries.
"""

import json
from typing import Optional
from .skill_loader import SkillLoader

# グローバルなスキルローダーとロード済みスキルのキャッシュ
_skill_loader: Optional[SkillLoader] = None
_loaded_skills: dict = {}  # {skill_name: SkillConfig}


def get_loaded_skills() -> dict:
    """Get all currently loaded skills (for use by Orchestrator)."""
    return _loaded_skills


def clear_session_skills():
    """Clear loaded skills at session start."""
    global _loaded_skills
    _loaded_skills = {}


def _get_loader() -> SkillLoader:
    """Get or create the global skill loader."""
    global _skill_loader
    import os
    profile = os.environ.get("MOCO_PROFILE", "development")

    # Recreate loader if profile changed.
    # NOTE: Web UI / Orchestrator can switch profiles per request/session.
    if _skill_loader is None or getattr(_skill_loader, "profile", None) != profile:
        _skill_loader = SkillLoader(profile=profile, use_semantic=True)
    return _skill_loader


def search_skills(query: str, include_remote: bool = True) -> str:
    """Search for skills matching the query.
    
    Searches both local installed skills and remote registries (anthropics/skills, etc.).
    Uses semantic search with automatic translation for non-English queries.
    
    Args:
        query: Search query (e.g., "PDF", "React", "spreadsheet", "PDFからテキスト抽出")
        include_remote: Whether to search remote registries (default: True)
    
    Returns:
        JSON string with list of matching skills and their descriptions
    
    Example:
        search_skills("PDF manipulation")
        search_skills("frontend React", include_remote=True)
        search_skills("スプレッドシート操作")  # Japanese also works
    """
    loader = _get_loader()
    results = []
    matched_names = set()
    
    # ローカルスキルをロード
    local_skills = loader.load_skills()
    
    # リモートスキルをセマンティック検索（翻訳機能付き）
    if include_remote:
        try:
            # セマンティック検索（自動翻訳付き）
            remote_results = loader._search_remote_semantic(query, "anthropics", top_k=5)
            for r in remote_results:
                name = r.get("name", "")
                if name and name not in matched_names:
                    matched_names.add(name)
                    # ローカルにある場合はローカルから
                    if name in local_skills:
                        results.append({
                            "name": name,
                            "description": local_skills[name].description[:200],
                            "source": "local",
                            "loaded": name in _loaded_skills
                        })
                    else:
                        results.append({
                            "name": name,
                            "description": r.get("description", "")[:200],
                            "source": "remote:anthropics",
                            "loaded": name in _loaded_skills
                        })
        except Exception:
            # セマンティック検索失敗時はキーワード検索にフォールバック
            pass
    
    # ローカルスキルをキーワード検索（セマンティック検索でヒットしなかったもの）
    for name, skill in local_skills.items():
        if name not in matched_names and skill.matches_input(query):
            matched_names.add(name)
            results.append({
                "name": name,
                "description": skill.description[:200],
                "source": "local",
                "loaded": name in _loaded_skills
            })
    
    if not results:
        return json.dumps({"message": f"No skills found for query: {query}", "skills": []})
    
    return json.dumps({
        "message": f"Found {len(results)} skills",
        "skills": results
    }, ensure_ascii=False, indent=2)


def load_skill(skill_name: str, source: str = "auto") -> str:
    """Load a skill to use its knowledge in the current task.
    
    Loads a skill from local storage or fetches from remote registry.
    Once loaded, the skill's knowledge and guidelines become available.
    
    Args:
        skill_name: Name of the skill to load (e.g., "pdf", "frontend-design")
        source: Where to load from:
            - "auto": Try local first, then remote (default)
            - "local": Only from local installed skills
            - "remote": Fetch from remote registry
    
    Returns:
        The skill's content (knowledge/guidelines) or error message
    
    Example:
        load_skill("pdf")
        load_skill("frontend-design", source="remote")
    """
    global _loaded_skills
    loader = _get_loader()
    
    # 既にロード済みならキャッシュから返す
    if skill_name in _loaded_skills:
        skill = _loaded_skills[skill_name]
        return f"[Skill: {skill.name} (cached)]\n\n{skill.content}"
    
    skill = None
    
    # ローカルから探す
    if source in ("auto", "local"):
        local_skills = loader.load_skills()
        if skill_name in local_skills:
            skill = local_skills[skill_name]
    
    # リモートから取得
    if skill is None and source in ("auto", "remote"):
        try:
            skill = loader.fetch_skill_on_demand(skill_name, "anthropics")
        except Exception:
            pass
    
    if skill is None:
        return f"Error: Skill '{skill_name}' not found. Use search_skills() to find available skills."
    
    # キャッシュに保存
    _loaded_skills[skill_name] = skill
    
    # スキルの内容を返す
    result = f"""[Skill: {skill.name} v{skill.version}]
Description: {skill.description}

{skill.content}"""
    
    if skill.allowed_tools:
        result += f"\n\nAllowed Tools: {', '.join(skill.allowed_tools)}"
    
    return result


def list_loaded_skills() -> str:
    """List all currently loaded skills.
    
    Returns:
        JSON string with list of loaded skill names and descriptions
    """
    if not _loaded_skills:
        return json.dumps({"message": "No skills currently loaded", "skills": []})
    
    skills = [
        {"name": name, "description": skill.description[:100]}
        for name, skill in _loaded_skills.items()
    ]
    
    return json.dumps({
        "message": f"{len(skills)} skills loaded",
        "skills": skills
    }, ensure_ascii=False, indent=2)


def clear_loaded_skills() -> str:
    """Clear all loaded skills from cache.
    
    Returns:
        Confirmation message
    """
    global _loaded_skills
    count = len(_loaded_skills)
    _loaded_skills = {}
    return f"Cleared {count} loaded skills from cache."


def execute_skill(skill_name: str, tool_name: str, arguments: dict) -> str:
    """Execute a declared logic-based skill tool (JS/TS/Python).

    IMPORTANT:
    - Only tools explicitly declared in the skill's SKILL.md frontmatter `tools:` section
      are executable.
    
    Args:
        skill_name: Name of the skill
        tool_name: Function/Method name to call within the skill
        arguments: Arguments for the tool
        
    Returns:
        JSON result string
    """
    import subprocess
    import os
    import json
    
    loader = _get_loader()
    local_skills = loader.load_skills()
    
    if skill_name not in local_skills:
        return f"Error: Skill '{skill_name}' not found locally."
    
    skill = local_skills[skill_name]
    if not skill.is_logic:
        return f"Error: Skill '{skill_name}' does not have executable logic."

    declared_tools = skill.exposed_tools or {}
    if tool_name not in declared_tools:
        return (
            f"Error: Tool '{tool_name}' is not declared in SKILL.md tools: for skill '{skill_name}'. "
            f"Declare it under frontmatter `tools:` to make it executable."
        )
    
    skill_dir = skill.path
    
    # JavaScript/TypeScript (index.js / index.ts)
    js_path = os.path.join(skill_dir, "index.js")
    ts_path = os.path.join(skill_dir, "index.ts")
    
    if os.path.exists(js_path) or os.path.exists(ts_path):
        # Execute only the declared tool via JS bridge.
        # Node will resolve the skill directory to index.js/index.ts.
        from .js_bridge import execute_js_skill
        try:
            res = execute_js_skill(skill_dir, tool_name, arguments or {})
            return json.dumps(res, ensure_ascii=False)
        except Exception as e:
            return f"Error executing JS skill tool '{skill_name}.{tool_name}': {e}"

    # Python スクリプトとしての実行
    # 1. 直接的なツール名.py を探す（宣言された tool_name のみ）
    py_script = os.path.join(skill_dir, f"{tool_name}.py")
    # 2. scripts/ツール名.py を探す
    if not os.path.exists(py_script):
        py_script = os.path.join(skill_dir, "scripts", f"{tool_name}.py")
        
    if os.path.exists(py_script):
        try:
            # 1. まず通常のコマンドライン引数として展開して渡す
            args_list = ["python3", py_script]
            
            # target などの位置引数を特別扱いするか、一律に展開
            for k, v in arguments.items():
                if k in ("target", "input_file", "source", "path"): # positional arguments candidate
                    args_list.append(str(v))
                elif isinstance(v, bool):
                    if v:
                        args_list.append(f"--{k}")
                else:
                    args_list.extend([f"--{k}", str(v)])
            
            result = subprocess.run(
                args_list,
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode == 0:
                out = (result.stdout or "").strip()
                # If the output is JSON, return as-is to preserve structure.
                try:
                    json.loads(out)
                    return out
                except Exception:
                    return out
            
            # 2. 失敗した場合は、JSON文字列を単一引数として渡す（旧方式）
            result_legacy = subprocess.run(
                ["python3", py_script, json.dumps(arguments)],
                capture_output=True,
                text=True,
                check=False
            )
            if result_legacy.returncode == 0:
                out = (result_legacy.stdout or "").strip()
                try:
                    json.loads(out)
                    return out
                except Exception:
                    return out
                
            return f"Error executing Python skill:\nSTDERR: {result.stderr}\nSTDOUT: {result.stdout}"
        except Exception as e:
            return f"Exception in execute_skill (Python): {str(e)}"
            
    return (
        f"Error: Declared tool '{tool_name}' for skill '{skill_name}' has no executable entry point. "
        f"Expected one of: {tool_name}.py, scripts/{tool_name}.py, or index.js/index.ts"
    )


# ツールのメタデータ（discover_tools で自動検出される形式）
TOOLS = {
    "search_skills": search_skills,
    "load_skill": load_skill,
    "list_loaded_skills": list_loaded_skills,
    "execute_skill": execute_skill,
}
