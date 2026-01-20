"""
Custom exception class hierarchy for the moco package.

Exception hierarchy:
    MocoError (Base)
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
    Base exception class for the moco package.

    All moco-specific exceptions inherit from this class.
    Can hold an error code and metadata.

    Attributes:
        message: Error message
        code: Optional error code (e.g., "MOCO-001")
        details: Additional information about the error
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
# Configuration Errors
# =============================================================================


class ConfigurationError(MocoError):
    """
    Configuration-related errors.

    Raised when configuration file loading fails, required parameters are missing,
    or invalid configuration values are provided.

    Examples:
        >>> raise ConfigurationError("Profile 'default' not found")
        >>> raise ConfigurationError(
        ...     "API key is not configured",
        ...     code="CFG-001",
        ...     details={"provider": "openai", "env_var": "OPENAI_API_KEY"}
        ... )
    """

    pass


# =============================================================================
# Provider Errors
# =============================================================================


class ProviderError(MocoError):
    """
    Base class for LLM provider-related errors.

    Common base for errors occurring during communication with providers
    such as OpenAI, Google, Anthropic, etc.

    Attributes:
        provider: Name of the provider where the error occurred
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
    Connection error to a provider.

    Raised for connection-level issues such as network failures, timeouts,
    or DNS resolution failures.

    Examples:
        >>> raise ProviderConnectionError(
        ...     "Connection to OpenAI API timed out",
        ...     provider="openai"
        ... )
    """

    pass


class ProviderRateLimitError(ProviderError):
    """
    Rate limit error from a provider.

    Raised when API call frequency exceeds limits.
    The retry_after attribute indicates when retrying is possible.

    Attributes:
        retry_after: Wait time in seconds before retrying (if provided by the provider)
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
    Authentication error from a provider.

    Raised for authentication and authorization issues such as invalid,
    expired, or insufficient API keys.

    Examples:
        >>> raise ProviderAuthenticationError(
        ...     "Invalid API key",
        ...     provider="anthropic",
        ...     code="AUTH-001"
        ... )
    """

    pass


# =============================================================================
# Tool Errors
# =============================================================================


class ToolError(MocoError):
    """
    Base class for tool execution-related errors.

    Common base for errors related to tools (function calls) used by agents.

    Attributes:
        tool_name: Name of the tool where the error occurred
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
    Error when a tool is not found.

    Raised when the specified tool name is not registered.

    Examples:
        >>> raise ToolNotFoundError(
        ...     "Tool 'search_web' is not registered",
        ...     tool_name="search_web"
        ... )
    """

    pass


class ToolExecutionError(ToolError):
    """
    Error during tool execution.

    Raised when an exception occurs during tool execution.
    Can hold the original exception as 'cause'.

    Attributes:
        cause: Original exception (if any)
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
        self.__cause__ = cause  # Standard exception chaining


class ToolValidationError(ToolError):
    """
    Validation error for tool arguments.

    Raised when arguments passed to a tool do not meet expected types or constraints.

    Attributes:
        argument_name: Name of the argument that failed validation
        expected: Description of the expected value/type
        actual: The actual value passed
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
# Guardrail Errors
# =============================================================================


class GuardrailError(MocoError):
    """
    Base class for guardrail-related errors.

    Common base for errors related to guardrail features such as input/output
    validation and safety checks.

    Attributes:
        guardrail_name: Name of the guardrail that triggered the error
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
    Input validation failure error.

    Raised when user input fails guardrail validation.
    Includes detection of inappropriate content, prohibited patterns, etc.

    Examples:
        >>> raise InputValidationError(
        ...     "Input contains prohibited content",
        ...     guardrail_name="content_filter"
        ... )
    """

    pass


class OutputValidationError(GuardrailError):
    """
    Output validation failure error.

    Raised when LLM output fails guardrail validation.
    Includes prevention of sensitive information leakage, format validation, etc.

    Examples:
        >>> raise OutputValidationError(
        ...     "Output may contain sensitive information",
        ...     guardrail_name="pii_detector"
        ... )
    """

    pass


# =============================================================================
# Context Errors
# =============================================================================


class ContextError(MocoError):
    """
    Base class for context-related errors.

    Common base for errors related to conversation history and context window management.
    """

    pass


class ContextOverflowError(ContextError):
    """
    Context token limit exceeded error.

    Raised when conversation history or prompts exceed the model's maximum token count.

    Attributes:
        current_tokens: Current number of tokens
        max_tokens: Maximum allowed number of tokens
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
# Checkpoint Errors
# =============================================================================


class CheckpointError(MocoError):
    """
    Checkpoint-related errors.

    Errors related to saving and restoring agent state.
    Includes serialization failures, file I/O errors, etc.

    Examples:
        >>> raise CheckpointError(
        ...     "Failed to load checkpoint file",
        ...     code="CKP-001",
        ...     details={"path": "/tmp/checkpoint.json"}
        ... )
    """

    pass


# =============================================================================
# MCP Errors
# =============================================================================


class MCPError(MocoError):
    """
    MCP (Model Context Protocol) related errors.

    Raised for communication errors with MCP servers, protocol errors,
    resource acquisition failures, etc.

    Attributes:
        server_name: Name of the MCP server where the error occurred
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
# Error Code Constants (Optional)
# =============================================================================


class ErrorCodes:
    """
    Error code constants.

    Helper class for consistent error code management.
    """

    # Configuration Errors (CFG-xxx)
    CONFIG_FILE_NOT_FOUND = "CFG-001"
    CONFIG_INVALID_FORMAT = "CFG-002"
    CONFIG_MISSING_REQUIRED = "CFG-003"
    CONFIG_PROFILE_NOT_FOUND = "CFG-004"

    # Provider Errors (PRV-xxx)
    PROVIDER_CONNECTION_FAILED = "PRV-001"
    PROVIDER_TIMEOUT = "PRV-002"
    PROVIDER_RATE_LIMITED = "PRV-003"
    PROVIDER_AUTH_FAILED = "PRV-004"
    PROVIDER_INVALID_RESPONSE = "PRV-005"

    # Tool Errors (TL-xxx)
    TOOL_NOT_FOUND = "TL-001"
    TOOL_EXECUTION_FAILED = "TL-002"
    TOOL_VALIDATION_FAILED = "TL-003"
    TOOL_TIMEOUT = "TL-004"

    # Guardrail Errors (GR-xxx)
    GUARDRAIL_INPUT_BLOCKED = "GR-001"
    GUARDRAIL_OUTPUT_BLOCKED = "GR-002"

    # Context Errors (CTX-xxx)
    CONTEXT_OVERFLOW = "CTX-001"
    CONTEXT_CORRUPTED = "CTX-002"

    # Checkpoint Errors (CKP-xxx)
    CHECKPOINT_SAVE_FAILED = "CKP-001"
    CHECKPOINT_LOAD_FAILED = "CKP-002"
    CHECKPOINT_CORRUPTED = "CKP-003"

    # MCP Errors (MCP-xxx)
    MCP_CONNECTION_FAILED = "MCP-001"
    MCP_PROTOCOL_ERROR = "MCP-002"
    MCP_RESOURCE_NOT_FOUND = "MCP-003"
