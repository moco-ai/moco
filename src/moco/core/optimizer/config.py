"""
Optimizer 設定管理

閾値と設定の永続化を担当。
"""

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

from ..llm_provider import get_analyzer_model


class OptimizerConfig:
    """Optimizer の設定と閾値を管理"""
    
    CONFIG_PATH = Path("data/optimizer/config.json")
    
    DEFAULT_CONFIG = {
        "thresholds": {
            "flat_max": 10,      # このスコア以下は flat
            "light_max": 25      # このスコア以下は light、超えると structured
        },
        "weights": {
            "quality": 0.7,      # 品質重視度
            "cost": 0.3          # コスト重視度
        },
        "safety": {
            "min_success_rate": 0.85,    # 最低成功率
            "max_threshold_change": 5     # 一度に変更できる閾値の最大値
        },
        "tuning": {
            "enabled": True,              # 自動チューニング有効
            "min_samples": 20,            # チューニングに必要な最小サンプル数
            "interval_days": 7            # チューニング間隔（日）
        },
        "analysis": {
            "model": None,  # 動的に設定（get_analyzer_model() で取得）
            "max_tokens": 150,
            "temperature": 0
        }
    }
    
    def __init__(self, profile: Optional[str] = None):
        """
        Args:
            profile: プロファイル名（将来のプロファイル別設定用）
        """
        self.profile = profile
        self.config = self._load()
        # 分析モデルを動的に設定
        if self.config.get("analysis", {}).get("model") is None:
            self.config["analysis"]["model"] = get_analyzer_model()
    
    def _load(self) -> Dict[str, Any]:
        """設定ファイルを読み込む"""
        if self.CONFIG_PATH.exists():
            try:
                with open(self.CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # デフォルトとマージ（深い階層も対応）
                return self._deep_merge(self.DEFAULT_CONFIG.copy(), saved)
            except (json.JSONDecodeError, IOError):
                return self.DEFAULT_CONFIG.copy()
        return self.DEFAULT_CONFIG.copy()
    
    def _deep_merge(self, base: dict, override: dict) -> dict:
        """辞書を深くマージする（deepcopy で副作用を防止）"""
        result = copy.deepcopy(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result
    
    def save(self) -> None:
        """設定をファイルに保存（アトミック書き込み）"""
        self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        # 一時ファイルに書き込んでからリネーム（アトミック操作）
        fd, tmp_path = tempfile.mkstemp(
            dir=self.CONFIG_PATH.parent,
            prefix=".config_",
            suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.CONFIG_PATH)
        except Exception:
            # 失敗時は一時ファイルを削除
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    
    def get_thresholds(self) -> Dict[str, int]:
        """現在の閾値を取得"""
        return self.config["thresholds"].copy()
    
    def update_thresholds(self, thresholds: Dict[str, int]) -> None:
        """閾値を更新して保存"""
        self.config["thresholds"].update(thresholds)
        self.save()
    
    def get_weights(self) -> Dict[str, float]:
        """品質/コストの重みを取得"""
        return self.config["weights"].copy()
    
    def get(self, key: str, default: Any = None) -> Any:
        """設定値を取得（ドット記法対応）
        
        例: config.get("tuning.enabled")
        """
        keys = key.split(".")
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    def set(self, key: str, value: Any) -> None:
        """設定値を更新（ドット記法対応）
        
        例: config.set("tuning.enabled", False)
        """
        keys = key.split(".")
        target = self.config
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value
        self.save()
    
    # デフォルトのエージェントルール
    DEFAULT_AGENT_RULES = {
        "architect": {
            "required_when": {"novelty": 0.5, "scope": 5},
            "skip_when": {"task_type": ["bugfix", "docs"]}
        },
        "code-reviewer": {
            "required_when": {"scope": 3},
            "skip_when": {"task_type": ["docs"]}
        },
        "backend-coder": {
            "always": True
        },
        "frontend-coder": {
            "required_when": {"task_type": ["feature"]},
            "skip_when": {"task_type": ["bugfix", "docs"]}
        },
        "doc-writer": {
            "required_when": {"task_type": ["docs", "feature"]},
            "skip_when": {"task_type": ["bugfix"]}
        }
    }
    
    def get_agent_rules(self, profile: Optional[str] = None) -> Dict[str, Any]:
        """プロファイル固有のエージェントルールを取得
        
        優先順位:
        1. profiles/{profile}/agent_rules.yaml
        2. デフォルトルール
        """
        profile_name = profile or self.profile
        
        if profile_name and YAML_AVAILABLE:
            # プロファイルディレクトリからルールを読み込み
            rules_path = self._find_rules_file(profile_name)
            if rules_path and rules_path.exists():
                try:
                    with open(rules_path, "r", encoding="utf-8") as f:
                        loaded_rules = yaml.safe_load(f)
                        if loaded_rules and isinstance(loaded_rules, dict):
                            # デフォルトとマージ
                            return self._deep_merge(
                                copy.deepcopy(self.DEFAULT_AGENT_RULES),
                                loaded_rules.get("rules", loaded_rules)
                            )
                except Exception:
                    pass  # ファイル読み込み失敗時はデフォルトを使用
        
        return copy.deepcopy(self.DEFAULT_AGENT_RULES)
    
    def _find_rules_file(self, profile: str) -> Optional[Path]:
        """プロファイルのルールファイルパスを探索"""
        # moco/profiles/{profile}/agent_rules.yaml を探す
        base_paths = [
            Path(__file__).parent.parent.parent / "profiles" / profile,
            Path("moco/profiles") / profile,
        ]
        
        for base in base_paths:
            rules_file = base / "agent_rules.yaml"
            if rules_file.exists():
                return rules_file
        
        return None

