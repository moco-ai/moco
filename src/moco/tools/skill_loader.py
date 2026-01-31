"""Skills loader for moco agent framework.

Skills are reusable knowledge packages that can be injected into agents,
following Claude Code's Skills format.

Supports:
- Local skills from profiles/<profile>/skills/
- External skills from GitHub repositories
- Skills registry (anthropics/skills, etc.)
- Semantic (vector) matching using embeddings
"""

import os
import re
import glob
import json
import shutil
import tempfile
import subprocess
import logging
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from .security_scanner import SecurityScanner

try:
    import yaml
except ImportError:
    yaml = None  # Will raise error when needed

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


# プロジェクトルートを取得
_MOCO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_MOCO_ROOT)


def _find_profiles_dir() -> str:
    """profiles ディレクトリを探索して見つける"""
    # 作業ディレクトリが指定されている場合は最優先
    working_dir = os.environ.get("MOCO_WORKING_DIRECTORY")
    if working_dir:
        wd_profiles = os.path.join(working_dir, "profiles")
        if os.path.exists(wd_profiles) and os.path.isdir(wd_profiles):
            return wd_profiles

    # カレントディレクトリの profiles
    cwd_profiles = os.path.join(os.getcwd(), "profiles")
    if os.path.exists(cwd_profiles) and os.path.isdir(cwd_profiles):
        return cwd_profiles

    # 現在のプロジェクトの profiles ディレクトリ
    profiles_dir = os.path.join(_PROJECT_ROOT, "profiles")
    if os.path.exists(profiles_dir) and os.path.isdir(profiles_dir):
        return profiles_dir

    # 依存関係としての moco パッケージ内の profiles ディレクトリ
    moco_profiles_dir = os.path.join(_MOCO_ROOT, "profiles")
    if os.path.exists(moco_profiles_dir) and os.path.isdir(moco_profiles_dir):
        return moco_profiles_dir

    raise RuntimeError("profiles directory not found")


@dataclass
class SkillConfig:
    """Skill configuration loaded from SKILL.md
    
    Claude Skills 互換フォーマット:
    - name: スキル名
    - description: 説明
    - triggers: 発動トリガー（キーワード）
    - version: バージョン
    - allowed_tools: 許可されたツール（Claude互換: allowed-tools）
    - content: 知識・ルール本文
    - is_logic: ロジック（JS/TS/Py）を含むか
    - path: スキルディレクトリの絶対パス
    - exposed_tools: スキルが露出するツール定義
    """
    name: str
    description: str
    triggers: List[str]
    version: str
    content: str
    allowed_tools: List[str] = field(default_factory=list)
    path: str = ""
    is_logic: bool = False
    exposed_tools: Dict[str, Any] = field(default_factory=dict)

    def matches_input(self, user_input: str) -> bool:
        """Check if this skill matches the user input based on triggers or description keywords"""
        input_lower = user_input.lower()
        
        # triggers ベースのマッチング
        if self.triggers:
            if any(trigger.lower() in input_lower for trigger in self.triggers):
                return True
        
        # スキル名が入力に含まれていればマッチ（部分一致）
        name_variants = [
            self.name.lower(),
            self.name.replace('-', ' ').lower(),
            self.name.replace('-', '').lower(),
        ]
        for variant in name_variants:
            if variant in input_lower:
                return True
        
        # スキル名の各パーツが入力に含まれていればマッチ
        name_parts = self.name.lower().split('-')
        if len(name_parts) >= 2:
            # 例: frontend-design → "frontend" and "design" が両方含まれていればマッチ
            if all(part in input_lower for part in name_parts if len(part) >= 3):
                return True
        
        # description からキーワードを抽出してマッチング（フォールバック）
        if self.description:
            # 重要なキーワードを抽出
            important_keywords = set()
            for word in self.description.split():
                word_clean = word.lower().strip('.,()[]/:')
                # 4文字以上で、技術的なキーワードを優先
                if len(word_clean) >= 4:
                    important_keywords.add(word_clean)
            
            # 一般的すぎる単語を除外
            stop_words = {'this', 'that', 'with', 'from', 'when', 'user', 'asks', 'create', 
                         'build', 'make', 'code', 'using', 'includes', 'examples', 'skill',
                         'guide', 'helps', 'uses', 'like', 'such', 'also', 'some', 'more',
                         'than', 'into', 'your', 'they', 'will', 'have', 'been', 'about'}
            important_keywords -= stop_words
            
            # 入力に含まれるキーワードをカウント
            matched_keywords = [kw for kw in important_keywords if kw in input_lower]
            if len(matched_keywords) >= 2:
                return True
        
        return False


