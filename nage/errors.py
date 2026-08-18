from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    USAGE = 2
    NEEDS_INPUT = 3
    CANCELLED = 4
    PROVIDER = 5
    TOOL = 6
    POLICY_DENIED = 7


class NageError(Exception):
    """Base class for expected application errors."""

    code = "nage_error"
    exit_code = ExitCode.TOOL


class ConfigurationError(NageError):
    code = "configuration_error"
    exit_code = ExitCode.USAGE


class StorageError(NageError):
    code = "storage_error"
    exit_code = ExitCode.TOOL


class MigrationError(StorageError):
    code = "migration_error"


class ProviderError(NageError):
    code = "provider_error"
    exit_code = ExitCode.PROVIDER


class TransientProviderError(ProviderError):
    code = "transient_provider_error"


class ProtocolError(ProviderError):
    code = "protocol_error"


class ContextError(NageError):
    code = "context_error"
    exit_code = ExitCode.USAGE


class ToolError(NageError):
    code = "tool_error"
    exit_code = ExitCode.TOOL


class PolicyDeniedError(NageError):
    code = "policy_denied"
    exit_code = ExitCode.POLICY_DENIED
