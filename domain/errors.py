class DomainError(Exception):
    """Base class for errors raised by domain models and policies."""


class DomainValidationError(DomainError, ValueError):
    """A value cannot represent a valid domain object."""


class DomainStateError(DomainError, RuntimeError):
    """Persisted or computed state violates a domain invariant."""
