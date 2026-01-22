"""
LLM API コスト追跡モジュール。

トークン使用量と料金の追跡、予算管理、レポート生成を提供する。
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable, Any, Dict, List, Generator
from contextlib import contextmanager
from functools import wraps

logger = logging.getLogger(__name__)


# =============================================================================
# 料金表（2024-2025年最新）
# 単位: USD per 1M tokens
# =============================================================================

PRICING: Dict[str, Dict[str, float]] = {
    # Google Gemini
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.0-flash-lite": {"input": 0.0375, "output": 0.15},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash-8b": {"input": 0.0375, "output": 0.15},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.0-pro": {"input": 1.25, "output": 5.00},
    
    # OpenAI GPT-4o
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-2024-11-20": {"input": 2.50, "output": 10.00},
    "gpt-4o-2024-08-06": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o-mini-2024-07-18": {"input": 0.15, "output": 0.60},
    
    # OpenAI GPT-4
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4-turbo-preview": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-4-32k": {"input": 60.00, "output": 120.00},
    
    # OpenAI GPT-3.5
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "gpt-3.5-turbo-0125": {"input": 0.50, "output": 1.50},
    "gpt-3.5-turbo-instruct": {"input": 1.50, "output": 2.00},
    
    # OpenAI o1/o3 (reasoning)
    "o1": {"input": 15.00, "output": 60.00},
    "o1-preview": {"input": 15.00, "output": 60.00},
    "o1-mini": {"input": 3.00, "output": 12.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
    
    # Anthropic Claude 3.5
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    
    # Anthropic Claude 3
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    
    # Anthropic Claude 2
    "claude-2.1": {"input": 8.00, "output": 24.00},
    "claude-2.0": {"input": 8.00, "output": 24.00},
    "claude-instant-1.2": {"input": 0.80, "output": 2.40},
    
    # Mistral
    "mistral-large": {"input": 2.00, "output": 6.00},
    "mistral-medium": {"input": 2.70, "output": 8.10},
    "mistral-small": {"input": 0.20, "output": 0.60},
    "mistral-tiny": {"input": 0.14, "output": 0.42},
    "codestral": {"input": 0.20, "output": 0.60},
    
    # Cohere
    "command-r-plus": {"input": 2.50, "output": 10.00},
    "command-r": {"input": 0.15, "output": 0.60},
    "command": {"input": 1.00, "output": 2.00},
    
    # Meta Llama (via API providers)
    "llama-3.1-405b": {"input": 3.00, "output": 3.00},
    "llama-3.1-70b": {"input": 0.35, "output": 0.40},
    "llama-3.1-8b": {"input": 0.05, "output": 0.08},
    "llama-3-70b": {"input": 0.59, "output": 0.79},
    "llama-3-8b": {"input": 0.05, "output": 0.08},
    
    # Amazon Bedrock (Claude pricing)
    "anthropic.claude-3-5-sonnet-20241022-v2:0": {"input": 3.00, "output": 15.00},
    "anthropic.claude-3-sonnet-20240229-v1:0": {"input": 3.00, "output": 15.00},
    "anthropic.claude-3-haiku-20240307-v1:0": {"input": 0.25, "output": 1.25},
    
    # Default fallback
    "_default": {"input": 1.00, "output": 3.00},
}

# プロバイダ別のデフォルトモデル
PROVIDER_DEFAULT_MODELS: Dict[str, str] = {
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet",
    "mistral": "mistral-small",
    "cohere": "command-r",
    "meta": "llama-3.1-70b",
}


class BudgetStatus(Enum):
    """予算ステータス"""
    OK = "ok"
    WARNING = "warning"  # 80%以上使用
    CRITICAL = "critical"  # 95%以上使用
    EXCEEDED = "exceeded"  # 100%超過


@dataclass
class TokenUsage:
    """トークン使用量"""
    input_tokens: int
    output_tokens: int
    total_tokens: int = field(init=False)
    
    def __post_init__(self):
        self.total_tokens = self.input_tokens + self.output_tokens
    
    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )
    
    @classmethod
    def zero(cls) -> "TokenUsage":
        return cls(input_tokens=0, output_tokens=0)
    
    @classmethod
    def from_dict(cls, data: Dict[str, int]) -> "TokenUsage":
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
        )


@dataclass
class CostRecord:
    """コスト記録"""
    timestamp: datetime
    provider: str
    model: str
    usage: TokenUsage
    cost_usd: float
    session_id: Optional[str] = None
    agent_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "total_tokens": self.usage.total_tokens,
            "cost_usd": self.cost_usd,
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CostRecord":
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            provider=data["provider"],
            model=data["model"],
            usage=TokenUsage(
                input_tokens=data["input_tokens"],
                output_tokens=data["output_tokens"],
            ),
            cost_usd=data["cost_usd"],
            session_id=data.get("session_id"),
            agent_name=data.get("agent_name"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class BudgetCheckResult:
    """予算チェック結果"""
    status: BudgetStatus
    current_cost: float
    budget_limit: Optional[float]
    remaining: Optional[float]
    usage_percentage: Optional[float]
    message: str


@dataclass
class CostSummary:
    """コストサマリー"""
    total_cost: float
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    record_count: int
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    breakdown_by_model: Dict[str, float] = field(default_factory=dict)
    breakdown_by_agent: Dict[str, float] = field(default_factory=dict)
    breakdown_by_session: Dict[str, float] = field(default_factory=dict)


class CostTracker:
    """
    LLM API コスト追跡クラス。
    
    トークン使用量の記録、料金計算、予算管理、レポート生成を行う。
    スレッドセーフな実装。
    
    Example:
        tracker = CostTracker(budget_limit=10.0)
        
        # 使用量を記録
        usage = TokenUsage(input_tokens=1000, output_tokens=500)
        record = tracker.record("gemini", "gemini-2.0-flash", usage)
        
        # 予算チェック
        status = tracker.check_budget()
        if status.status == BudgetStatus.EXCEEDED:
            raise Exception("Budget exceeded!")
        
        # レポート生成
        report = tracker.generate_report(format="markdown")
        print(report)
    """
    
    def __init__(
        self,
        budget_limit: Optional[float] = None,
        warning_threshold: float = 0.8,
        critical_threshold: float = 0.95,
        custom_pricing: Optional[Dict[str, Dict[str, float]]] = None,
        on_budget_warning: Optional[Callable[[BudgetCheckResult], None]] = None,
        on_budget_exceeded: Optional[Callable[[BudgetCheckResult], None]] = None,
    ):
        """
        Args:
            budget_limit: 予算上限（USD）。None の場合は無制限。
            warning_threshold: 警告閾値（0-1）。デフォルト 0.8（80%）。
            critical_threshold: 危険閾値（0-1）。デフォルト 0.95（95%）。
            custom_pricing: カスタム料金表。PRICING を上書きする。
            on_budget_warning: 予算警告時のコールバック。
            on_budget_exceeded: 予算超過時のコールバック。
        """
        self._records: List[CostRecord] = []
        self._lock = threading.RLock()
        
        self.budget_limit = budget_limit
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        
        # 料金表（カスタム料金で上書き）
        self._pricing = PRICING.copy()
        if custom_pricing:
            self._pricing.update(custom_pricing)
        
        # コールバック
        self._on_budget_warning = on_budget_warning
        self._on_budget_exceeded = on_budget_exceeded
        
        # 前回の予算ステータス（重複通知防止）
        self._last_budget_status: Optional[BudgetStatus] = None
    
    def record(
        self,
        provider: str,
        model: str,
        usage: TokenUsage,
        session_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> CostRecord:
        """
        トークン使用量を記録する。
        
        Args:
            provider: プロバイダ名（gemini, openai, anthropic 等）
            model: モデル名
            usage: トークン使用量
            session_id: セッションID（オプション）
            agent_name: エージェント名（オプション）
            metadata: 追加メタデータ（オプション）
            timestamp: タイムスタンプ（オプション、デフォルトは現在時刻）
        
        Returns:
            作成された CostRecord
        """
        # 料金計算
        cost_usd = self._calculate_cost(model, usage)
        
        record = CostRecord(
            timestamp=timestamp or datetime.now(timezone.utc),
            provider=provider,
            model=model,
            usage=usage,
            cost_usd=cost_usd,
            session_id=session_id,
            agent_name=agent_name,
            metadata=metadata or {},
        )
        
        with self._lock:
            self._records.append(record)
        
        logger.debug(
            f"Cost recorded: model={model}, tokens={usage.total_tokens}, "
            f"cost=${cost_usd:.6f}, session={session_id}, agent={agent_name}"
        )
        
        # 予算チェックとコールバック
        self._check_and_notify_budget()
        
        return record
    
    def _calculate_cost(self, model: str, usage: TokenUsage) -> float:
        """料金を計算する"""
        # モデル名を正規化（小文字化、バージョン除去）
        model_key = self._normalize_model_name(model)
        
        # 料金表から取得
        pricing = self._pricing.get(model_key, self._pricing.get("_default"))
        
        input_cost = (usage.input_tokens / 1_000_000) * pricing["input"]
        output_cost = (usage.output_tokens / 1_000_000) * pricing["output"]
        
        return input_cost + output_cost
    
    def _normalize_model_name(self, model: str) -> str:
        """モデル名を正規化する"""
        model_lower = model.lower()
        
        # 完全一致
        if model_lower in self._pricing:
            return model_lower
        
        # 部分一致（プレフィックスマッチ）
        for key in self._pricing:
            if model_lower.startswith(key) or key.startswith(model_lower):
                return key
        
        # Gemini の正規化
        if "gemini" in model_lower:
            if "2.0" in model_lower and "flash" in model_lower:
                if "lite" in model_lower:
                    return "gemini-2.0-flash-lite"
                return "gemini-2.0-flash"
            if "1.5" in model_lower:
                if "pro" in model_lower:
                    return "gemini-1.5-pro"
                return "gemini-1.5-flash"
        
        # GPT の正規化
        if "gpt-4o" in model_lower:
            if "mini" in model_lower:
                return "gpt-4o-mini"
            return "gpt-4o"
        if "gpt-4" in model_lower:
            if "turbo" in model_lower:
                return "gpt-4-turbo"
            return "gpt-4"
        if "gpt-3.5" in model_lower:
            return "gpt-3.5-turbo"
        
        # Claude の正規化
        if "claude" in model_lower:
            if "3-5" in model_lower or "3.5" in model_lower:
                if "sonnet" in model_lower:
                    return "claude-3-5-sonnet"
                if "haiku" in model_lower:
                    return "claude-3-5-haiku"
            if "3" in model_lower:
                if "opus" in model_lower:
                    return "claude-3-opus"
                if "sonnet" in model_lower:
                    return "claude-3-sonnet"
                if "haiku" in model_lower:
                    return "claude-3-haiku"
        
        return model_lower
    
    def _check_and_notify_budget(self) -> None:
        """予算チェックとコールバック通知"""
        if self.budget_limit is None:
            return
        
        result = self.check_budget()
        
        # ステータスが悪化した場合のみ通知
        if result.status != self._last_budget_status:
            if result.status == BudgetStatus.EXCEEDED and self._on_budget_exceeded:
                self._on_budget_exceeded(result)
            elif result.status in (BudgetStatus.WARNING, BudgetStatus.CRITICAL) and self._on_budget_warning:
                self._on_budget_warning(result)
            
            self._last_budget_status = result.status
    
    def get_total_cost(self) -> float:
        """総コストを取得する"""
        with self._lock:
            return sum(r.cost_usd for r in self._records)
    
    def get_total_tokens(self) -> TokenUsage:
        """総トークン使用量を取得する"""
        with self._lock:
            result = TokenUsage.zero()
            for r in self._records:
                result = result + r.usage
            return result
    
    def get_cost_by_session(self, session_id: str) -> float:
        """セッション別コストを取得する"""
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if r.session_id == session_id
            )
    
    def get_cost_by_agent(self, agent_name: str) -> float:
        """エージェント別コストを取得する"""
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if r.agent_name == agent_name
            )
    
    def get_cost_by_model(self, model: str) -> float:
        """モデル別コストを取得する"""
        model_normalized = self._normalize_model_name(model)
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if self._normalize_model_name(r.model) == model_normalized
            )
    
    def get_cost_by_provider(self, provider: str) -> float:
        """プロバイダ別コストを取得する"""
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if r.provider.lower() == provider.lower()
            )
    
    def get_cost_by_period(
        self,
        start: datetime,
        end: Optional[datetime] = None,
    ) -> float:
        """期間別コストを取得する"""
        end = end or datetime.now(timezone.utc)
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if start <= r.timestamp <= end
            )
    
    def get_records(
        self,
        session_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        model: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[CostRecord]:
        """
        条件に一致するレコードを取得する。
        
        Args:
            session_id: セッションIDでフィルタ
            agent_name: エージェント名でフィルタ
            model: モデル名でフィルタ
            start: 開始日時でフィルタ
            end: 終了日時でフィルタ
            limit: 最大件数
        
        Returns:
            条件に一致する CostRecord のリスト
        """
        with self._lock:
            records = self._records.copy()
        
        # フィルタリング
        if session_id is not None:
            records = [r for r in records if r.session_id == session_id]
        if agent_name is not None:
            records = [r for r in records if r.agent_name == agent_name]
        if model is not None:
            model_normalized = self._normalize_model_name(model)
            records = [r for r in records if self._normalize_model_name(r.model) == model_normalized]
        if start is not None:
            records = [r for r in records if r.timestamp >= start]
        if end is not None:
            records = [r for r in records if r.timestamp <= end]
        
        # ソート（新しい順）
        records.sort(key=lambda r: r.timestamp, reverse=True)
        
        # 件数制限
        if limit is not None:
            records = records[:limit]
        
        return records
    
    def check_budget(self) -> BudgetCheckResult:
        """
        予算をチェックする。
        
        Returns:
            BudgetCheckResult
        """
        current_cost = self.get_total_cost()
        
        if self.budget_limit is None:
            return BudgetCheckResult(
                status=BudgetStatus.OK,
                current_cost=current_cost,
                budget_limit=None,
                remaining=None,
                usage_percentage=None,
                message="No budget limit set",
            )
        
        remaining = self.budget_limit - current_cost
        usage_pct = (current_cost / self.budget_limit) * 100
        
        if current_cost >= self.budget_limit:
            status = BudgetStatus.EXCEEDED
            message = f"Budget exceeded! ${current_cost:.4f} / ${self.budget_limit:.2f} ({usage_pct:.1f}%)"
        elif current_cost >= self.budget_limit * self.critical_threshold:
            status = BudgetStatus.CRITICAL
            message = f"Critical: ${current_cost:.4f} / ${self.budget_limit:.2f} ({usage_pct:.1f}%)"
        elif current_cost >= self.budget_limit * self.warning_threshold:
            status = BudgetStatus.WARNING
            message = f"Warning: ${current_cost:.4f} / ${self.budget_limit:.2f} ({usage_pct:.1f}%)"
        else:
            status = BudgetStatus.OK
            message = f"OK: ${current_cost:.4f} / ${self.budget_limit:.2f} ({usage_pct:.1f}%)"
        
        return BudgetCheckResult(
            status=status,
            current_cost=current_cost,
            budget_limit=self.budget_limit,
            remaining=remaining,
            usage_percentage=usage_pct,
            message=message,
        )
    
    def get_summary(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> CostSummary:
        """
        コストサマリーを取得する。
        
        Args:
            start: 開始日時（オプション）
            end: 終了日時（オプション）
        
        Returns:
            CostSummary
        """
        records = self.get_records(start=start, end=end)
        
        if not records:
            return CostSummary(
                total_cost=0.0,
                total_input_tokens=0,
                total_output_tokens=0,
                total_tokens=0,
                record_count=0,
                period_start=start,
                period_end=end,
            )
        
        total_cost = sum(r.cost_usd for r in records)
        total_input = sum(r.usage.input_tokens for r in records)
        total_output = sum(r.usage.output_tokens for r in records)
        
        # モデル別集計
        by_model: Dict[str, float] = {}
        for r in records:
            key = r.model
            by_model[key] = by_model.get(key, 0) + r.cost_usd
        
        # エージェント別集計
        by_agent: Dict[str, float] = {}
        for r in records:
            if r.agent_name:
                by_agent[r.agent_name] = by_agent.get(r.agent_name, 0) + r.cost_usd
        
        # セッション別集計
        by_session: Dict[str, float] = {}
        for r in records:
            if r.session_id:
                by_session[r.session_id] = by_session.get(r.session_id, 0) + r.cost_usd
        
        return CostSummary(
            total_cost=total_cost,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_input + total_output,
            record_count=len(records),
            period_start=start or min(r.timestamp for r in records),
            period_end=end or max(r.timestamp for r in records),
            breakdown_by_model=by_model,
            breakdown_by_agent=by_agent,
            breakdown_by_session=by_session,
        )
    
    def generate_report(
        self,
        format: str = "text",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> str:
        """
        レポートを生成する。
        
        Args:
            format: 出力形式（"text", "json", "markdown"）
            start: 開始日時（オプション）
            end: 終了日時（オプション）
        
        Returns:
            レポート文字列
        """
        summary = self.get_summary(start=start, end=end)
        budget_status = self.check_budget()
        
        if format == "json":
            return self._generate_json_report(summary, budget_status)
        elif format == "markdown":
            return self._generate_markdown_report(summary, budget_status)
        else:
            return self._generate_text_report(summary, budget_status)
    
    def _generate_text_report(
        self,
        summary: CostSummary,
        budget_status: BudgetCheckResult,
    ) -> str:
        """テキスト形式のレポート"""
        lines = [
            "=" * 60,
            "LLM Cost Report",
            "=" * 60,
            "",
            f"Total Cost:         ${summary.total_cost:.6f}",
            f"Total Tokens:       {summary.total_tokens:,}",
            f"  - Input:          {summary.total_input_tokens:,}",
            f"  - Output:         {summary.total_output_tokens:,}",
            f"Record Count:       {summary.record_count}",
            "",
        ]
        
        if summary.period_start and summary.period_end:
            lines.extend([
                f"Period:             {summary.period_start.strftime('%Y-%m-%d %H:%M')} - "
                f"{summary.period_end.strftime('%Y-%m-%d %H:%M')}",
                "",
            ])
        
        # 予算ステータス
        lines.extend([
            "-" * 60,
            "Budget Status",
            "-" * 60,
            f"Status:             {budget_status.status.value.upper()}",
            f"Current:            ${budget_status.current_cost:.6f}",
        ])
        
        if budget_status.budget_limit is not None:
            lines.extend([
                f"Limit:              ${budget_status.budget_limit:.2f}",
                f"Remaining:          ${budget_status.remaining:.6f}",
                f"Usage:              {budget_status.usage_percentage:.1f}%",
            ])
        
        lines.append("")
        
        # モデル別内訳
        if summary.breakdown_by_model:
            lines.extend([
                "-" * 60,
                "Cost by Model",
                "-" * 60,
            ])
            for model, cost in sorted(summary.breakdown_by_model.items(), key=lambda x: -x[1]):
                pct = (cost / summary.total_cost * 100) if summary.total_cost > 0 else 0
                lines.append(f"  {model:30} ${cost:.6f} ({pct:.1f}%)")
            lines.append("")
        
        # エージェント別内訳
        if summary.breakdown_by_agent:
            lines.extend([
                "-" * 60,
                "Cost by Agent",
                "-" * 60,
            ])
            for agent, cost in sorted(summary.breakdown_by_agent.items(), key=lambda x: -x[1]):
                pct = (cost / summary.total_cost * 100) if summary.total_cost > 0 else 0
                lines.append(f"  {agent:30} ${cost:.6f} ({pct:.1f}%)")
            lines.append("")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def _generate_markdown_report(
        self,
        summary: CostSummary,
        budget_status: BudgetCheckResult,
    ) -> str:
        """Markdown 形式のレポート"""
        lines = [
            "# LLM Cost Report",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total Cost | ${summary.total_cost:.6f} |",
            f"| Total Tokens | {summary.total_tokens:,} |",
            f"| Input Tokens | {summary.total_input_tokens:,} |",
            f"| Output Tokens | {summary.total_output_tokens:,} |",
            f"| Record Count | {summary.record_count} |",
            "",
        ]
        
        # 予算ステータス
        status_emoji = {
            BudgetStatus.OK: "✅",
            BudgetStatus.WARNING: "⚠️",
            BudgetStatus.CRITICAL: "🔴",
            BudgetStatus.EXCEEDED: "🚨",
        }
        
        lines.extend([
            "## Budget Status",
            "",
            f"**Status:** {status_emoji.get(budget_status.status, '')} {budget_status.status.value.upper()}",
            "",
        ])
        
        if budget_status.budget_limit is not None:
            lines.extend([
                "| Metric | Value |",
                "|--------|-------|",
                f"| Current | ${budget_status.current_cost:.6f} |",
                f"| Limit | ${budget_status.budget_limit:.2f} |",
                f"| Remaining | ${budget_status.remaining:.6f} |",
                f"| Usage | {budget_status.usage_percentage:.1f}% |",
                "",
            ])
        
        # モデル別内訳
        if summary.breakdown_by_model:
            lines.extend([
                "## Cost by Model",
                "",
                "| Model | Cost | Percentage |",
                "|-------|------|------------|",
            ])
            for model, cost in sorted(summary.breakdown_by_model.items(), key=lambda x: -x[1]):
                pct = (cost / summary.total_cost * 100) if summary.total_cost > 0 else 0
                lines.append(f"| {model} | ${cost:.6f} | {pct:.1f}% |")
            lines.append("")
        
        # エージェント別内訳
        if summary.breakdown_by_agent:
            lines.extend([
                "## Cost by Agent",
                "",
                "| Agent | Cost | Percentage |",
                "|-------|------|------------|",
            ])
            for agent, cost in sorted(summary.breakdown_by_agent.items(), key=lambda x: -x[1]):
                pct = (cost / summary.total_cost * 100) if summary.total_cost > 0 else 0
                lines.append(f"| {agent} | ${cost:.6f} | {pct:.1f}% |")
            lines.append("")
        
        return "\n".join(lines)
    
    def _generate_json_report(
        self,
        summary: CostSummary,
        budget_status: BudgetCheckResult,
    ) -> str:
        """JSON 形式のレポート"""
        data = {
            "summary": {
                "total_cost": summary.total_cost,
                "total_tokens": summary.total_tokens,
                "total_input_tokens": summary.total_input_tokens,
                "total_output_tokens": summary.total_output_tokens,
                "record_count": summary.record_count,
                "period_start": summary.period_start.isoformat() if summary.period_start else None,
                "period_end": summary.period_end.isoformat() if summary.period_end else None,
            },
            "budget": {
                "status": budget_status.status.value,
                "current_cost": budget_status.current_cost,
                "budget_limit": budget_status.budget_limit,
                "remaining": budget_status.remaining,
                "usage_percentage": budget_status.usage_percentage,
            },
            "breakdown": {
                "by_model": summary.breakdown_by_model,
                "by_agent": summary.breakdown_by_agent,
                "by_session": summary.breakdown_by_session,
            },
        }
        return json.dumps(data, indent=2, ensure_ascii=False)
    
    def clear(self) -> int:
        """
        全レコードをクリアする。
        
        Returns:
            削除されたレコード数
        """
        with self._lock:
            count = len(self._records)
            self._records.clear()
            self._last_budget_status = None
        
        logger.info(f"Cost tracker cleared: {count} records removed")
        return count
    
    def export_records(self, filepath: str) -> int:
        """
        レコードをJSONファイルにエクスポートする。
        
        Args:
            filepath: 出力ファイルパス
        
        Returns:
            エクスポートされたレコード数
        """
        with self._lock:
            records = [r.to_dict() for r in self._records]
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Exported {len(records)} records to {filepath}")
        return len(records)
    
    def import_records(self, filepath: str) -> int:
        """
        JSONファイルからレコードをインポートする。
        
        Args:
            filepath: 入力ファイルパス
        
        Returns:
            インポートされたレコード数
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        records = [CostRecord.from_dict(d) for d in data]
        
        with self._lock:
            self._records.extend(records)
        
        logger.info(f"Imported {len(records)} records from {filepath}")
        return len(records)
    
    def update_pricing(self, model: str, input_price: float, output_price: float) -> None:
        """
        料金表を更新する。
        
        Args:
            model: モデル名
            input_price: 入力トークン料金（USD per 1M tokens）
            output_price: 出力トークン料金（USD per 1M tokens）
        """
        self._pricing[model.lower()] = {
            "input": input_price,
            "output": output_price,
        }
        logger.debug(f"Pricing updated: {model} = input=${input_price}, output=${output_price}")
    
    def get_pricing(self, model: str) -> Dict[str, float]:
        """
        モデルの料金を取得する。
        
        Args:
            model: モデル名
        
        Returns:
            {"input": float, "output": float}
        """
        model_key = self._normalize_model_name(model)
        return self._pricing.get(model_key, self._pricing.get("_default")).copy()


