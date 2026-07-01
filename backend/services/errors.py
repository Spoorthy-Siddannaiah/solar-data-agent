"""Service-layer errors independent of any future transport layer."""


class ServiceError(Exception):
    """Base application service error."""


class PermissionDeniedError(ServiceError):
    """Raised when the trusted user context lacks a required capability."""


class ResourceNotFoundError(ServiceError):
    """Raised when a resource is absent or inaccessible to the caller."""
