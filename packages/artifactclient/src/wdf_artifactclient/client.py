"""The store client of one role: streaming puts and gets that verify size and SHA-256."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from ._stub import not_implemented

STUBBED = ("ArtifactClient", "verify")

Source = bytes | Path | BinaryIO
Progress = Callable[[int], None]


class ArtifactClient:
    def __init__(
        self, endpoint: str, credentials: Any, retry: Any = None, chunk_bytes: int | None = None
    ):
        raise not_implemented("C")

    def put_content(
        self,
        source: Source,
        kind: str,
        name: str,
        *,
        conn: Any = None,
        job_id: str | None = None,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> Any:
        raise not_implemented("C")

    def put_attempt(
        self,
        source: Source,
        kind: str,
        name: str,
        producer: str,
        tenant: str,
        batch: str,
        seed: str,
        segment: str | None = None,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> Any:
        raise not_implemented("C")

    def get(
        self,
        reference: Any,
        dest: Path,
        *,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> None:
        raise not_implemented("C")

    def get_bytes(
        self,
        reference: Any,
        max_bytes: int,
        *,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> bytes:
        raise not_implemented("C")

    def head(
        self,
        reference: Any,
        *,
        deadline: float | None = None,
        progress: Progress | None = None,
    ) -> Any:
        raise not_implemented("C")


def verify(client: ArtifactClient, reference: Any, deadline: float | None = None) -> None:
    raise not_implemented("C")
