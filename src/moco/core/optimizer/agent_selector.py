"""
AgentSelector - 適応的エージェント選択

タスクスコアに基づいて必要なエージェントを選択する。
不要なエージェント呼び出しを削減してコスト最適化。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from .task_analyzer import TaskScores, TaskAnalyzer
from .config import OptimizerConfig


@dataclass
class SelectionResult:
    """エージェント選択結果"""
    depth: str                          # "flat" | "light" | "structured"
    agents: List[str]                   # 選択されたエージェント
    skipped: List[str] = field(default_factory=list)  # スキップされたエージェント
    reason: str = ""                    # 選択理由
    total_score: int = 0                # 総合スコア


class AgentSelector:
    """タスクスコアに基づいてエージェントを選択"""
    
    def __init__(self, config: Optional[OptimizerConfig] = None):
        """
        Args:
            config: Optimizer設定（省略時はデフォルト設定）
        """
        self.config = config or OptimizerConfig()
        self.thresholds = self.config.get_thresholds()
        self.rules = self.config.get_agent_rules()
    
    def reload_config(self) -> None:
        """設定を再読み込み"""
        self.thresholds = self.config.get_thresholds()
        self.rules = self.config.get_agent_rules()
    
    def select(
        self,
        scores: TaskScores,
        available_agents: List[str]
    ) -> SelectionResult:
        """
        スコアに基づいてエージェントを選択
        
        Args:
            scores: タスク分析スコア
            available_agents: 利用可能なエージェント名のリスト
            
        Returns:
            SelectionResult: 選択結果
        """
        # 総合スコアを計算
        total_score = self._calculate_total(scores)
        
        # 深度を決定
        depth = self._determine_depth(total_score)
        
        # エージェントを選択
        selected = []
        skipped = []
        
        for agent in available_agents:
            # orchestrator は常にスキップ（呼び出し元）
            if agent == "orchestrator":
                continue
                
            if self._should_include(agent, scores, depth):
                selected.append(agent)
            else:
                skipped.append(agent)
        
        # 最低1つのエージェントを保証
        if not selected and available_agents:
            # デフォルトで最初の非orchestratorエージェントを選択
            for agent in available_agents:
                if agent != "orchestrator":
                    selected.append(agent)
                    if agent in skipped:
                        skipped.remove(agent)
                    break
        
        reason = self._generate_reason(scores, depth, selected, skipped)
        
        return SelectionResult(
            depth=depth,
            agents=selected,
            skipped=skipped,
            reason=reason,
            total_score=total_score
        )
    
    def _calculate_total(self, scores: TaskScores) -> int:
        """総合スコアを計算（TaskAnalyzer に委譲）"""
        return TaskAnalyzer.calculate_total_static(scores)
    
    def _determine_depth(self, total_score: int) -> str:
        """スコアから深度を決定"""
        flat_max = self.thresholds.get("flat_max", 10)
        light_max = self.thresholds.get("light_max", 25)
        
        if total_score <= flat_max:
            return "flat"
        elif total_score <= light_max:
            return "light"
        else:
            return "structured"
    
    def _should_include(
        self,
        agent: str,
        scores: TaskScores,
        depth: str
    ) -> bool:
        """エージェントを含めるべきか判定"""
        # ルールを取得（なければデフォルト動作）
        rule = self.rules.get(agent, {})
        
        # always フラグがあれば常に含める
        if rule.get("always"):
            return True
        
        # skip_when 条件をチェック
        skip_when = rule.get("skip_when", {})
        skip_types = skip_when.get("task_type", [])
        if scores.get("task_type", "other") in skip_types:
            return False
        
        # flat モードでは always のみ
        if depth == "flat":
            return False
        
        # required_when 条件をチェック
        required_when = rule.get("required_when", {})
        
        # task_type による必須チェック
        required_types = required_when.get("task_type", [])
        if scores.get("task_type", "other") in required_types:
            return True
        
        # スコアによる必須チェック
        for key, threshold in required_when.items():
            if key == "task_type":
                continue
            if key in scores and scores.get(key, 0) >= threshold:
                return True
        
        # structured モードでは全員参加
        if depth == "structured":
            return True
        
        # light モードではルールに明示的にマッチしない場合はスキップ
        return False
    
    def _generate_reason(
        self,
        scores: TaskScores,
        depth: str,
        selected: List[str],
        skipped: List[str]
    ) -> str:
        """選択理由を生成"""
        reasons = []
        
        # 深度の理由
        total = self._calculate_total(scores)
        reasons.append(f"総合スコア {total} → {depth} モード")
        
        # タスクタイプ
        reasons.append(f"タスク種類: {scores.get('task_type', 'other')}")
        
        # 選択/スキップ
        if selected:
            reasons.append(f"選択: {', '.join(selected)}")
        if skipped:
            reasons.append(f"スキップ: {', '.join(skipped)}")
        
        return " | ".join(reasons)
    
    def estimate_cost_savings(
        self,
        selection: SelectionResult,
        available_agents: List[str]
    ) -> Dict[str, Any]:
        """コスト削減効果を推定"""
        total_agents = len([a for a in available_agents if a != "orchestrator"])
        selected_agents = len(selection.agents)
        skipped_agents = len(selection.skipped)
        
        if total_agents == 0:
            return {"savings_percent": 0, "detail": "エージェントなし"}
        
        savings_percent = (skipped_agents / total_agents) * 100
        
        return {
            "total_agents": total_agents,
            "selected": selected_agents,
            "skipped": skipped_agents,
            "savings_percent": round(savings_percent, 1),
            "depth": selection.depth
        }

