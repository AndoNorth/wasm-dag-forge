"""The typed errors of failures.yaml, each carrying the code of its entry."""

STUBBED = ()

# The reason of a task.attempt.failed event holds at most this many characters.
REASON_LIMIT = 1024


class ArtifactError(Exception):
    code: str

    @property
    def reason(self) -> str:
        """The text of a task.attempt.failed reason: the code, a colon, then the detail."""
        return f"{self.code}: {self}"[:REASON_LIMIT]


class StoreUnavailable(ArtifactError):
    code = "STORE_UNAVAILABLE"


class StoreForbidden(ArtifactError):
    code = "STORE_FORBIDDEN"


class ArtifactNotFound(ArtifactError):
    code = "ARTIFACT_NOT_FOUND"


class ArtifactCorrupt(ArtifactError):
    code = "ARTIFACT_CORRUPT"


class ArtifactLost(ArtifactError):
    """An artifact that stayed missing or corrupt after the refetch, with the first cause."""

    code = "ARTIFACT_LOST"

    def __init__(self, message: str, cause: ArtifactError):
        super().__init__(message)
        self.cause = cause
        self.__cause__ = cause


class OutputUploadFailed(ArtifactError):
    code = "OUTPUT_UPLOAD_FAILED"


class OutputMissing(ArtifactError):
    code = "OUTPUT_MISSING"


class CheckpointCorrupt(ArtifactError):
    code = "CHECKPOINT_CORRUPT"
