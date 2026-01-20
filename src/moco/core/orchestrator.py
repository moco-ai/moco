import os
import re
import json
import asyncio
import time
import logging
from pathlib import Path
from typing import Dict, Optional, List, Callable, Any
from ..tools.discovery import AgentLoader, AgentConfig, discover_tools
from ..tools.skill_loader import SkillLoader, SkillConfig
from ..tools.skill_tools import get_loaded_skills, clear_session_skills
from ..cancellation import check_cancelled, clear_cancel_event, OperationCancelled
from .runtime import AgentRuntime, LLMProvider
from ..storage.session_logger import SessionLogger
from ..storage.semantic_memory import SemanticMemory

# Optimizer components
from .optimizer import (
    TaskAnalyzer,
    AgentSelector,
    QualityTracker,
    QualityEvaluator,
    OptimizerConfig,
    ExecutionMetrics,
    SelectionResult
)
from ..utils.json_parser import SmartJSONParser

try:
    from rich.console import Console
    from rich.spinner import Spinner
    from rich.live import Live
    _console = Console()

    def _log_delegation(agent_name: str, task_summary: str = ""):
        # タスクの概要を短く切り詰め
        summary = task_summary[:60].replace('\n', ' ').strip()
        if len(task_summary) > 60:
            summary += "..."
        _console.print(f"  [dim]→[/dim] [cyan]@{agent_name}[/cyan] [dim]{summary}[/dim]")
except ImportError:
    _console = None
    def _log_delegation(agent_name: str, task_summary: str = ""):
        summary = task_summary[:60].replace('\n', ' ').strip()
        if len(task_summary) > 60:
            summary += "..."
        print(f"  → @{agent_name} {summary}")


def moco_log(message: str, verbose: bool = False):
    """
    詳細ログを出力。
    UIが利用可能ならUIの詳細ログウィンドウに表示、
    そうでなければコンソールにdim表示。
    """
    if not verbose:
        return
    
    clean_msg = str(message).strip().replace('\n', ' ')
    if len(clean_msg) > 100:
        clean_msg = clean_msg[:97] + "..."

    try:
        from ..ui.layout import ui_state
        ui_state.add_verbose_log(clean_msg)
    except (ImportError, Exception):
        # UI未使用時はコンソール出力
        if _console:
            _console.print(f"[dim]{clean_msg}[/dim]")
        else:
            print(clean_msg)


