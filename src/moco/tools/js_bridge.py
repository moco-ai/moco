import json
import subprocess
import os
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

def execute_js_skill(skill_path: str, tool_name: str, args: Dict[str, Any]) -> Any:
    """
    Executes a JavaScript tool from a skill directory.
    Uses 'node -e' to bootstrap the execution.
    """
    # 実行用のインラインコード
    # resolve で絶対パス化し、require してツールを実行
    runner_script = f"""
    const path = require('path');
    try {{
        const skillPath = path.resolve('{skill_path}');
        const skill = require(skillPath);
        const tool = skill['{tool_name}'];
        
        if (typeof tool !== 'function') {{
            console.error(JSON.stringify({{error: `Tool "${tool_name}" not found or not a function in skill "${skill_path}"`}}));
            process.exit(1);
        }}
        
        Promise.resolve(tool({json.dumps(args)}))
            .then(result => {{
                console.log(JSON.stringify(result));
            }})
            .catch(err => {{
                console.error(JSON.stringify({{error: err.message || err}}));
                process.exit(1);
            }});
    }} catch (e) {{
        console.error(JSON.stringify({{error: e.message}}));
        process.exit(1);
    }}
    """
    
    try:
        # PATHが通っている node を使用
        node_cmd = os.environ.get("MOCO_NODE_PATH", "node")
        result = subprocess.run(
            [node_cmd, "-e", runner_script],
            capture_output=True,
            text=True,
            check=True
        )
        # stdoutの最後の行をJSONとしてパース（途中にログが混じる可能性を考慮）
        output = result.stdout.strip().split('\n')[-1]
        return json.loads(output)
    except subprocess.CalledProcessError as e:
        # stderr が JSON ならそのまま、そうでなければラップする
        try:
            err_data = json.loads(e.stderr.strip().split('\n')[-1])
            return err_data
        except Exception:
            return {"error": e.stderr.strip() or "Process failed"}
    except Exception as e:
        return {"error": str(e)}

def wrap_js_tool(skill_path: str, tool_name: str, description: str = ""):
    """
    Pythonツールとして振る舞うようにJSツールをラップする。
    """
    def js_tool_wrapper(**kwargs):
        return execute_js_skill(skill_path, tool_name, kwargs)
    
    js_tool_wrapper.__name__ = tool_name
    js_tool_wrapper.__doc__ = description or f"JS Skill Tool: {tool_name}"
    return js_tool_wrapper