# =============================================================================
# グローバルインスタンス
# =============================================================================

_global_tracker: Optional[CostTracker] = None
_global_lock = threading.Lock()


def get_cost_tracker() -> CostTracker:
    """グローバルな CostTracker インスタンスを取得する"""
    global _global_tracker
    with _global_lock:
        if _global_tracker is None:
            _global_tracker = CostTracker()
        return _global_tracker


def reset_cost_tracker() -> None:
    """グローバルな CostTracker をリセットする"""
    global _global_tracker
    with _global_lock:
        _global_tracker = None


def set_cost_tracker(tracker: CostTracker) -> None:
    """グローバルな CostTracker を設定する"""
    global _global_tracker
    with _global_lock:
        _global_tracker = tracker


# =============================================================================
# デコレータとコンテキストマネージャ
# =============================================================================

@contextmanager
def track_cost(
    provider: str,
    model: str,
    session_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    tracker: Optional[CostTracker] = None,
) -> Generator[Dict[str, Any], None, None]:
    """
    コスト追跡のコンテキストマネージャ。
    
    使用例:
        with track_cost("gemini", "gemini-2.0-flash") as ctx:
            response = client.generate(...)
            ctx["input_tokens"] = response.usage.input_tokens
            ctx["output_tokens"] = response.usage.output_tokens
    
    Args:
        provider: プロバイダ名
        model: モデル名
        session_id: セッションID
        agent_name: エージェント名
        tracker: CostTracker（省略時はグローバルインスタンス）
    
    Yields:
        トークン情報を設定するための辞書
    """
    tracker = tracker or get_cost_tracker()
    ctx: Dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "metadata": {},
    }
    
    try:
        yield ctx
    finally:
        usage = TokenUsage(
            input_tokens=ctx.get("input_tokens", 0),
            output_tokens=ctx.get("output_tokens", 0),
        )
        
        if usage.total_tokens > 0:
            tracker.record(
                provider=provider,
                model=model,
                usage=usage,
                session_id=session_id,
                agent_name=agent_name,
                metadata=ctx.get("metadata"),
            )