class Orchestrator:
    """
    メインオーケストレーター。
    ユーザー入力を適切なエージェントにルーティングし、
    会話履歴を管理する。

    各サブエージェントは独立したセッション履歴を持つ。
    """

    def __init__(
        self,
        profile: str = 'default',
        session_logger: Optional[SessionLogger] = None,
        provider: str = None,
        model: str = None,
        stream: bool = True,
        verbose: bool = False,
        progress_callback: Optional[callable] = None,
        use_optimizer: bool = False,
        working_directory: Optional[str] = None
    ):
        self.profile = profile
        self.provider = provider  # None = 環境変数から決定
        self.model = model        # None = プロバイダーのデフォルト
        self.stream = stream      # ストリーミング出力
        self.verbose = verbose    # 詳細ログ出力
        self.progress_callback = progress_callback  # 進捗コールバック
        self.use_optimizer = use_optimizer  # Optimizer使用フラグ
        self.working_directory = working_directory or os.getcwd()  # 作業ディレクトリ
        self.name = "orchestrator"
        self.loader = AgentLoader(profile=self.profile)
        self.skill_loader = SkillLoader(profile=self.profile)
        self._all_skills: Dict[str, SkillConfig] = {}  # ローカルスキルキャッシュ
        self._session_skills: List[SkillConfig] = []   # セッション中にロードしたスキル（プール）
        
        # Optimizer コンポーネント
        self.optimizer_config = OptimizerConfig(profile=profile)
        self.task_analyzer = TaskAnalyzer(
            llm_generate_fn=self._llm_generate_for_optimizer
        )
        self.agent_selector = AgentSelector(self.optimizer_config)
        self.quality_tracker = QualityTracker()
        self.quality_evaluator = QualityEvaluator(
            llm_generate_fn=self._llm_generate_for_optimizer
        )
        
        # 実行メトリクス用
        self._current_execution_start: Optional[float] = None
        self._current_scores: Optional[dict] = None
        self._current_selection: Optional[SelectionResult] = None
        self._inline_evaluations: Dict[str, dict] = {}  # エージェント名 -> 評価結果
        self._agent_execution_metrics: List[Any] = []   # エージェント別実行メトリクス

        # セマンティックメモリの初期化
        db_path = os.getenv("SEMANTIC_DB_PATH", str(Path.cwd() / "data" / "semantic.db"))
        self.semantic_memory = SemanticMemory(db_path=db_path)

        # ツールを動的に読み込む
        self.tool_map = discover_tools(profile=self.profile)

        self.agents: Dict[str, AgentConfig] = {}
        self.runtimes: Dict[str, AgentRuntime] = {}

        # 会話履歴管理
        self.session_logger = session_logger or SessionLogger()
        self._current_session_id: Optional[str] = None

        # サブエージェントのセッションIDをキャッシュ
        # {parent_session_id: {agent_name: sub_session_id}}
        self._sub_sessions: Dict[str, Dict[str, str]] = {}

        self.reload_agents()

    def reload_agents(self):
        """エージェント定義を再読み込みする"""
        self.agents = self.loader.load_agents()
        self.runtimes = {}
        
        # Skills をロード
        self._all_skills = self.skill_loader.load_skills()
        if self.verbose and self._all_skills:
            print(f"[Skills] Loaded {len(self._all_skills)} skills: {list(self._all_skills.keys())}")

        # 利用可能なエージェント一覧（orchestrator 以外）
        available_agents = [name for name in self.agents.keys() if name != "orchestrator"]

        for name, config in self.agents.items():
            if self.verbose:
                print(f"Loaded agent: {name}")

            # delegate_to_agent ツールが必要かどうか判定
            needs_delegate = name == "orchestrator" or "delegate_to_agent" in config.tools
            
            if needs_delegate:
                # delegate_to_agent ツールを追加
                agent_tools = dict(self.tool_map)
                agent_tools["delegate_to_agent"] = self._create_delegate_tool(available_agents)
                self.runtimes[name] = AgentRuntime(
                    config,
                    agent_tools,
                    agent_name=name,
                    name=name,
                    provider=self.provider,
                    model=self.model,
                    stream=self.stream,
                    verbose=self.verbose,
                    progress_callback=self.progress_callback,
                    parent_agent=None if name == "orchestrator" else "orchestrator",
                    semantic_memory=self.semantic_memory
                )
            else:
                self.runtimes[name] = AgentRuntime(
                    config,
                    self.tool_map,
                    agent_name=name,
                    name=name,
                    provider=self.provider,
                    model=self.model,
                    stream=self.stream,
                    verbose=self.verbose,
                    progress_callback=self.progress_callback,
                    parent_agent="orchestrator",  # サブエージェントの親はorchestrator
                    semantic_memory=self.semantic_memory
                )

    def _create_delegate_tool(self, available_agents: list):
        """delegate_to_agent ツールを動的に作成"""
        orchestrator = self

        def delegate_to_agent(agent_name: str, task: str) -> str:
            """
            サブエージェントにタスクを委譲します。

            Args:
                agent_name: 委譲先エージェント名（例: backend-coder, code-reviewer）
                task: タスクの詳細な説明

            Returns:
                エージェントの実行結果
            """
            if agent_name not in available_agents:
                return f"Error: Unknown agent '{agent_name}'. Available: {', '.join(available_agents)}"

            _log_delegation(agent_name, task)

            # 共通の委譲ロジックを使用（履歴管理やログ記録を含む）
            return orchestrator._delegate_to_agent(
                agent_name, 
                task, 
                orchestrator._current_session_id
            )

        return delegate_to_agent

    def _get_or_create_sub_session(
        self,
        parent_session_id: str,
        agent_name: str
    ) -> str:
        """
        サブエージェント用のセッションを取得または作成する。
        同じ親セッション内では同じサブセッションを再利用する。
        """
        if parent_session_id not in self._sub_sessions:
            self._sub_sessions[parent_session_id] = {}

        if agent_name not in self._sub_sessions[parent_session_id]:
            # 新しいサブセッションを作成
            sub_session_id = self.session_logger.create_session(
                title=f"Sub: @{agent_name}",
                parent_session_id=parent_session_id,
                agent_name=agent_name
            )
            self._sub_sessions[parent_session_id][agent_name] = sub_session_id

        return self._sub_sessions[parent_session_id][agent_name]

    def _prepare_session(self, user_input: str, session_id: Optional[str] = None) -> tuple[str, List[Any]]:
        """セッションの準備と履歴の取得を行う"""
        if not session_id:
            session_id = self.create_session(title=user_input[:50])

        # 履歴を取得（プロバイダに応じたフォーマット）
        history_format = "openai" if self.provider in (LLMProvider.OPENAI, LLMProvider.OPENROUTER) else "gemini"
        history = self.session_logger.get_agent_history(session_id, format=history_format)
        
        return session_id, history

    async def process_message(
        self,
        user_input: str,
        session_id: Optional[str] = None,
        history: Optional[List[Any]] = None
    ) -> str:
        """
        ユーザー入力を処理し、適切なエージェントにルーティングする

        Args:
            user_input: ユーザーからの入力
            session_id: セッションID（履歴管理用）
            history: 事前に取得した履歴（省略時は自動取得）
        """
        # 実行開始時刻を記録
        self._current_execution_start = time.time()
        
        # インライン評価とエージェントメトリクスをリセット
        self._inline_evaluations = {}
        self._agent_execution_metrics = []
        
        # スキルプールをリセット（新しいタスクごとにクリア）
        self._session_skills = []
        clear_session_skills()  # skill_tools のキャッシュもクリア
        
        # セッションIDを保持
        self._current_session_id = session_id

        # キャンセルチェック
        if session_id:
            check_cancelled(session_id)

        # TodoツールにセッションIDを設定
        if session_id:
            from ..tools.todo import set_current_session as set_todo_session
            from ..tools.stats import set_current_session as set_stats_session
            set_todo_session(session_id)
            set_stats_session(session_id)
        
        # Optimizer: タスク分析（use_optimizer=True の場合のみ）
        if self.use_optimizer:
            self._current_scores = await self.task_analyzer.analyze(user_input)
            available_agents = [name for name in self.agents.keys()]
            self._current_selection = self.agent_selector.select(
                self._current_scores, 
                available_agents
            )
            
            moco_log(f"[Optimizer] Scores: {self._current_scores}", self.verbose)
            moco_log(f"[Optimizer] Selection: {self._current_selection.depth} -> {self._current_selection.agents}", self.verbose)
        else:
            # Optimizer無効時は全エージェント使用
            self._current_scores = None
            self._current_selection = SelectionResult(
                depth="full",
                agents=[name for name in self.agents.keys() if name != "orchestrator"],
                reason="Optimizer disabled"
            )

        # Orchestratorの履歴を取得（渡されていない場合）
        if history is None and session_id:
            # プロバイダに応じたフォーマットで履歴を取得
            history_format = "openai" if self.provider in (LLMProvider.OPENAI, LLMProvider.OPENROUTER) else "gemini"
            history = self.session_logger.get_agent_history(
                session_id,
                limit=20,
                format=history_format
            )

        # ユーザーメッセージを記録（Orchestratorのセッションに）
        if session_id:
            self.session_logger.log_agent_message(session_id, "user", user_input)

        # @agent-name の検出（ハイフン含む名前に対応）
        match = re.match(r"^@([\w-]+)[:\s]*(.*)", user_input, re.DOTALL)
        
        response = ""
        current_agent = "orchestrator"
        try:
            if match:
                agent_name = match.group(1)
                current_agent = agent_name
                query = match.group(2).lstrip(': ').strip()

                if agent_name in self.runtimes:
                    # キャンセルチェック
                    if session_id:
                        check_cancelled(session_id)
                    # サブエージェントは独立した履歴を使用
                    response = await self._delegate_to_agent(agent_name, query, session_id)
                    # 実行後のキャンセルチェック
                    if session_id:
                        check_cancelled(session_id)
                else:
                    response = f"Error: Agent '@{agent_name}' not found. Available agents: {list(self.agents.keys())}"
            else:
                # デフォルトの挙動：orchestratorエージェントに任せる
                if "orchestrator" in self.runtimes:
                    # キャンセルチェック
                    if session_id:
                        check_cancelled(session_id)
                    # orchestratorエージェントにはOrchestratorの履歴を渡す
                    response = await self._delegate_to_orchestrator_agent(user_input, session_id, history)
                    # 実行後のキャンセルチェック
                    if session_id:
                        check_cancelled(session_id)
                else:
                    response = self._list_agents()
        except Exception as e:
            # エラー発生時、部分応答を保存
            if session_id and current_agent in self.runtimes:
                partial = self.runtimes[current_agent]._partial_response
                if partial:
                    self.session_logger.log_agent_message(
                        session_id, 
                        "assistant", 
                        f"[エラーで中断: {type(e).__name__}]\n{partial}"
                    )
            raise

        # アシスタントの応答を記録（Orchestratorのセッションに）
        if session_id:
            self.session_logger.log_agent_message(session_id, "assistant", response)
        
        # Optimizer: メトリクス記録
        await self._record_execution_metrics(session_id, user_input, response)

        return response
    
    async def _record_execution_metrics(
        self,
        session_id: Optional[str],
        user_input: str,
        response: str
    ) -> None:
        """実行メトリクスを記録"""
        if not self._current_scores or not self._current_selection:
            return
        
        try:
            duration = time.time() - (self._current_execution_start or time.time())
            
            # 実際のトークン数を取得
            total_tokens = 0
            # Orchestrator自身のランタイムから取得
            if "orchestrator" in self.runtimes:
                metrics = self.runtimes["orchestrator"].get_metrics()
                total_tokens += metrics.get("total_tokens", 0)

            # 委譲先エージェントの分も合算（もしあれば）
            for name in self._current_selection.agents:
                if name in self.runtimes:
                    metrics = self.runtimes[name].get_metrics()
                    total_tokens += metrics.get("total_tokens", 0)

            # 品質評価：インライン評価優先、なければLLM評価
            aggregated_eval = self._aggregate_inline_evaluations()
            if aggregated_eval:
                # インライン評価を使用（追加LLMコールなし）
                quality_scores = {
                    "completion": int(aggregated_eval.get("completion", 5)),
                    "quality": int(aggregated_eval.get("quality", 5)),
                    "task_complexity": int(aggregated_eval.get("task_complexity", 5)),
                    "prompt_specificity": int(aggregated_eval.get("prompt_specificity", 5)),
                }
                if self.verbose:
                    print(f"[Optimizer] Using inline evaluation from {len(self._inline_evaluations)} agents")
            else:
                # フォールバック：QualityEvaluatorを使用
                quality_scores = await self.quality_evaluator.evaluate(
                    task=user_input,
                    result=response,
                    profile=self.profile
                )
            ai_score = (quality_scores.get("completion", 5) + quality_scores.get("quality", 5)) / 20.0

            # 実行中に計測した指標
            delegation_count = len(self._current_selection.agents)
            todo_used = 1 if "todowrite" in response.lower() or "todo" in response.lower() else 0
            input_length = len(user_input)
            output_length = len(response)
            
            # 履歴・要約関連の指標
            history_turns = 0
            summary_depth = 0
            if session_id and self.session_logger:
                # 履歴のターン数を取得
                history = self.session_logger.get_agent_history(session_id, limit=100)
                history_turns = len(history) if history else 0
                # 要約の深さを取得
                summary_depth = self.session_logger.get_summary_depth(session_id)

            execution = ExecutionMetrics(
                tokens=total_tokens,
                duration=duration,
                tool_calls=response.count("@"),  # 委譲回数の概算
                errors=1 if "Error" in response else 0,
                retries=0,
                has_apology="申し訳" in response or "sorry" in response.lower()
            )
            
            record_id = self.quality_tracker.record(
                profile=self.profile,
                session_id=session_id or "unknown",
                task_summary=user_input[:100],
                scores=self._current_scores,
                selection=self._current_selection,
                execution=execution,
                thresholds=self.optimizer_config.get_thresholds(),
                ai_score=ai_score,
                task_complexity=quality_scores.get("task_complexity", 5),
                prompt_specificity=quality_scores.get("prompt_specificity", 5),
                todo_used=todo_used,
                delegation_count=delegation_count,
                input_length=input_length,
                output_length=output_length,
                history_turns=history_turns,
                summary_depth=summary_depth
            )
            
            # orchestrator 自身の実行メトリクスも agent_executions に記録する
            # （Insight Panel の Agent Stats に orchestrator を表示するため）
            try:
                from moco.core.optimizer import AgentExecutionMetrics

                # 既に入っている場合は重複を避ける
                if self._agent_execution_metrics:
                    self._agent_execution_metrics = [
                        m for m in self._agent_execution_metrics if getattr(m, "agent_name", None) != "orchestrator"
                    ]

                o_prompt_tokens = 0
                o_completion_tokens = 0
                try:
                    o_runtime = self.runtimes.get("orchestrator")
                    if o_runtime:
                        o_usage = o_runtime.get_metrics() or {}
                        o_prompt_tokens = int(o_usage.get("prompt_tokens", 0) or 0)
                        o_completion_tokens = int(o_usage.get("completion_tokens", 0) or 0)
                except Exception:
                    pass  # メトリクス取得失敗は無視

                orch_metric = AgentExecutionMetrics(
                    agent_name="orchestrator",
                    parent_agent=None,
                    tokens_input=o_prompt_tokens,
                    tokens_output=o_completion_tokens,
                    execution_time_ms=int(duration * 1000),
                    tool_calls=0,
                    inline_score=float(ai_score),
                    eval_completion=None,
                    eval_quality=None,
                    eval_task_complexity=None,
                    eval_prompt_specificity=None,
                    summary_depth=int(summary_depth or 0),
                    history_turns=int(history_turns or 0),
                    error_message=None,
                )

                if self._agent_execution_metrics is None:
                    self._agent_execution_metrics = []
                self._agent_execution_metrics.insert(0, orch_metric)
            except Exception:
                pass

            # エージェント別メトリクスを記録
            if self._agent_execution_metrics:
                self.quality_tracker.record_agent_executions(
                    request_id=record_id,
                    agents=self._agent_execution_metrics
                )
                if self.verbose:
                    print(f"[Optimizer] Recorded {len(self._agent_execution_metrics)} agent executions")
                    
        except Exception as e:
            if self.verbose:
                print(f"[Optimizer] Failed to record metrics: {e}")

    async def _delegate_to_orchestrator_agent(
        self,
        query: str,
        session_id: Optional[str],
        history: Optional[List[Any]]
    ) -> str:
        """orchestratorエージェントに委譲（Orchestratorの履歴を使用）

        Note: orchestrator は delegate_to_agent ツールを持っており、
        サブエージェントへの委譲はツール呼び出しで行われる。
        ツール結果を受け取った後、orchestrator が最終応答を生成する。
        """
        runtime = self.runtimes["orchestrator"]
        
        # 作業ディレクトリ情報を自動注入
        working_dir_prefix = self._get_working_context_prompt()
        query_with_workdir = working_dir_prefix + query

        # Optimizer ガイダンスを注入（use_optimizer=True かつ選択結果がある場合）
        if self.use_optimizer and self._current_selection:
            optimizer_guidance = f'''【Optimizer ガイダンス】
深度: {self._current_selection.depth}
推奨エージェント: {', '.join(self._current_selection.agents) if self._current_selection.agents else 'なし'}
スキップ推奨: {', '.join(self._current_selection.skipped) if self._current_selection.skipped else 'なし'}
理由: {self._current_selection.reason}

※ 上記は推奨です。タスクの性質上、追加のエージェントが必要な場合は判断して使用してください。

'''
            query_with_workdir = optimizer_guidance + query_with_workdir
            
            if self.verbose:
                print(f'[Optimizer] Injecting guidance: depth={self._current_selection.depth}, agents={self._current_selection.agents}')

        try:
            # AgentRuntime.run は async
            response = await runtime.run(query_with_workdir, history=history, session_id=session_id)
            # 応答内の @agent-name 指示を自動処理する
            response = await self._process_delegations_in_response(response, session_id)

            # サブエージェントの評価結果をレスポンスに追加
            if self._inline_evaluations:
                eval_summary = self._aggregate_inline_evaluations()
                eval_block = f"""
---
【サブエージェント評価】
- completion: {int(eval_summary.get('completion', 5))}
- quality: {int(eval_summary.get('quality', 5))}
- task_complexity: {int(eval_summary.get('task_complexity', 5))}
- prompt_specificity: {int(eval_summary.get('prompt_specificity', 5))}
"""
                response = response + eval_block

            return f"@orchestrator: {response}"
        except Exception as e:
            return f"Error running @orchestrator: {e}"

    async def _process_delegations_in_response(
        self,
        response: str,
        session_id: Optional[str]
    ) -> str:
        """
        オーケストレーターの応答に含まれる @agent-name パターンを検出し、
        該当するサブエージェントに処理を委譲する。

        委譲タスクは並列で実行されます。
        """
        # responseがNoneの場合は空文字列として扱う
        if response is None:
            return ""

        # 応答内の @agent-name パターンを検出
        # 例: "@doc-writer ファイルを作成してください..."
        lines = response.split('\n')
        
        # まず全ての委譲タスクを検出（第1パス）
        delegations = []  # [(agent_name, instruction, start_line_index, end_line_index)]
        i = 0

        while i < len(lines):
            # ループ内でのキャンセルチェック
            if session_id:
                check_cancelled(session_id)

            line = lines[i]
            # コロンやスペースに柔軟に対応
            match = re.match(r"^@([\w-]+)[:\s]*(.*)", line)

            if match:
                agent_name = match.group(1)

                if agent_name in self.runtimes and agent_name != "orchestrator":
                    # ループ内でのキャンセルチェック
                    if session_id:
                        check_cancelled(session_id)

                    # この行から始まる指示を収集（次の@まで、空行は含める）
                    # 先頭のコロンや空白を確実に除去
                    initial_instruction = match.group(2).lstrip(': ').strip()
                    instruction_lines = [initial_instruction] if initial_instruction else []
                    i += 1
                    consecutive_empty = 0
                    start_line_index = i - 1  # @agent-name 行のインデックス
                    while i < len(lines):
                        next_line = lines[i]
                        # 次の@エージェントパターンで終了
                        if re.match(r"^@[\w-]+", next_line):
                            break
                        # 連続した空行が2つ以上で終了（段落区切り）
                        if next_line.strip() == "":
                            consecutive_empty += 1
                            if consecutive_empty >= 2:
                                break
                        else:
                            consecutive_empty = 0
                        instruction_lines.append(next_line)
                        i += 1

                    instruction = '\n'.join(instruction_lines).strip()
                    if instruction:
                        delegations.append((agent_name, instruction, start_line_index, i))
                    continue

            i += 1

        # 委譲タスクがあれば並列実行
        delegation_results = {}  # {start_line_index: (sub_response, error)}
        delegated_count = len(delegations)

        if delegations:
            # 各委譲タスクのコルーチンを作成
            async def execute_delegation(agent_name: str, instruction: str, line_index: int):
                try:
                    # 委譲先は常に表示（ユーザーが処理の流れを把握できるように）
                    _log_delegation(agent_name, instruction)
                    sub_response = await self._delegate_to_agent(agent_name, instruction, session_id)
                    return (line_index, sub_response, None)
                except Exception as e:
                    # エラーが発生しても他のタスクは継続
                    error_msg = f"@{agent_name} の実行中にエラーが発生しました: {e}"
                    logging.error(error_msg, exc_info=True)
                    # エラー時は空の応答を返し、エラー情報は別途記録
                    return (line_index, "", error_msg)

            # 全ての委譲タスクを並列実行
            tasks = [
                execute_delegation(agent_name, instruction, start_idx)
                for agent_name, instruction, start_idx, end_idx in delegations
            ]
            results = await asyncio.gather(*tasks, return_exceptions=False)

            # 結果をインデックスでマッピング
            for line_idx, sub_response, error in results:
                delegation_results[line_idx] = (sub_response, error)

        # 第2パス: 結果を元の位置にマージして最終的な出力を構築
        processed_lines = []
        successful_results = []  # サマリー生成用（エラーを除く）
        i = 0
        delegation_idx = 0

        while i < len(lines):
            # ループ内でのキャンセルチェック
            if session_id:
                check_cancelled(session_id)

            # 委譲がある位置かチェック
            if delegation_idx < len(delegations):
                _, _, start_idx, end_idx = delegations[delegation_idx]
                if i == start_idx:
                    # この位置は委譲タスクの開始位置
                    sub_response, error = delegation_results[start_idx]
                    if error:
                        processed_lines.append(f"### エラー\n{error}")
                    else:
                        processed_lines.append(sub_response)
                        successful_results.append(sub_response)
                    i = end_idx
                    delegation_idx += 1
                    continue

            processed_lines.append(lines[i])
            i += 1

        # サブエージェントに委譲があった場合、最終まとめを生成
        if successful_results:
            summary = await self._generate_final_summary(successful_results, session_id)
            if summary:
                processed_lines.append(f"\n---\n## まとめ\n{summary}")

        return '\n'.join(processed_lines)

    async def _generate_final_summary(self, results: list, session_id: Optional[str] = None) -> str:
        """サブエージェントの結果から最終サマリーを LLM で生成"""
        # 結果を短く切り詰め
        combined = '\n'.join(results)
        if len(combined) > 2000:
            combined = combined[:2000] + "...(省略)"

        # Orchestrator に要要約を依頼
        runtime = self.runtimes.get("orchestrator")
        if not runtime:
            return "完了しました。"

        prompt = f"""以下のサブエージェントの実行結果を、ユーザー向けに3-5行で簡潔にまとめてください。
技術的詳細は省略し、「何が完了したか」「結果はどうだったか」を伝えてください。

---
{combined}
---

まとめ（3-5行）:"""

        try:
            summary = await runtime.run(prompt, history=None, session_id=session_id)
            # summaryがNoneの場合は空文字列として扱う
            if summary is None:
                return ""
            # 長すぎる場合は切り詰め
            lines = summary.strip().split('\n')
            if len(lines) > 7:
                summary = '\n'.join(lines[:7])
            return summary.strip()
        except Exception:
            return "タスクが完了しました。"

    async def _delegate_to_agent(
        self,
        agent_name: str,
        query: str,
        parent_session_id: Optional[str] = None
    ) -> str:
        """
        指定されたサブエージェントに処理を委譲する。
        サブエージェントは独立した履歴を持つ。
        """
        # 進捗通知
        if self.progress_callback:
            self.progress_callback(
                event_type="delegate",
                name=agent_name,
                detail=query,
                agent_name=agent_name,
                parent_agent=self.name,
                status="running"
            )

        runtime = self.runtimes[agent_name]

        try:
            # キャンセルチェック
            if parent_session_id:
                check_cancelled(parent_session_id)

            # サブエージェント用のセッションを取得/作成
            if parent_session_id:
                sub_session_id = self._get_or_create_sub_session(parent_session_id, agent_name)

                # サブエージェントの履歴を取得（プロバイダに応じたフォーマット）
                history_format = "openai" if self.provider in (LLMProvider.OPENAI, LLMProvider.OPENROUTER) else "gemini"
                sub_history = self.session_logger.get_agent_history(
                    sub_session_id,
                    limit=10,  # サブエージェントは短めの履歴
                    format=history_format
                )

                # サブエージェントのセッションにユーザー（Orchestrator）からのクエリを記録
                self.session_logger.log_agent_message(
                    sub_session_id,
                    "user",
                    query,
                    agent_id="orchestrator"
                )
            else:
                sub_history = None
                sub_session_id = None

            # サブエージェントを実行（独自の履歴で）
            # AgentRuntime.run は同期的なので、スレッドで実行
            
            # 作業ディレクトリ情報を自動注入
            working_dir_prefix = self._get_working_context_prompt()
            query_with_workdir = working_dir_prefix + query
            
            # 評価指示は追加しない（オーケストレーターが事後評価を行うため）
            enhanced_query = query_with_workdir
            
            # Skills 注入: Orchestrator がツールでロードしたスキルをサブエージェントに共有
            loaded_skills = get_loaded_skills()
            if loaded_skills:
                # タスクに関連するスキルを選択
                relevant_skills = []
                for name, skill in loaded_skills.items():
                    if skill.matches_input(query):
                        relevant_skills.append(skill)
                
                # マッチしなくても全スキルを渡す（Orchestrator が選んだもの）
                if not relevant_skills:
                    relevant_skills = list(loaded_skills.values())[:3]  # 最大3つ
                
                if relevant_skills:
                    runtime.skills = relevant_skills
                    if self.verbose:
                        print(f"[Skills] Injected {len(relevant_skills)} skills from pool to @{agent_name}: {[s.name for s in relevant_skills]}")
            
            # 実行時間計測開始
            agent_start_time = time.time()
            
            response = await runtime.run(enhanced_query, history=sub_history)
            
            # 実行時間計測終了
            agent_execution_time_ms = int((time.time() - agent_start_time) * 1000)
            
            
            # エージェント実行メトリクスを収集
            runtime_metrics = runtime.get_metrics()

            # オーケストレーターがサブエージェントのレスポンスを評価
            orchestrator_eval = self._evaluate_subagent_response(agent_name, query, response)
            eval_block = ""
            if orchestrator_eval:
                self._inline_evaluations[agent_name] = orchestrator_eval
                if self.verbose:
                    print(f"[Orchestrator] Saved evaluation for @{agent_name}: {orchestrator_eval}")
                
                eval_block = f"""
---
【サブエージェント評価】
- completion: {orchestrator_eval.get('completion', 0)}
- quality: {orchestrator_eval.get('quality', 0)}
- task_complexity: {orchestrator_eval.get('task_complexity', 0)}
- prompt_specificity: {orchestrator_eval.get('prompt_specificity', 0)}
"""
            
            # エージェント別の要約情報を取得（エラーでも処理を止めない）
            # メインセッション（parent_session_id）の summary_depth を使用
            # サブエージェントもメインコンテキストの一部として扱う
            agent_summary_depth = 0
            agent_history_turns = 0
            try:
                # メインセッションの要約深度を取得（サブセッションではなく）
                main_session = parent_session_id or sub_session_id
                if main_session and self.session_logger:
                    agent_summary_depth = self.session_logger.get_summary_depth(main_session)
                    agent_history = self.session_logger.get_agent_history(main_session, limit=100)
                    agent_history_turns = len(agent_history) if agent_history else 0
            except Exception:
                pass  # メトリクス取得失敗は無視
            
            from moco.core.optimizer import AgentExecutionMetrics
            inline_score = None
            if orchestrator_eval:
                completion = float(orchestrator_eval.get("completion", 0))
                quality = float(orchestrator_eval.get("quality", 0))
                inline_score = (completion + quality) / 20.0

            agent_metric = AgentExecutionMetrics(
                agent_name=agent_name,
                parent_agent=self.name,
                tokens_input=runtime_metrics.get("prompt_tokens", 0),
                tokens_output=runtime_metrics.get("candidates_tokens", runtime_metrics.get("completion_tokens", 0)),
                execution_time_ms=agent_execution_time_ms,
                tool_calls=response.count("🛠️") if response else 0,  # ツール呼び出しの推定
                inline_score=inline_score,  # オーケストレーター評価を反映
                # インライン評価の詳細項目
                eval_completion=int(orchestrator_eval.get("completion", 0)) if orchestrator_eval else None,
                eval_quality=int(orchestrator_eval.get("quality", 0)) if orchestrator_eval else None,
                eval_task_complexity=int(orchestrator_eval.get("task_complexity", 0)) if orchestrator_eval else None,
                eval_prompt_specificity=int(orchestrator_eval.get("prompt_specificity", 0)) if orchestrator_eval else None,
                # 要約・コンテキスト関連
                summary_depth=agent_summary_depth,
                history_turns=agent_history_turns,
                error_message=None
            )
            self._agent_execution_metrics.append(agent_metric)

            if self.progress_callback:
                self.progress_callback(
                    event_type="delegate",
                    name=agent_name,
                    detail=query,
                    agent_name=agent_name,
                    parent_agent=self.name,
                    status="completed"
                )

            # サブエージェントの応答を記録
            if sub_session_id:
                self.session_logger.log_agent_message(
                    sub_session_id,
                    "assistant",
                    response,
                    agent_id=agent_name
                )

            return f"@{agent_name}: {response}{eval_block}"

        except Exception as e:
            # エラー時もメトリクスを記録
            from moco.core.optimizer import AgentExecutionMetrics
            agent_metric = AgentExecutionMetrics(
                agent_name=agent_name,
                parent_agent=self.name,
                error_message=str(e)
            )
            self._agent_execution_metrics.append(agent_metric)
            return f"Error running agent @{agent_name}: {e}"

    def _list_agents(self) -> str:
        if not self.agents:
            return f"No agents loaded. Please check profiles/{self.profile}/agents/*.md files."

        lines = ["Available agents:"]
        for name, config in self.agents.items():
            lines.append(f"- @{name}: {config.description}")
        return "\n".join(lines)

    def _get_working_context_prompt(self) -> str:
        """エージェントに渡す作業ディレクトリのコンテキストプロンプトを生成する"""
        display_dir = Path(self.working_directory).name
        return (
            f"【作業コンテキスト】現在のワークスペース: ./{display_dir}\n"
            f"⛔ この作業ディレクトリの外に出ることは禁止。ファイル操作は必ずこのディレクトリ内で行うこと。\n\n"
        )

    def _evaluate_subagent_response(
        self,
        agent_name: str,
        task: str,
        response: str
    ) -> Optional[dict]:
        """サブエージェントのレスポンスをオーケストレーターが評価

        Args:
            agent_name: サブエージェント名
            task: 依頼したタスク
            response: サブエージェントからのレスポンス

        Returns:
            評価結果（辞書）、エラー時はNone
        """
        eval_prompt = f"""以下のサブエージェントのタスク実行結果を客観的に評価してください。

【エージェント名】{agent_name}

【依頼タスク】
{task}

【実行結果】
{response[:2000]}

---

評価基準（0-10の整数で採点）:

1. completion (0-10):
   - 9-10: エラーなくタスクが完遂し、目的が完全に達成された
   - 4-8: タスクの大部分が達成されたが、一部不完全または改善の余地あり
   - 1-3: ツール実行エラーが発生した、または目的が未達成
   - 0: 途中で断念した、または解決策が見つからなかった

2. quality (0-10):
   - 高得点: コードの正確性、効率性、安全性が高い。説明が明確で十分
   - 低得点: エラーログが含まれている、または説明が不十分、コードに問題がある

3. task_complexity (1-10): タスクの複雑さ

4. prompt_specificity (0-10): 依頼の指示の具体性（タスクの難しさではなく、指示がどれだけ明確だったか）

以下のJSON形式のみで回答してください（他のテキストを含めないでください）:

{{
  "completion": <整数>,
  "quality": <整数>,
  "task_complexity": <整数>,
  "prompt_specificity": <整数>
}}"""

        try:
            result = self._llm_generate_for_optimizer(
                prompt=eval_prompt,
                model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
                max_tokens=200,
                temperature=0.3
            )

            # JSONパース（result が None の場合も考慮）
            result = (result or "").strip()
            eval_json = SmartJSONParser.parse(result, default={})

            # 整数値のバリデーション（範囲チェック付き）
            for key in ["completion", "quality", "prompt_specificity"]:
                if key in eval_json:
                    try:
                        value = int(eval_json[key])
                        eval_json[key] = max(0, min(10, value))  # 0-10の範囲に制約
                    except (ValueError, TypeError):
                        eval_json[key] = 0
            if "task_complexity" in eval_json:
                try:
                    value = int(eval_json["task_complexity"])
                    eval_json["task_complexity"] = max(1, min(10, value))  # 1-10の範囲に制約
                except (ValueError, TypeError):
                    eval_json["task_complexity"] = 5

            if self.verbose:
                print(f"[Orchestrator] Evaluated @{agent_name}: {eval_json}")

            return eval_json

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"[Orchestrator] Failed to evaluate @{agent_name}: {e}")
            if self.verbose:
                print(f"[Orchestrator] Failed to evaluate @{agent_name}: {e}")
            return None

    def _aggregate_inline_evaluations(self) -> dict:
        """収集したインライン評価を集計"""
        if not self._inline_evaluations:
            return {}
        
        total = {"completion": 0, "quality": 0, "task_complexity": 0, "prompt_specificity": 0}
        count = len(self._inline_evaluations)
        
        for eval_data in self._inline_evaluations.values():
            for key in total:
                total[key] += eval_data.get(key, 5)
        
        return {key: val / count for key, val in total.items()}

    def create_session(self, title: str = "New Session", **metadata) -> str:
        """新しいセッションを作成"""
        return self.session_logger.create_session(
            profile=self.profile,
            title=title,
            **metadata
        )

    def get_session_history(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """セッションの会話履歴を取得"""
        return self.session_logger.get_agent_history(session_id, limit=limit)

    def continue_session(self, session_id: str) -> None:
        """既存のセッションを継続する際にプロファイルを確認する"""
        saved_profile = self.session_logger.get_session_profile(session_id)
        if saved_profile != self.profile and self.verbose:
            print(f'Warning: Session was created with profile "{saved_profile}", but current profile is "{self.profile}"')


    def get_sub_session_id(self, parent_session_id: str, agent_name: str) -> Optional[str]:
        """サブエージェントのセッションIDを取得"""
        if parent_session_id in self._sub_sessions:
            return self._sub_sessions[parent_session_id].get(agent_name)
        return None

    def get_all_sub_sessions(self, parent_session_id: str) -> Dict[str, str]:
        """親セッションに紐づく全サブセッションを取得"""
        return self._sub_sessions.get(parent_session_id, {})
    
    # ========== Optimizer API ==========
    
    def _llm_generate_for_optimizer(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float
    ) -> str:
        """Optimizer用のLLM生成関数（フォールバック付き・同期版）
        
        優先順位: ZAI → Gemini → OpenRouter → OpenAI
        レートリミット時は次のプロバイダーにフォールバック
        
        Note: この関数は同期的です。TaskAnalyzer/QualityEvaluator内で
        run_in_executor を使用して非同期に呼び出されます。
        """
        errors = []
        
        # 1. ZAI を試す（OpenAI互換API）
        zai_key = os.environ.get("ZAI_API_KEY")
        if zai_key:
            try:
                from openai import OpenAI
                
                client = OpenAI(
                    api_key=zai_key,
                    base_url="https://api.z.ai/api/coding/paas/v4"
                )
                response = client.chat.completions.create(
                    model=os.environ.get("ZAI_MODEL", "glm-4.7"),
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                errors.append(f"ZAI: {e}")
                if "429" not in str(e) and "rate" not in str(e).lower():
                    raise  # レートリミット以外のエラーは即座に raise
        
        # 2. Gemini にフォールバック
        gemini_key = (
            os.environ.get("GENAI_API_KEY") or
            os.environ.get("GEMINI_API_KEY") or
            os.environ.get("GOOGLE_API_KEY")
        )
        if gemini_key:
            try:
                from google import genai
                from google.genai import types
                
                client = genai.Client(api_key=gemini_key)
                response = client.models.generate_content(
                    model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=max_tokens,
                        temperature=temperature
                    )
                )
                return response.text
            except Exception as e:
                errors.append(f"Gemini: {e}")
                if "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e):
                    raise  # レートリミット以外のエラーは即座に raise
        
        # 3. OpenRouter にフォールバック
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                from openai import OpenAI
                
                client = OpenAI(
                    api_key=openrouter_key,
                    base_url="https://openrouter.ai/api/v1"
                )
                response = client.chat.completions.create(
                    model=os.environ.get("OPENROUTER_MODEL", "xiaomi/mimo-v2-flash"),
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                errors.append(f"OpenRouter: {e}")
        
        # 4. OpenAI にフォールバック
        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            try:
                from openai import OpenAI
                
                client = OpenAI(api_key=openai_key)
                model_name = os.environ.get("OPENAI_MODEL", "gpt-5.2-codex")
                # gpt-5系は max_completion_tokens を使用
                if "gpt-5" in model_name or "o1" in model_name or "o3" in model_name:
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[{"role": "user", "content": prompt}],
                        max_completion_tokens=max_tokens,
                        temperature=temperature
                    )
                else:
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=temperature
                    )
                return response.choices[0].message.content or ""
            except Exception as e:
                errors.append(f"OpenAI: {e}")
        
        # 全て失敗
        raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")
    
    def get_optimizer_stats(self, days: int = 30) -> Dict[str, Any]:
        """Optimizer の統計情報を取得"""
        return self.quality_tracker.get_stats(profile=self.profile, days=days)
    
    def get_optimizer_recommendations(self) -> List[str]:
        """Optimizer の推奨事項を取得"""
        from .optimizer import AutoTuner
        tuner = AutoTuner(self.quality_tracker, self.optimizer_config)
        return tuner.get_recommendations()
    
    def run_optimizer_tuning(self) -> Dict[str, Any]:
        """Optimizer の自動チューニングを実行"""
        from .optimizer import AutoTuner
        tuner = AutoTuner(self.quality_tracker, self.optimizer_config)
        result = tuner.tune()
        return {
            "status": result.status,
            "reason": result.reason,
            "old_thresholds": result.old_thresholds,
            "new_thresholds": result.new_thresholds,
            "samples_used": result.samples_used
        }
    
    def get_last_selection(self) -> Optional[Dict[str, Any]]:
        """最後のエージェント選択結果を取得"""
        if self._current_selection:
            return {
                "depth": self._current_selection.depth,
                "agents": self._current_selection.agents,
                "skipped": self._current_selection.skipped,
                "reason": self._current_selection.reason,
                "scores": self._current_scores
            }
        return None

    async def run(self, user_input: str, session_id: Optional[str] = None) -> str:
        """
        非同期でオーケストレーターを実行する（asyncio対応）

        Args:
            user_input: ユーザーからの入力
            session_id: セッションID（履歴管理用）
                        省略時は新しいセッションを作成

        Returns:
            エージェントからの応答
        """
        session_id, history = self._prepare_session(user_input, session_id)
        try:
            return await self.process_message(user_input, session_id, history)
        except OperationCancelled:
            if session_id:
                clear_cancel_event(session_id)
            return f"Job {session_id} was cancelled."
        except Exception as e:
            if session_id:
                clear_cancel_event(session_id)
            raise e
        finally:
            if session_id:
                # 正常終了時もクリーンアップ（二重に呼んでも安全）
                clear_cancel_event(session_id)

    async def process_message_stream(
        self,
        user_input: str,
        session_id: Optional[str] = None
    ):
        """
        ユーザー入力を処理し、結果をストリーミングで返す（SSE用）
        """
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def stream_callback(event_type: str, **kwargs):
            data = {"type": event_type}
            data.update(kwargs)
            loop.call_soon_threadsafe(queue.put_nowait, data)

        # 既存のコールバックを保存し、ストリーム用コールバックを設定
        original_callback = self.progress_callback
        self.progress_callback = stream_callback
        
        # 全てのランタイムのコールバックを更新
        for runtime in self.runtimes.values():
            runtime.progress_callback = stream_callback

        # 実行タスクを作成
        task = asyncio.create_task(self.run(user_input, session_id))

        try:
            while not task.done() or not queue.empty():
                try:
                    # タイムアウト付きでキューから取得して yield
                    item = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield item
                except (asyncio.TimeoutError, asyncio.QueueEmpty):
                    continue
            
            # 最終結果を確認（例外があればここで発生する）
            await task
            
        finally:
            # コールバックを元に戻す
            self.progress_callback = original_callback
            for runtime in self.runtimes.values():
                runtime.progress_callback = original_callback

    def run_sync(self, user_input: str, session_id: Optional[str] = None) -> str:
        """
        同期的にオーケストレーターを実行する
        """
        session_id, history = self._prepare_session(user_input, session_id)

        # 手動でループを管理して KeyboardInterrupt を適切に処理
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.process_message(user_input, session_id, history))
            return result
        except KeyboardInterrupt:
            # ペンディングタスクをキャンセル
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            # キャンセル完了を待つ（エラーを無視）
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            raise
        except RuntimeError as e:
            # 既にイベントループが実行中の場合
            if "cannot be called from a running event loop" in str(e):
                # nest_asyncio を試みる
                try:
                    import nest_asyncio
                    nest_asyncio.apply()
                    return asyncio.run(self.process_message(user_input, session_id, history))
                except ImportError:
                    pass
                
                # フォールバック: 新しいスレッドで実行
                import threading
                result = [None, None]
                
                def run_in_thread():
                    try:
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        result[0] = new_loop.run_until_complete(
                            self.process_message(user_input, session_id, history)
                        )
                        new_loop.close()
                    except Exception as ex:
                        result[1] = ex
                
                t = threading.Thread(target=run_in_thread)
                t.start()
                t.join(timeout=300)
                
                if result[1]:
                    raise result[1]
                return result[0] or ""
            raise
        except Exception as e:
            if self.verbose:
                print(f"[Orchestrator] run_sync error: {e}")
            return f"Error: {e}"
        finally:
            try:
                # async generator をクリーンアップ
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            except Exception:
                pass