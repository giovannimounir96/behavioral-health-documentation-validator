"""Domain-specific errors with user-facing messages."""


class ValidatorError(Exception):
    """Base exception for expected validation-pipeline failures."""


class ConfigurationError(ValidatorError):
    """Raised when the validation rules configuration is invalid."""


class InputDataError(ValidatorError):
    """Raised when an input file violates the documented data contract."""


class PersistenceError(ValidatorError):
    """Raised when run history cannot be safely read or written."""


class RunNotFoundError(ValidatorError):
    """Raised when a requested validation run does not exist."""
