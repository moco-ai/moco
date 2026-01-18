"""
OpenTelemetry 統合モジュール。

トレースとメトリクスの収集・エクスポートを提供する。
OpenTelemetry がインストールされていない場合は NoOp として動作する。
"""

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Any, Dict, Generator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# OpenTelemetry のインポート（オプション依存）
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        PeriodicExportingMetricReader,
        ConsoleMetricExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.semconv.resource import ResourceAttributes
    
    # OTLP エクスポーター（gRPC）
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        OTLP_AVAILABLE = True
    except ImportError:
        OTLP_AVAILABLE = False
    
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    OTLP_AVAILABLE = False


@dataclass
class TelemetryConfig:
    """
    テレメトリ設定。
    
    Attributes:
        enabled: テレメトリの有効化（デフォルト: False）
        service_name: サービス名（デフォルト: "moco"）
        otlp_endpoint: OTLP エンドポイント URL（例: "http://localhost:4317"）
        console_export: コンソール出力の有効化（デバッグ用）
    """
    enabled: bool = False
    service_name: str = "moco"
    otlp_endpoint: Optional[str] = None
    console_export: bool = False
    
    def __post_init__(self):
        # 環境変数からの設定上書き
        if os.environ.get("OTEL_ENABLED", "").lower() in ("true", "1", "yes"):
            self.enabled = True
        if os.environ.get("OTEL_SERVICE_NAME"):
            self.service_name = os.environ["OTEL_SERVICE_NAME"]
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            self.otlp_endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
        if os.environ.get("OTEL_CONSOLE_EXPORT", "").lower() in ("true", "1", "yes"):
            self.console_export = True


class NoOpSpan:
    """OpenTelemetry が無効な場合のダミースパン"""
    
    def set_attribute(self, key: str, value: Any) -> None:
        pass
    
    def set_status(self, status: Any) -> None:
        pass
    
    def record_exception(self, exception: Exception) -> None:
        pass
    
    def end(self) -> None:
        pass
    
    def __enter__(self) -> "NoOpSpan":
        return self
    
    def __exit__(self, *args) -> None:
        pass


