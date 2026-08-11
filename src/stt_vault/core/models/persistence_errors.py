class PersistenceError(Exception):
    """Base class for persistence failures exposed to application layers."""


class DatabaseClosedError(PersistenceError):
    """The database instance has been closed and cannot accept operations."""


class MigrationStateError(PersistenceError):
    """The durable database schema is incompatible with the required shape."""


class StaleClaimError(PersistenceError):
    """A conditional claim or recovery reservation no longer matches durable state."""


class AssetNotFoundError(PersistenceError, KeyError):
    """An asset mutation target is absent at operation time."""


class FolderNotFoundError(PersistenceError, KeyError):
    """A required folder is absent at operation time."""


class FolderDataIntegrityError(PersistenceError):
    """Persisted folder data cannot form a valid response tree."""


class EmbeddingSpaceConflictError(PersistenceError, ValueError):
    """Speaker embeddings cannot be combined across different spaces."""
