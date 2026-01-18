"""
moco パッケージ用カスタム例外クラス階層。

例外の階層構造:
    MocoError (基底)
    ├── ConfigurationError
    ├── ProviderError
    │   ├── ProviderConnectionError
    │   ├── ProviderRateLimitError
    │   └── ProviderAuthenticationError
    ├── ToolError
    │   ├── ToolNotFoundError
    │   ├── ToolExecutionError
    │   └── ToolValidationError
    ├── GuardrailError
    │   ├── InputValidationError
    │   └── OutputValidationError
    ├── ContextError
    │   └── ContextOverflowError
    ├── CheckpointError
    └── MCPError
"""

from __future__ import annotations

from typing import Any


class MocoError(Exception):
    """
    moco パッケージの基底例外クラス。

    すべての moco 固有例外はこのクラスを継承する。
    エラーコードとメタデータを保持可能。

    Attributes:
        message: エラーメッセージ
        code: オプションのエラーコード（例: "MOCO-001"）
        details: エラーに関する追加情報
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, code={self.code!r})"


# =============================================================================
# 設定関連エラー
# =============================================================================


class ConfigurationError(MocoError):
    """
    設定関連のエラー。

    設定ファイルの読み込み失敗、必須パラメータの欠落、
    不正な設定値などの場合に発生。

    Examples:
        >>> raise ConfigurationError("プロファイル 'default' が見つかりません")
        >>> raise ConfigurationError(
        ...     "API キーが設定されていません",
        ...     code="CFG-001",
        ...     details={"provider": "openai", "env_var": "OPENAI_API_KEY"}
        ... )
    """

    pass


# =============================================================================
# プロバイダ関連エラー
# =============================================================================


class ProviderError(MocoError):
    """
    LLM プロバイダ関連エラーの基底クラス。

    OpenAI、Google、Anthropic 等のプロバイダとの通信で
    発生するエラーの共通基底。

    Attributes:
        provider: エラーが発生したプロバイダ名
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if provider:
            details["provider"] = provider
        super().__init__(message, code=code, details=details)
        self.provider = provider


class ProviderConnectionError(ProviderError):
    """
    プロバイダへの接続エラー。

    ネットワーク障害、タイムアウト、DNS 解決失敗など
    接続レベルの問題で発生。

    Examples:
        >>> raise ProviderConnectionError(
        ...     "OpenAI API への接続がタイムアウトしました",
        ...     provider="openai"
        ... )
    """

    pass


class ProviderRateLimitError(ProviderError):
    """
    プロバイダのレート制限エラー。

    API 呼び出し頻度が制限を超えた場合に発生。
    retry_after 属性でリトライ可能時刻を示す。

    Attributes:
        retry_after: リトライまでの待機秒数（プロバイダから提供された場合）
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        retry_after: float | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if retry_after is not None:
            details["retry_after"] = retry_after
        super().__init__(message, provider=provider, code=code, details=details)
        self.retry_after = retry_after


class ProviderAuthenticationError(ProviderError):
    """
    プロバイダの認証エラー。

    API キーの無効、期限切れ、権限不足などの
    認証・認可に関する問題で発生。

    Examples:
        >>> raise ProviderAuthenticationError(
        ...     "無効な API キーです",
        ...     provider="anthropic",
        ...     code="AUTH-001"
        ... )
    """

    pass


# =============================================================================
# ツール関連エラー
# =============================================================================


class ToolError(MocoError):
    """
    ツール実行関連エラーの基底クラス。

    エージェントが使用するツール（関数呼び出し）に関する
    エラーの共通基底。

    Attributes:
        tool_name: エラーが発生したツール名
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if tool_name:
            details["tool_name"] = tool_name
        super().__init__(message, code=code, details=details)
        self.tool_name = tool_name


class ToolNotFoundError(ToolError):
    """
    ツールが見つからないエラー。

    指定されたツール名が登録されていない場合に発生。

    Examples:
        >>> raise ToolNotFoundError(
        ...     "ツール 'search_web' は登録されていません",
        ...     tool_name="search_web"
        ... )
    """

    pass


class ToolExecutionError(ToolError):
    """
    ツール実行時エラー。

    ツールの実行中に例外が発生した場合に発生。
    元の例外を cause として保持可能。

    Attributes:
        cause: 元の例外（存在する場合）
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str | None = None,
        cause: Exception | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if cause:
            details["cause_type"] = type(cause).__name__
            details["cause_message"] = str(cause)
        super().__init__(message, tool_name=tool_name, code=code, details=details)
        self.cause = cause
        self.__cause__ = cause  # 標準の例外チェーン


class ToolValidationError(ToolError):
    """
    ツール引数の検証エラー。

    ツールに渡された引数が期待される型や制約を
    満たさない場合に発生。

    Attributes:
        argument_name: 検証に失敗した引数名
        expected: 期待される値/型の説明
        actual: 実際に渡された値
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str | None = None,
        argument_name: str | None = None,
        expected: str | None = None,
        actual: Any = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if argument_name:
            details["argument_name"] = argument_name
        if expected:
            details["expected"] = expected
        if actual is not None:
            details["actual"] = repr(actual)
        super().__init__(message, tool_name=tool_name, code=code, details=details)
        self.argument_name = argument_name
        self.expected = expected
        self.actual = actual


# =============================================================================
# ガードレール関連エラー
# =============================================================================


