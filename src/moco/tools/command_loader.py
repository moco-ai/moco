from pathlib import Path
from typing import Optional, Dict, List, Any
import yaml
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

COMMAND_PATHS = [
    Path.home() / ".moco" / "commands",  # グローバル
    Path.cwd() / ".moco" / "commands",   # プロジェクト固有
]

class CustomCommand:
    def __init__(self, name: str, description: str, template: str, arguments: List[Dict[str, Any]] = None):
        self.name = name
        self.description = description
        self.template = template
        self.arguments = arguments or []

@lru_cache(maxsize=1)
def load_custom_commands() -> Dict[str, CustomCommand]:
    """カスタムコマンドをロード（キャッシュ付き）"""
    commands = {}
    
    for base_path in COMMAND_PATHS:
        if not base_path.exists():
            continue
        for cmd_file in base_path.glob("*.md"):
            try:
                cmd = parse_command_file(cmd_file)
                if cmd:
                    commands[cmd.name] = cmd
            except Exception as e:
                logger.error(f"Error loading custom command {cmd_file}: {e}")
                continue
    
    return commands

def parse_command_file(path: Path) -> Optional[CustomCommand]:
    """Markdown ファイルからコマンドをパース"""
    try:
        content = path.read_text(encoding="utf-8")
        stripped_content = content.lstrip()
        
        # YAML front matter を抽出
        if stripped_content.startswith('---'):
            parts = stripped_content.split('---', 2)
            if len(parts) >= 3:
                try:
                    front_matter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError as ye:
                    logger.error(f"Failed to parse YAML in {path}: {ye}")
                    return None
                    
                template = parts[2].strip()
                
                return CustomCommand(
                    name=path.stem,
                    description=front_matter.get('description', ''),
                    template=template,
                    arguments=front_matter.get('arguments', [])
                )
    except Exception as e:
        logger.error(f"Error reading command file {path}: {e}")
        return None
    return None

def render_command(cmd: CustomCommand, args: Dict[str, str]) -> str:
    """テンプレートに引数を埋め込む"""
    result = cmd.template
    for key, value in args.items():
        result = result.replace(f'{{{{{key}}}}}', value)
    return result

def parse_command_args(expected_args: List[Dict[str, Any]], provided_args: List[str]) -> Dict[str, str]:
    """引数リストを辞書に変換"""
    arg_dict = {}
    for i, arg_meta in enumerate(expected_args):
        name = arg_meta.get('name')
        if i < len(provided_args):
            arg_dict[name] = provided_args[i]
        elif arg_meta.get('required'):
            arg_dict[name] = ""
    return arg_dict

def clear_command_cache():
    """コマンドキャッシュをクリア（テスト用や明示的なリロード用）"""
    load_custom_commands.cache_clear()