class Telemetry:
    """
    OpenTelemetry 統合クラス。
    
    トレースとメトリクスの収集を行う。
    OpenTelemetry がインストールされていない場合や無効な場合は NoOp として動作する。
    
    Example:
        >>> config = TelemetryConfig(enabled=True, console_export=True)
        >>> telemetry = Telemetry(config)
        >>> with telemetry.span("my_operation", {"key": "value"}):
        ...     # 処理
        ...     pass
        >>> telemetry.record_llm_call("gemini", "gemini-2.0-flash", 100, 50, 1234.5, True)
    """
    
    def __init__(self, config: Optional[TelemetryConfig] = None):
        """
        テレメトリを初期化する。
        
        Args:
            config: テレメトリ設定（省略時はデフォルト設定）
        """
        self.config = config or TelemetryConfig()
        self._tracer = None
        self._meter = None
        
        # メトリクス
        self._llm_calls_counter = None
        self._llm_input_tokens_counter = None
        self._llm_output_tokens_counter = None
        self._llm_latency_histogram = None
        self._tool_calls_counter = None
        self._tool_latency_histogram = None
        self._tool_errors_counter = None
        
        if self.config.enabled and OTEL_AVAILABLE:
            self._initialize_otel()
        else:
            if self.config.enabled and not OTEL_AVAILABLE:
                logger.warning(
                    "OpenTelemetry is enabled but not installed. "
                    "Run: pip install opentelemetry-sdk opentelemetry-exporter-otlp"
                )
    
    def _initialize_otel(self) -> None:
        """OpenTelemetry を初期化する"""
        # リソース設定
        resource = Resource.create({
            ResourceAttributes.SERVICE_NAME: self.config.service_name,
        })

        # 既存の TracerProvider があれば再利用（グローバル競合対策）
        existing_provider = trace.get_tracer_provider()
        if (
            hasattr(existing_provider, 'get_tracer') and
            not isinstance(existing_provider, trace.ProxyTracerProvider)
        ):
            # 既に設定済みの TracerProvider がある場合はそのまま使用
            logger.debug("Using existing TracerProvider")
            self._tracer = trace.get_tracer(self.config.service_name)
        else:
            # 新規に TracerProvider を設定
            tracer_provider = TracerProvider(resource=resource)

            # スパンエクスポーター設定
            if self.config.otlp_endpoint and OTLP_AVAILABLE:
                otlp_exporter = OTLPSpanExporter(endpoint=self.config.otlp_endpoint)
                tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                logger.info(f"OTLP trace exporter configured: {self.config.otlp_endpoint}")

            if self.config.console_export:
                tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
                logger.info("Console trace exporter configured")

            trace.set_tracer_provider(tracer_provider)
            self._tracer = trace.get_tracer(self.config.service_name)

        # メーター設定
        metric_readers = []
        
        if self.config.otlp_endpoint and OTLP_AVAILABLE:
            otlp_metric_exporter = OTLPMetricExporter(endpoint=self.config.otlp_endpoint)
            metric_readers.append(
                PeriodicExportingMetricReader(otlp_metric_exporter, export_interval_millis=60000)
            )
        
        if self.config.console_export:
            metric_readers.append(
                PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60000)
            )
        
        if metric_readers:
            meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
            metrics.set_meter_provider(meter_provider)
            self._meter = metrics.get_meter(self.config.service_name)
            self._setup_metrics()
    
    def _setup_metrics(self) -> None:
        """メトリクスを設定する"""
        if not self._meter:
            return
        
        # LLM メトリクス
        self._llm_calls_counter = self._meter.create_counter(
            name="moco.llm.calls",
            description="Number of LLM API calls",
            unit="1",
        )
        
        self._llm_input_tokens_counter = self._meter.create_counter(
            name="moco.llm.tokens.input",
            description="Number of input tokens sent to LLM",
            unit="1",
        )
        
        self._llm_output_tokens_counter = self._meter.create_counter(
            name="moco.llm.tokens.output",
            description="Number of output tokens received from LLM",
            unit="1",
        )
        
        self._llm_latency_histogram = self._meter.create_histogram(
            name="moco.llm.latency",
            description="LLM API call latency",
            unit="ms",
        )
        
        # ツールメトリクス
        self._tool_calls_counter = self._meter.create_counter(
            name="moco.tool.calls",
            description="Number of tool calls",
            unit="1",
        )
        
        self._tool_latency_histogram = self._meter.create_histogram(
            name="moco.tool.latency",
            description="Tool execution latency",
            unit="ms",
        )
        
        self._tool_errors_counter = self._meter.create_counter(
            name="moco.tool.errors",
            description="Number of tool execution errors",
            unit="1",
        )
    
    @property
    def is_enabled(self) -> bool:
        """テレメトリが有効かどうか"""
        return self.config.enabled and OTEL_AVAILABLE and self._tracer is not None

    @property
    def is_metrics_enabled(self) -> bool:
        """メトリクス記録が有効かどうか"""
        return self._meter is not None

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None
    ) -> Generator[Any, None, None]:
        """
        トレーススパンを作成する。
        
        Args:
            name: スパン名
            attributes: スパン属性（キーと値のペア）
        
        Yields:
            スパンオブジェクト（OpenTelemetry Span または NoOpSpan）
        
        Example:
            >>> with telemetry.span("process_message", {"user_id": "123"}):
            ...     # 処理
            ...     pass
        """
        if not self.is_enabled:
            yield NoOpSpan()
            return
        
        with self._tracer.start_as_current_span(name) as span:
            if attributes:
                for key, value in attributes.items():
                    # OpenTelemetry は特定の型のみサポート
                    if isinstance(value, (str, int, float, bool)):
                        span.set_attribute(key, value)
                    else:
                        span.set_attribute(key, str(value))
            yield span
    
    def record_llm_call(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        success: bool
    ) -> None:
        """
        LLM 呼び出しメトリクスを記録する。
        
        Args:
            provider: プロバイダ名（"gemini", "openai", "openrouter"）
            model: モデル名
            input_tokens: 入力トークン数
            output_tokens: 出力トークン数
            latency_ms: レイテンシ（ミリ秒）
            success: 成功したかどうか
        """
        if not self.is_enabled:
            return
        
        labels = {
            "provider": provider,
            "model": model,
            "success": str(success).lower(),
        }
        
        if self._llm_calls_counter:
            self._llm_calls_counter.add(1, labels)
        
        if self._llm_input_tokens_counter:
            self._llm_input_tokens_counter.add(input_tokens, labels)
        
        if self._llm_output_tokens_counter:
            self._llm_output_tokens_counter.add(output_tokens, labels)
        
        if self._llm_latency_histogram:
            self._llm_latency_histogram.record(latency_ms, labels)
    
    def _normalize_error_type(self, error: str) -> str:
        """
        エラーメッセージを種別に正規化する。

        カーディナリティ対策として、エラーメッセージを限定された種別に分類する。

        Args:
            error: エラーメッセージ

        Returns:
            正規化されたエラー種別
        """
        error_lower = error.lower()
        if "timeout" in error_lower:
            return "timeout"
        if "permission" in error_lower or "access denied" in error_lower:
            return "permission_denied"
        if "not found" in error_lower:
            return "not_found"
        if "connection" in error_lower:
            return "connection_error"
        if "rate limit" in error_lower or "quota" in error_lower:
            return "rate_limit"
        if "validation" in error_lower or "invalid" in error_lower:
            return "validation_error"
        if "authentication" in error_lower or "unauthorized" in error_lower:
            return "authentication_error"
        return "unknown"

    def record_tool_call(
        self,
        tool_name: str,
        latency_ms: float,
        success: bool,
        error: Optional[str] = None
    ) -> None:
        """
        ツール呼び出しメトリクスを記録する。

        Args:
            tool_name: ツール名
            latency_ms: レイテンシ（ミリ秒）
            success: 成功したかどうか
            error: エラーメッセージ（失敗時）
        """
        if not self.is_enabled:
            return

        labels = {
            "tool_name": tool_name,
            "success": str(success).lower(),
        }

        if self._tool_calls_counter:
            self._tool_calls_counter.add(1, labels)

        if self._tool_latency_histogram:
            self._tool_latency_histogram.record(latency_ms, labels)

        if not success and self._tool_errors_counter:
            error_labels = {**labels}
            if error:
                # エラー種別に正規化（カーディナリティ対策）
                error_labels["error_type"] = self._normalize_error_type(error)
            self._tool_errors_counter.add(1, error_labels)

    def record_session(
        self,
        session_id: str,
        total_turns: int,
        total_tokens: int,
        duration_s: float
    ) -> None:
        """
        セッションメトリクスを記録する。
        
        Args:
            session_id: セッションID
            total_turns: 総ターン数
            total_tokens: 総トークン数
            duration_s: セッション継続時間（秒）
        
        Note:
            セッションメトリクスはスパンとして記録される。
        """
        if not self.is_enabled:
            return
        
        # セッション情報をスパンとして記録
        with self.span("session_summary", {
            "session.id": session_id,
            "session.total_turns": total_turns,
            "session.total_tokens": total_tokens,
            "session.duration_s": duration_s,
        }):
            pass  # 属性のみ記録


# グローバルインスタンス（遅延初期化）
_global_telemetry: Optional[Telemetry] = None


def get_telemetry(config: Optional[TelemetryConfig] = None) -> Telemetry:
    """
    グローバルテレメトリインスタンスを取得する。
    
    Args:
        config: テレメトリ設定（初回呼び出し時のみ有効）
    
    Returns:
        Telemetry インスタンス
    """
    global _global_telemetry
    if _global_telemetry is None:
        _global_telemetry = Telemetry(config)
    return _global_telemetry


def reset_telemetry() -> None:
    """グローバルテレメトリインスタンスをリセットする（テスト用）"""
    global _global_telemetry
    _global_telemetry = None