class SkillLoader:
    """Loader for Skills from a profile directory.

    Skills are defined as:
    profiles/<profile>/skills/<skill-name>/SKILL.md

    SKILL.md format:
    ---
    name: python-style
    description: Python coding guidelines
    triggers:
      - python
      - .py
    version: 1.0.0
    ---
    (content: knowledge/rules)
    
    Supports:
    - Keyword-based matching (triggers, name, description)
    - Semantic (vector) matching using embeddings
    """

    def __init__(self, profile: str = "default", use_semantic: bool = True):
        self.profile = profile
        self.use_semantic = use_semantic
        profiles_dir = _find_profiles_dir()
        self.skills_dir = os.path.join(profiles_dir, self.profile, "skills")
        
        # Semantic memory for vector matching
        self._semantic_memory = None
        self._skills_indexed = False
        self._skill_mtimes: Dict[str, float] = {}  # スキルごとの更新日時
        self._indexed_skills: set = set()  # インデックス済みスキル名

    def _get_skill_mtimes(self) -> Dict[str, float]:
        """各スキルファイルの更新日時を取得"""
        mtimes = {}
        if not os.path.exists(self.skills_dir):
            return mtimes
        
        search_path = os.path.join(self.skills_dir, "*", "SKILL.md")
        for file_path in glob.glob(search_path):
            try:
                skill_name = os.path.basename(os.path.dirname(file_path))
                mtimes[skill_name] = os.path.getmtime(file_path)
            except OSError:
                pass
        return mtimes

    def _get_index_changes(self, skills: Dict[str, Any]) -> tuple:
        """インデックスの差分を計算（追加、更新、削除）"""
        current_mtimes = self._get_skill_mtimes()
        current_names = set(skills.keys())
        indexed_names = self._indexed_skills
        
        # 新規追加: 現在あるがインデックスにない
        to_add = current_names - indexed_names
        
        # 削除: インデックスにあるが現在ない
        to_remove = indexed_names - current_names
        
        # 更新: 両方にあるが更新日時が変わった
        to_update = set()
        for name in current_names & indexed_names:
            old_mtime = self._skill_mtimes.get(name, 0)
            new_mtime = current_mtimes.get(name, 0)
            if new_mtime > old_mtime:
                to_update.add(name)
        
        return to_add, to_update, to_remove, current_mtimes

    def _needs_reindex(self) -> bool:
        """インデックスの更新が必要かチェック"""
        if not self._skills_indexed:
            return True
        
        current_mtimes = self._get_skill_mtimes()
        current_names = set(current_mtimes.keys())
        
        # スキルが追加/削除された
        if current_names != self._indexed_skills:
            return True
        
        # いずれかのスキルが更新された
        for name, mtime in current_mtimes.items():
            if mtime > self._skill_mtimes.get(name, 0):
                return True
        
        return False

    def load_skills(self) -> Dict[str, SkillConfig]:
        """Load all skills from the profile's skills directory"""
        # プロファイル変更に対応するため、毎回再計算
        profiles_dir = _find_profiles_dir()
        self.skills_dir = os.path.join(profiles_dir, self.profile, "skills")
        
        skills = {}

        if not os.path.exists(self.skills_dir) or not os.path.isdir(self.skills_dir):
            return skills

        # skills/<skill-name>/SKILL.md を探索
        search_path = os.path.join(self.skills_dir, "*", "SKILL.md")
        files = glob.glob(search_path)

        for file_path in files:
            try:
                skill = self._parse_skill_file(file_path)
                if skill:
                    skills[skill.name] = skill
            except Exception as e:
                logger.warning(f"Failed to load skill from {file_path}: {e}")

        return skills

    def _parse_skill_file(self, file_path: str) -> Optional[SkillConfig]:
        """Parse SKILL.md file and return SkillConfig"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 正規表現で先頭の YAML frontmatter を抽出
        pattern = r'^---\n(.*?)\n---\n(.*)$'
        match = re.match(pattern, content, re.DOTALL)
        if not match:
            return None

        frontmatter_yaml = match.group(1)
        body = match.group(2).strip()

        try:
            metadata = yaml.safe_load(frontmatter_yaml)
        except yaml.YAMLError:
            return None

        # metadata が None の場合（空の frontmatter）
        if metadata is None:
            metadata = {}

        name = metadata.get("name")
        if not name:
            # ファイル名からスキル名を推測（ディレクトリ名）
            skill_dir = os.path.dirname(file_path)
            name = os.path.basename(skill_dir)

        # name が空文字列のフォールバック
        if not name:
            return None

        # triggers が文字列の場合はリストに変換、None も考慮
        triggers = metadata.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [triggers]
        elif not isinstance(triggers, list):
            triggers = []

        # allowed-tools (Claude互換) または allowed_tools をパース
        allowed_tools = metadata.get("allowed-tools") or metadata.get("allowed_tools") or []
        if isinstance(allowed_tools, str):
            allowed_tools = [allowed_tools]
        elif not isinstance(allowed_tools, list):
            allowed_tools = []

        skill_dir = os.path.dirname(file_path)
        # ロジック判定 (JS, TS, Py)
        logic_files = ["index.js", "index.ts", "package.json"]
        is_logic = any(os.path.exists(os.path.join(skill_dir, f)) for f in logic_files)
        
        # scripts ディレクトリや .py ファイルがある場合もロジックありとみなす
        if not is_logic:
            is_logic = os.path.exists(os.path.join(skill_dir, "scripts")) or \
                       any(f.endswith(".py") for f in os.listdir(skill_dir) if f != "__init__.py")

        # 露出ツールの定義 (YAML内の tools セクション)
        exposed_tools_raw = metadata.get("tools", {}) or {}
        # Normalize to dict:
        # - dict: {tool_name: {description, parameters,...}}
        # - list: [{name: tool_name, ...}, ...]  (seen in some local skills)
        exposed_tools: Dict[str, Any] = {}
        if isinstance(exposed_tools_raw, dict):
            exposed_tools = exposed_tools_raw
        elif isinstance(exposed_tools_raw, list):
            for item in exposed_tools_raw:
                # Common format: {name: "...", description: "...", ...}
                if isinstance(item, dict):
                    name_val = item.get("name")
                    if isinstance(name_val, str) and name_val.strip():
                        tool_name = name_val.strip()
                        # Keep the rest of the fields as tool definition
                        tool_def = dict(item)
                        tool_def.pop("name", None)
                        exposed_tools[tool_name] = tool_def
                        continue
                    # Alternate compact format: {tool_name: {...}}
                    if len(item) == 1:
                        k = next(iter(item.keys()))
                        v = item.get(k)
                        if isinstance(k, str) and k.strip():
                            exposed_tools[k.strip()] = v if isinstance(v, dict) else {"value": v}
                            continue
                # Fallback: string tool names
                if isinstance(item, str) and item.strip():
                    exposed_tools[item.strip()] = {}
        else:
            exposed_tools = {}

        return SkillConfig(
            name=name,
            description=metadata.get("description", ""),
            triggers=triggers,
            version=metadata.get("version", "1.0.0"),
            content=body,
            allowed_tools=allowed_tools,
            path=skill_dir,
            is_logic=is_logic,
            exposed_tools=exposed_tools
        )

    def match_skills(
        self, 
        user_input: str, 
        skills: Optional[Dict[str, SkillConfig]] = None,
        use_semantic: Optional[bool] = None,
        max_skills: int = 3
    ) -> List[SkillConfig]:
        """Find skills that match the user input based on triggers and/or semantic similarity.

        Args:
            user_input: The user's input text
            skills: Optional pre-loaded skills dict. If None, loads from self.skills_dir
            use_semantic: Override semantic matching setting. If None, uses self.use_semantic
            max_skills: Maximum number of skills to return

        Returns:
            List of matching SkillConfig objects (sorted by relevance)
        """
        if skills is None:
            skills = self.load_skills()

        if not skills:
            return []

        # スコア付きマッチング結果 {skill_name: score}（低いほど良い）
        scores: Dict[str, float] = {}

        # キーワードベースのマッチング（スコア = 0 で最優先）
        for skill in skills.values():
            if skill.matches_input(user_input):
                scores[skill.name] = 0.0  # キーワードマッチは最優先

        # セマンティックマッチング（有効な場合）
        should_use_semantic = use_semantic if use_semantic is not None else self.use_semantic
        
        if should_use_semantic:
            try:
                semantic_results = self._semantic_match(user_input, skills, top_k=max_skills + 2)
                for name, score in semantic_results:
                    if name not in scores:  # キーワードマッチがない場合のみ
                        scores[name] = score
                    else:
                        # 両方でマッチした場合はスコアを下げる（より優先）
                        scores[name] = min(scores[name], score - 0.5)
            except Exception as e:
                logger.debug(f"Semantic matching failed, falling back to keyword: {e}")

        # スコアでソートして上位のみ返す
        sorted_names = sorted(scores.keys(), key=lambda n: scores[n])
        top_names = sorted_names[:max_skills]
        
        return [skills[name] for name in top_names if name in skills]

    def _get_semantic_memory(self):
        """Get or initialize semantic memory for skill matching."""
        if self._semantic_memory is not None:
            return self._semantic_memory
        
        try:
            from ..storage.semantic_memory import SemanticMemory
            
            # Skills 用のセマンティックメモリを作成
            db_path = os.path.join(self.skills_dir, ".skills_semantic.db")
            self._semantic_memory = SemanticMemory(db_path=db_path)
            return self._semantic_memory
        except Exception as e:
            logger.warning(f"Failed to initialize semantic memory: {e}")
            return None

    def _index_skills_for_semantic(self, skills: Dict[str, SkillConfig]):
        """Index skills for semantic search (incremental update)."""
        # 更新が必要かチェック
        if not self._needs_reindex():
            return
        
        memory = self._get_semantic_memory()
        if memory is None:
            return
        
        try:
            # 差分を計算
            to_add, to_update, to_remove, current_mtimes = self._get_index_changes(skills)
            
            # 初回は全スキルを追加
            if not self._skills_indexed:
                to_add = set(skills.keys())
                to_update = set()
                to_remove = set()
            
            changes_made = False
            
            # 削除されたスキルをインデックスから削除
            for name in to_remove:
                try:
                    memory.delete_document(f"skill:{name}")
                    self._indexed_skills.discard(name)
                    changes_made = True
                    logger.debug(f"Removed skill from index: {name}")
                except Exception:
                    pass
            
            # 更新されたスキルを再インデックス（まず削除してから追加）
            for name in to_update:
                if name in skills:
                    try:
                        memory.delete_document(f"skill:{name}")
                    except Exception:
                        pass
                    to_add.add(name)
            
            # 新規/更新スキルを追加
            for name in to_add:
                if name not in skills:
                    continue
                skill = skills[name]
                
                # description + name + triggers を結合してインデックス
                text_parts = [skill.name.replace('-', ' ')]
                if skill.description:
                    text_parts.append(skill.description)
                if skill.triggers:
                    text_parts.extend(skill.triggers)
                
                combined_text = " ".join(text_parts)
                
                try:
                    memory.add_document(
                        doc_id=f"skill:{name}",
                        content=combined_text,
                        metadata={"skill_name": name, "type": "skill"}
                    )
                    self._indexed_skills.add(name)
                    changes_made = True
                except Exception as e:
                    logger.debug(f"Failed to index skill {name}: {e}")
            
            # 状態を更新
            self._skills_indexed = True
            self._skill_mtimes = current_mtimes
            
            if changes_made:
                added = len(to_add)
                removed = len(to_remove)
                logger.info(f"Skill index updated: +{added} -{removed} (total: {len(self._indexed_skills)})")
        except Exception as e:
            logger.warning(f"Failed to index skills: {e}")

    def _semantic_match(
        self, 
        user_input: str, 
        skills: Dict[str, SkillConfig],
        threshold: float = 0.5,
        top_k: int = 5
    ) -> List[Tuple[str, float]]:
        """Find skills using semantic (vector) similarity.

        Args:
            user_input: The user's input text
            skills: Pre-loaded skills dict
            threshold: Minimum similarity score (lower L2 distance = more similar)
            top_k: Maximum number of results

        Returns:
            List of (skill_name, score) tuples
        """
        # スキルをインデックス
        self._index_skills_for_semantic(skills)
        
        memory = self._get_semantic_memory()
        if memory is None:
            return []
        
        try:
            results = memory.search(user_input, top_k=top_k)
            
            matches = []
            for result in results:
                # L2 距離が小さいほど類似度が高い
                # threshold は距離の上限（小さい値 = より厳格）
                # text-embedding-004 の場合、距離 < 0.8 が強く関連あり
                score = result.get('score', float('inf'))
                if score < 0.8:  # 距離が0.8未満なら強く関連あり（厳格化）
                    skill_name = result.get('metadata', {}).get('skill_name')
                    if skill_name:
                        matches.append((skill_name, score))
            
            return matches
        except Exception as e:
            logger.debug(f"Semantic search failed: {e}")
            return []

    def rebuild_semantic_index(self):
        """Rebuild the semantic index for all skills."""
        self._skills_indexed = False
        
        memory = self._get_semantic_memory()
        if memory:
            memory.clear()
        
        skills = self.load_skills()
        self._index_skills_for_semantic(skills)
        
        return len(skills)

    # ========== External Skills Management ==========

    def list_installed_skills(self) -> List[Dict[str, str]]:
        """List all installed skills with metadata"""
        skills = self.load_skills()
        result = []
        for name, skill in skills.items():
            skill_dir = os.path.join(self.skills_dir, name)
            source_file = os.path.join(skill_dir, ".source")
            source = "local"
            if os.path.exists(source_file):
                with open(source_file, 'r') as f:
                    source = f.read().strip()
            result.append({
                "name": name,
                "description": skill.description,
                "version": skill.version,
                "source": source,
                "triggers": skill.triggers
            })
        return result

    def install_skill_from_github(
        self,
        repo: str,
        skill_name: str,
        branch: str = "main"
    ) -> Tuple[bool, str]:
        """Install a single skill from a GitHub repository.

        Args:
            repo: GitHub repo in format "owner/repo" (e.g., "anthropics/skills")
            skill_name: Name of the skill to install
            branch: Branch to use (default: main)

        Returns:
            Tuple of (success: bool, message: str)
        """
        # skills ディレクトリを確保
        os.makedirs(self.skills_dir, exist_ok=True)

        # 一時ディレクトリにクローン
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_url = f"https://github.com/{repo}.git"
            try:
                # sparse checkout で skills ディレクトリのみ取得
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", branch, 
                     "--filter=blob:none", "--sparse", repo_url, temp_dir],
                    check=True, capture_output=True, text=True
                )
                subprocess.run(
                    ["git", "-C", temp_dir, "sparse-checkout", "set", f"skills/{skill_name}"],
                    check=True, capture_output=True, text=True
                )
            except subprocess.CalledProcessError as e:
                return False, f"Git clone failed: {e.stderr}"

            # スキルのソースパスを探す
            source_path = os.path.join(temp_dir, "skills", skill_name)
            if not os.path.exists(source_path):
                # ルートレベルにスキルがある場合
                source_path = os.path.join(temp_dir, skill_name)
            if not os.path.exists(source_path):
                return False, f"Skill '{skill_name}' not found in {repo}"

            # SKILL.md があるか確認
            skill_file = os.path.join(source_path, "SKILL.md")
            if not os.path.exists(skill_file):
                return False, f"SKILL.md not found in {skill_name}"

            # --- Security Scan (Pre-install) ---
            scanner = SecurityScanner()
            findings = scanner.scan_directory(source_path)
            high_findings = [f for f in findings if f.get('severity') == 'high']
            
            if high_findings:
                msg = f"CRITICAL SECURITY ALERT: Skill '{skill_name}' contains high-risk patterns and will NOT be installed.\n{scanner.generate_report(high_findings)}"
                logger.error(msg)
                return False, f"Security risk blocked: {len(high_findings)} high-severity issues found."
            
            if findings:
                logger.warning(f"Security Warning for skill '{skill_name}':\n{scanner.generate_report(findings)}")
            # -----------------------------------

            # インストール先
            dest_path = os.path.join(self.skills_dir, skill_name)
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)

            # コピー
            shutil.copytree(source_path, dest_path)

            # ソース情報を記録
            with open(os.path.join(dest_path, ".source"), 'w') as f:
                f.write(f"github:{repo}:{skill_name}@{branch}")

            # インデックスを再構築
            self._skills_indexed = False
            logger.info(f"Rebuilding skill index after installing '{skill_name}'")

            return True, f"Installed '{skill_name}' from {repo}"

    def install_skills_from_repo(
        self,
        repo: str,
        branch: str = "main",
        category: Optional[str] = None
    ) -> Tuple[int, List[str]]:
        """Install all skills from a GitHub repository.

        Args:
            repo: GitHub repo in format "owner/repo"
            branch: Branch to use
            category: Optional category filter (e.g., "development")

        Returns:
            Tuple of (count: int, skill_names: List[str])
        """
        os.makedirs(self.skills_dir, exist_ok=True)
        installed = []

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_url = f"https://github.com/{repo}.git"
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", branch, repo_url, temp_dir],
                    check=True, capture_output=True, text=True
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Git clone failed: {e.stderr}")
                return 0, []

            # skills ディレクトリを探す
            skills_root = os.path.join(temp_dir, "skills")
            if not os.path.exists(skills_root):
                # ルートに skills がない場合、ルート直下を探す
                skills_root = temp_dir

            # SKILL.md を持つディレクトリを探索
            for root, dirs, files in os.walk(skills_root):
                if "SKILL.md" in files:
                    skill_name = os.path.basename(root)
                    
                    # カテゴリフィルタ
                    if category:
                        rel_path = os.path.relpath(root, skills_root)
                        if not rel_path.startswith(category):
                            continue

                    # --- Security Scan (Pre-install) ---
                    scanner = SecurityScanner()
                    findings = scanner.scan_directory(root)
                    high_findings = [f for f in findings if f.get('severity') == 'high']
                    
                    if high_findings:
                        logger.error(f"BLOCKING SKILL '{skill_name}': High-risk patterns detected.\n{scanner.generate_report(high_findings)}")
                        continue # Skip this skill but continue with others
                    
                    if findings:
                        logger.warning(f"Security Warning for skill '{skill_name}':\n{scanner.generate_report(findings)}")
                    # -----------------------------------

                    dest_path = os.path.join(self.skills_dir, skill_name)
                    if os.path.exists(dest_path):
                        shutil.rmtree(dest_path)

                    shutil.copytree(root, dest_path)

                    # ソース情報を記録
                    with open(os.path.join(dest_path, ".source"), 'w') as f:
                        f.write(f"github:{repo}@{branch}")

                    installed.append(skill_name)

        # インストール後にインデックスを再構築
        if installed:
            self._skills_indexed = False
            logger.info(f"Rebuilding skill index after installing {len(installed)} skills")

        return len(installed), installed

    def uninstall_skill(self, skill_name: str) -> Tuple[bool, str]:
        """Uninstall a skill.

        Args:
            skill_name: Name of the skill to uninstall

        Returns:
            Tuple of (success: bool, message: str)
        """
        skill_path = os.path.join(self.skills_dir, skill_name)
        if not os.path.exists(skill_path):
            return False, f"Skill '{skill_name}' not found"

        shutil.rmtree(skill_path)
        
        # インデックスを再構築
        self._skills_indexed = False
        logger.info(f"Rebuilding skill index after uninstalling '{skill_name}'")
        
        return True, f"Uninstalled '{skill_name}'"

    def sync_from_registry(self, registry: str = "anthropics") -> Tuple[int, List[str]]:
        """Sync skills from a known registry.

        Args:
            registry: Registry name. Supported:
                - "anthropics" -> anthropics/skills
                - "community" -> alirezarezvani/claude-skills
                - "claude-code" -> daymade/claude-code-skills

        Returns:
            Tuple of (count: int, skill_names: List[str])
        """
        registries = {
            "anthropics": "anthropics/skills",
            "community": "alirezarezvani/claude-skills",
            "claude-code": "daymade/claude-code-skills",
            "collection": "abubakarsiddik31/claude-skills-collection",
            "remotion": "remotion-dev/skills",
        }

        if registry not in registries:
            logger.error(f"Unknown registry: {registry}. Available: {list(registries.keys())}")
            return 0, []

        repo = registries[registry]
        logger.info(f"Syncing skills from {repo}...")
        return self.install_skills_from_repo(repo)

    def search_skills(self, query: str) -> List[Dict[str, str]]:
        """Search installed skills by name or description.

        Args:
            query: Search query

        Returns:
            List of matching skill metadata
        """
        skills = self.list_installed_skills()
        query_lower = query.lower()
        return [
            s for s in skills
            if query_lower in s["name"].lower() or query_lower in s["description"].lower()
        ]

    # ========== Remote Skills Registry ==========

    def search_remote_skills(
        self,
        query: str,
        registries: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Search skills from remote registries without installing.

        Args:
            query: Search query
            registries: List of registry names. Default: ["anthropics"]

        Returns:
            List of matching skill metadata from remote
        """
        if registries is None:
            registries = ["anthropics"]

        # レジストリのメタデータキャッシュを取得/更新
        all_results = []
        for registry in registries:
            cache = self._get_registry_cache(registry)
            if cache:
                query_lower = query.lower()
                for skill_meta in cache:
                    name = skill_meta.get("name", "").lower()
                    desc = skill_meta.get("description", "").lower()
                    if query_lower in name or query_lower in desc:
                        skill_meta["registry"] = registry
                        all_results.append(skill_meta)

        return all_results

    def _get_registry_cache(self, registry: str) -> List[Dict[str, Any]]:
        """Get or update registry metadata cache."""
        registries = {
            "anthropics": "anthropics/skills",
            "community": "alirezarezvani/claude-skills",
            "claude-code": "daymade/claude-code-skills",
        }

        if registry not in registries:
            return []

        repo = registries[registry]
        cache_file = os.path.join(self.skills_dir, f".cache_{registry}.json")

        # キャッシュが24時間以内なら再利用
        if os.path.exists(cache_file):
            mtime = os.path.getmtime(cache_file)
            import time
            if time.time() - mtime < 86400:  # 24 hours
                try:
                    with open(cache_file, 'r') as f:
                        return json.load(f)
                except Exception:
                    pass

        # GitHub API でメタデータを取得
        try:
            return self._fetch_registry_metadata(repo, cache_file)
        except Exception as e:
            logger.warning(f"Failed to fetch registry {registry}: {e}")
            return []

    def _fetch_registry_metadata(self, repo: str, cache_file: str) -> List[Dict[str, Any]]:
        """Fetch skill metadata from GitHub repository."""
        import urllib.request
        import urllib.error

        # GitHub API で skills ディレクトリの内容を取得
        api_url = f"https://api.github.com/repos/{repo}/contents/skills"
        
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "moco-skill-loader"})
            with urllib.request.urlopen(req, timeout=10) as response:
                dirs = json.loads(response.read().decode())
        except Exception as e:
            logger.debug(f"GitHub API failed: {e}")
            return []

        skills_meta = []
        for item in dirs:
            if item.get("type") != "dir":
                continue
            
            skill_name = item.get("name")
            # SKILL.md の raw URL を取得
            skill_md_url = f"https://raw.githubusercontent.com/{repo}/main/skills/{skill_name}/SKILL.md"
            
            try:
                req = urllib.request.Request(skill_md_url, headers={"User-Agent": "moco-skill-loader"})
                with urllib.request.urlopen(req, timeout=5) as response:
                    content = response.read().decode()
                
                # frontmatter をパース
                meta = self._parse_frontmatter(content)
                if meta:
                    meta["name"] = meta.get("name", skill_name)
                    meta["repo"] = repo
                    meta["url"] = skill_md_url
                    skills_meta.append(meta)
            except Exception:
                # 個別のスキル取得失敗は無視
                pass

        # キャッシュを保存
        os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
        try:
            with open(cache_file, 'w') as f:
                json.dump(skills_meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return skills_meta

    def _parse_frontmatter(self, content: str) -> Optional[Dict[str, Any]]:
        """Parse YAML frontmatter from content."""
        pattern = r'^---\n(.*?)\n---'
        match = re.match(pattern, content, re.DOTALL)
        if not match:
            return None
        
        try:
            return yaml.safe_load(match.group(1))
        except Exception:
            return None

    def fetch_skill_on_demand(
        self,
        skill_name: str,
        registry: str = "anthropics"
    ) -> Optional[SkillConfig]:
        """Fetch a skill from remote registry on-demand (without permanent install).

        Args:
            skill_name: Name of the skill
            registry: Registry name

        Returns:
            SkillConfig if found, None otherwise
        """
        registries = {
            "anthropics": "anthropics/skills",
            "community": "alirezarezvani/claude-skills",
            "claude-code": "daymade/claude-code-skills",
        }

        if registry not in registries:
            return None

        repo = registries[registry]
        skill_md_url = f"https://raw.githubusercontent.com/{repo}/main/skills/{skill_name}/SKILL.md"

        try:
            import urllib.request
            req = urllib.request.Request(skill_md_url, headers={"User-Agent": "moco-skill-loader"})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode()

            # frontmatter + body をパース
            pattern = r'^---\n(.*?)\n---\n(.*)$'
            match = re.match(pattern, content, re.DOTALL)
            if not match:
                return None

            metadata = yaml.safe_load(match.group(1)) or {}
            body = match.group(2).strip()

            triggers = metadata.get("triggers") or []
            if isinstance(triggers, str):
                triggers = [triggers]

            allowed_tools = metadata.get("allowed-tools") or metadata.get("allowed_tools") or []
            if isinstance(allowed_tools, str):
                allowed_tools = [allowed_tools]

            return SkillConfig(
                name=metadata.get("name", skill_name),
                description=metadata.get("description", ""),
                triggers=triggers,
                version=metadata.get("version", "1.0.0"),
                content=body,
                allowed_tools=allowed_tools
            )
        except Exception as e:
            logger.debug(f"Failed to fetch skill {skill_name}: {e}")
            return None

    def match_skills_with_remote(
        self,
        user_input: str,
        skills: Optional[Dict[str, SkillConfig]] = None,
        registries: Optional[List[str]] = None,
        max_skills: int = 3
    ) -> List[SkillConfig]:
        """Match skills from remote registries first, then local as fallback.

        基本はリモートから最適なスキルを探す。ローカルはキャッシュとして活用。

        Args:
            user_input: User input
            skills: Local skills dict (used as cache)
            registries: Remote registries to search
            max_skills: Maximum skills to return

        Returns:
            List of SkillConfig (remote preferred, local as fallback)
        """
        if skills is None:
            skills = self.load_skills()

        if registries is None:
            registries = ["anthropics"]

        matched_skills: List[SkillConfig] = []
        matched_names: set = set()

        # 1. リモートレジストリからセマンティック検索
        for registry in registries:
            try:
                remote_results = self._search_remote_semantic(user_input, registry, top_k=max_skills)
                
                for remote_meta in remote_results:
                    if len(matched_skills) >= max_skills:
                        break
                    
                    skill_name = remote_meta.get("name")
                    if skill_name in matched_names:
                        continue
                    
                    # ローカルにキャッシュがあればそれを使う
                    if skill_name in skills:
                        matched_skills.append(skills[skill_name])
                        matched_names.add(skill_name)
                        logger.info(f"Using cached skill '{skill_name}'")
                        logger.debug(f"[Skills] Using cached: {skill_name}")
                    else:
                        # リモートからオンデマンドで取得
                        skill = self.fetch_skill_on_demand(skill_name, registry)
                        if skill:
                            matched_skills.append(skill)
                            matched_names.add(skill_name)
                            logger.info(f"Fetched skill '{skill_name}' on-demand from {registry}")
                            logger.debug(f"[Skills] Fetched from remote: {skill_name} ({registry})")
            except Exception as e:
                logger.warning(f"Remote search failed for {registry}: {e}")

        # 2. リモートで見つからなければローカルにフォールバック
        if len(matched_skills) < max_skills:
            local_matches = self.match_skills(user_input, skills, max_skills=max_skills)
            for skill in local_matches:
                if len(matched_skills) >= max_skills:
                    break
                if skill.name not in matched_names:
                    matched_skills.append(skill)
                    matched_names.add(skill.name)

        return matched_skills[:max_skills]

    def _translate_query_to_english(self, query: str) -> str:
        """Translate query to English using LLM for better skill matching."""
        # 英語のみの場合はそのまま返す
        if query.isascii():
            return query
        
        try:
            from google import genai
            from google.genai import types
            
            api_key = (
                os.environ.get("GENAI_API_KEY") or
                os.environ.get("GEMINI_API_KEY") or
                os.environ.get("GOOGLE_API_KEY")
            )
            if not api_key:
                return query
            
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=f"Translate to English (only output the translation, no explanation): {query}",
                config=types.GenerateContentConfig(
                    max_output_tokens=100,
                    temperature=0.0
                )
            )
            translated = response.text.strip()
            logger.info(f"Translated query: '{query}' -> '{translated}'")
            # verbose 用に print も出力
            logger.debug(f"[Skills] Translated: '{query}' -> '{translated}'")
            return translated
        except Exception as e:
            logger.debug(f"Translation failed: {e}")
            return query

    def _search_remote_semantic(
        self,
        query: str,
        registry: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Search remote registry using embedding-based someantic matching."""
        cache = self._get_registry_cache(registry)
        if not cache:
            return []

        # 日本語クエリを英語に翻訳
        english_query = self._translate_query_to_english(query)

        try:
            # Gemini 埋め込みを使ってセマンティック検索
            from ..storage.semantic_memory import SemanticMemory, GENAI_AVAILABLE
            
            if not GENAI_AVAILABLE:
                return self._search_remote_keyword(query, cache, top_k)
            
            # リモートスキル用のセマンティックメモリ
            db_path = os.path.join(self.skills_dir, f".remote_{registry}_semantic.db")
            memory = SemanticMemory(db_path=db_path)
            
            # キャッシュをインデックス（まだなければ）
            if memory.index.ntotal == 0:
                for skill_meta in cache:
                    name = skill_meta.get("name", "")
                    desc = skill_meta.get("description", "")
                    text = f"{name}: {desc}"
                    try:
                        memory.add_document(
                            doc_id=f"remote:{registry}:{name}",
                            content=text,
                            metadata={"skill_name": name, "registry": registry}
                        )
                    except Exception:
                        pass
            
            # セマンティック検索（翻訳されたクエリを使用）
            results = memory.search(english_query, top_k=top_k)
            
            # 結果をスキルメタデータに変換
            matched = []
            for result in results:
                skill_name = result.get('metadata', {}).get('skill_name')
                if skill_name:
                    # キャッシュから完全なメタデータを取得
                    for skill_meta in cache:
                        if skill_meta.get("name") == skill_name:
                            matched.append(skill_meta)
                            break
            
            return matched
            
        except Exception as e:
            logger.warning(f"Semantic search failed, falling back to keyword: {e}")
            return self._search_remote_keyword(query, cache, top_k)

    def _search_remote_keyword(
        self,
        query: str,
        cache: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Fallback keyword-based search for remote skills."""
        query_words = set(query.lower().split())
        scored_results = []

        for skill_meta in cache:
            name = skill_meta.get("name", "").lower()
            desc = skill_meta.get("description", "").lower()
            
            text = f"{name} {desc}"
            score = sum(1 for word in query_words if word in text and len(word) >= 3)
            
            if score > 0:
                scored_results.append((score, skill_meta))

        scored_results.sort(key=lambda x: x[0], reverse=True)
        return [meta for score, meta in scored_results[:top_k]]