def cost_tracked(
    provider: str,
    model: str,
    session_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    extract_usage: Optional[Callable[[Any], TokenUsage]] = None,
):
    """
    コスト追跡デコレータ。
    
    使用例:
        @cost_tracked("gemini", "gemini-2.0-flash", extract_usage=lambda r: TokenUsage(r.input, r.output))
        def generate(prompt: str) -> Response:
            ...
    
    Args:
        provider: プロバイダ名
        model: モデル名
        session_id: セッションID
        agent_name: エージェント名
        extract_usage: レスポンスからTokenUsageを抽出する関数
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            
            if extract_usage:
                try:
                    usage = extract_usage(result)
                    tracker = get_cost_tracker()
                    tracker.record(
                        provider=provider,
                        model=model,
                        usage=usage,
                        session_id=session_id,
                        agent_name=agent_name,
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract usage from response: {e}")
            
            return result
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            
            if extract_usage:
                try:
                    usage = extract_usage(result)
                    tracker = get_cost_tracker()
                    tracker.record(
                        provider=provider,
                        model=model,
                        usage=usage,
                        session_id=session_id,
                        agent_name=agent_name,
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract usage from response: {e}")
            
            return result
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    
    return decorator


# =============================================================================
# Agent/Runtime 統合用ヘルパー
# =============================================================================

def extract_gemini_usage(response: Any) -> TokenUsage:
    """Gemini レスポンスから TokenUsage を抽出する"""
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata:
        return TokenUsage(
            input_tokens=getattr(usage_metadata, "prompt_token_count", 0),
            output_tokens=getattr(usage_metadata, "candidates_token_count", 0),
        )
    return TokenUsage.zero()


def extract_openai_usage(response: Any) -> TokenUsage:
    """OpenAI レスポンスから TokenUsage を抽出する"""
    usage = getattr(response, "usage", None)
    if usage:
        return TokenUsage(
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
        )
    return TokenUsage.zero()


def extract_anthropic_usage(response: Any) -> TokenUsage:
    """Anthropic レスポンスから TokenUsage を抽出する"""
    usage = getattr(response, "usage", None)
    if usage:
        return TokenUsage(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
        )
    return TokenUsage.zero()


class CostTrackerMiddleware:
    """
    AgentRuntime 用のコスト追跡ミドルウェア。
    
    使用例:
        runtime = AgentRuntime(config)
        middleware = CostTrackerMiddleware(tracker)
        runtime.add_middleware(middleware)
    """
    
    def __init__(
        self,
        tracker: Optional[CostTracker] = None,
        raise_on_budget_exceeded: bool = False,
    ):
        """
        Args:
            tracker: CostTracker（省略時はグローバルインスタンス）
            raise_on_budget_exceeded: 予算超過時に例外を発生させるか
        """
        self.tracker = tracker or get_cost_tracker()
        self.raise_on_budget_exceeded = raise_on_budget_exceeded
    
    def before_call(
        self,
        provider: str,
        model: str,
        session_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        """API呼び出し前のチェック"""
        if self.raise_on_budget_exceeded:
            status = self.tracker.check_budget()
            if status.status == BudgetStatus.EXCEEDED:
                raise BudgetExceededError(
                    f"Budget exceeded: ${status.current_cost:.4f} / ${status.budget_limit:.2f}"
                )
    
    def after_call(
        self,
        provider: str,
        model: str,
        response: Any,
        session_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> CostRecord:
        """API呼び出し後の記録"""
        # プロバイダに応じて使用量を抽出
        provider_lower = provider.lower()
        if "gemini" in provider_lower or "google" in provider_lower:
            usage = extract_gemini_usage(response)
        elif "openai" in provider_lower or "gpt" in provider_lower:
            usage = extract_openai_usage(response)
        elif "anthropic" in provider_lower or "claude" in provider_lower:
            usage = extract_anthropic_usage(response)
        else:
            # 汎用的な抽出を試みる
            usage = self._try_extract_usage(response)
        
        return self.tracker.record(
            provider=provider,
            model=model,
            usage=usage,
            session_id=session_id,
            agent_name=agent_name,
        )
    
    def _try_extract_usage(self, response: Any) -> TokenUsage:
        """汎用的な使用量抽出"""
        # usage 属性を探す
        usage = getattr(response, "usage", None)
        if usage:
            return TokenUsage(
                input_tokens=getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0),
            )
        
        # usage_metadata を探す
        usage_metadata = getattr(response, "usage_metadata", None)
        if usage_metadata:
            return TokenUsage(
                input_tokens=getattr(usage_metadata, "prompt_token_count", 0),
                output_tokens=getattr(usage_metadata, "candidates_token_count", 0),
            )
        
        return TokenUsage.zero()


class BudgetExceededError(Exception):
    """予算超過エラー"""
    pass


# =============================================================================
# 便利関数
# =============================================================================

def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    tracker: Optional[CostTracker] = None,
) -> float:
    """
    コストを見積もる（記録せずに計算のみ）。
    
    Args:
        model: モデル名
        input_tokens: 入力トークン数
        output_tokens: 出力トークン数
        tracker: CostTracker（料金表の参照用）
    
    Returns:
        見積もりコスト（USD）
    """
    tracker = tracker or get_cost_tracker()
    usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    return tracker._calculate_cost(model, usage)


def format_cost(cost: float, currency: str = "USD") -> str:
    """
    コストをフォーマットする。
    
    Args:
        cost: コスト
        currency: 通貨
    
    Returns:
        フォーマットされた文字列
    """
    if currency == "USD":
        if cost < 0.01:
            return f"${cost:.6f}"
        elif cost < 1:
            return f"${cost:.4f}"
        else:
            return f"${cost:.2f}"
    elif currency == "JPY":
        # 1 USD = 150 JPY として概算
        jpy = cost * 150
        return f"¥{jpy:.0f}"
    else:
        return f"{cost:.6f} {currency}"
