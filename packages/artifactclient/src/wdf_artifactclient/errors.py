"""The typed errors of failures.yaml, each carrying the code of its entry."""

from ._stub import not_implemented

STUBBED = (
    "ArtifactError",
    "StoreUnavailable",
    "StoreForbidden",
    "ArtifactNotFound",
    "ArtifactCorrupt",
    "ArtifactLost",
    "OutputUploadFailed",
    "OutputMissing",
    "CheckpointCorrupt",
)


class ArtifactError(Exception):
    code: str

    def __init__(self, *args: object):
        raise not_implemented("C")


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
    cause: ArtifactError


class OutputUploadFailed(ArtifactError):
    code = "OUTPUT_UPLOAD_FAILED"


class OutputMissing(ArtifactError):
    code = "OUTPUT_MISSING"


class CheckpointCorrupt(ArtifactError):
    code = "CHECKPOINT_CORRUPT"
