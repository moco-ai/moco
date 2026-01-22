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
    if _skill_loader is None:
        import os
        profile = os.environ.get("MOCO_PROFILE", "development")
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


# ツールのメタデータ（discover_tools で自動検出される形式）
TOOLS = {
    "search_skills": search_skills,
    "load_skill": load_skill,
    "list_loaded_skills": list_loaded_skills,
}