class GuardrailError(MocoError):
    """
    ガードレール関連エラーの基底クラス。

    入力/出力の検証、安全性チェックなどの
    ガードレール機能に関するエラーの共通基底。

    Attributes:
        guardrail_name: エラーを発生させたガードレール名
    """

    def __init__(
        self,
        message: str,
        *,
        guardrail_name: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if guardrail_name:
            details["guardrail_name"] = guardrail_name
        super().__init__(message, code=code, details=details)
        self.guardrail_name = guardrail_name


class InputValidationError(GuardrailError):
    """
    入力検証の失敗エラー。

    ユーザー入力がガードレールの検証に失敗した場合に発生。
    不適切なコンテンツ、禁止パターンの検出など。

    Examples:
        >>> raise InputValidationError(
        ...     "入力に禁止されたコンテンツが含まれています",
        ...     guardrail_name="content_filter"
        ... )
    """

    pass


class OutputValidationError(GuardrailError):
    """
    出力検証の失敗エラー。

    LLM の出力がガードレールの検証に失敗した場合に発生。
    機密情報の漏洩防止、フォーマット検証など。

    Examples:
        >>> raise OutputValidationError(
        ...     "出力に機密情報が含まれている可能性があります",
        ...     guardrail_name="pii_detector"
        ... )
    """

    pass


# =============================================================================
# コンテキスト関連エラー
# =============================================================================


class ContextError(MocoError):
    """
    コンテキスト関連エラーの基底クラス。

    会話履歴、コンテキストウィンドウの管理に関する
    エラーの共通基底。
    """

    pass


class ContextOverflowError(ContextError):
    """
    コンテキストのトークン上限超過エラー。

    会話履歴やプロンプトがモデルの最大トークン数を
    超えた場合に発生。

    Attributes:
        current_tokens: 現在のトークン数
        max_tokens: 最大許容トークン数
    """

    def __init__(
        self,
        message: str,
        *,
        current_tokens: int | None = None,
        max_tokens: int | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if current_tokens is not None:
            details["current_tokens"] = current_tokens
        if max_tokens is not None:
            details["max_tokens"] = max_tokens
        super().__init__(message, code=code, details=details)
        self.current_tokens = current_tokens
        self.max_tokens = max_tokens


# =============================================================================
# チェックポイント関連エラー
# =============================================================================


class CheckpointError(MocoError):
    """
    チェックポイント関連エラー。

    エージェントの状態保存・復元に関するエラー。
    シリアライズ失敗、ファイル I/O エラーなど。

    Examples:
        >>> raise CheckpointError(
        ...     "チェックポイントファイルの読み込みに失敗しました",
        ...     code="CKP-001",
        ...     details={"path": "/tmp/checkpoint.json"}
        ... )
    """

    pass


# =============================================================================
# MCP 関連エラー
# =============================================================================


class MCPError(MocoError):
    """
    MCP (Model Context Protocol) 関連エラー。

    MCP サーバーとの通信、プロトコルエラー、
    リソース取得失敗などで発生。

    Attributes:
        server_name: エラーが発生した MCP サーバー名
    """

    def __init__(
        self,
        message: str,
        *,
        server_name: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if server_name:
            details["server_name"] = server_name
        super().__init__(message, code=code, details=details)
        self.server_name = server_name


# =============================================================================
# エラーコード定数（オプション）
# =============================================================================


class ErrorCodes:
    """
    エラーコード定数。

    一貫したエラーコード管理のためのヘルパークラス。
    """

    # 設定エラー (CFG-xxx)
    CONFIG_FILE_NOT_FOUND = "CFG-001"
    CONFIG_INVALID_FORMAT = "CFG-002"
    CONFIG_MISSING_REQUIRED = "CFG-003"
    CONFIG_PROFILE_NOT_FOUND = "CFG-004"

    # プロバイダエラー (PRV-xxx)
    PROVIDER_CONNECTION_FAILED = "PRV-001"
    PROVIDER_TIMEOUT = "PRV-002"
    PROVIDER_RATE_LIMITED = "PRV-003"
    PROVIDER_AUTH_FAILED = "PRV-004"
    PROVIDER_INVALID_RESPONSE = "PRV-005"

    # ツールエラー (TL-xxx)
    TOOL_NOT_FOUND = "TL-001"
    TOOL_EXECUTION_FAILED = "TL-002"
    TOOL_VALIDATION_FAILED = "TL-003"
    TOOL_TIMEOUT = "TL-004"

    # ガードレールエラー (GR-xxx)
    GUARDRAIL_INPUT_BLOCKED = "GR-001"
    GUARDRAIL_OUTPUT_BLOCKED = "GR-002"

    # コンテキストエラー (CTX-xxx)
    CONTEXT_OVERFLOW = "CTX-001"
    CONTEXT_CORRUPTED = "CTX-002"

    # チェックポイントエラー (CKP-xxx)
    CHECKPOINT_SAVE_FAILED = "CKP-001"
    CHECKPOINT_LOAD_FAILED = "CKP-002"
    CHECKPOINT_CORRUPTED = "CKP-003"

    # MCP エラー (MCP-xxx)
    MCP_CONNECTION_FAILED = "MCP-001"
    MCP_PROTOCOL_ERROR = "MCP-002"
    MCP_RESOURCE_NOT_FOUND = "MCP-003"
