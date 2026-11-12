"""The compile cache activities, `cache_lookup`, `cache_record` and `cache_invalidate`, under a
login in `wdf_artifacts`."""

import tempfile
from pathlib import Path

from pydantic import ValidationError
from temporalio import activity
from wdf_artifactclient import cache
from wdf_artifactclient.client import ArtifactClient, TooLarge, kind_caps, wall_now
from wdf_artifactindex import Invalidated, WasmGone, invalidate_cache_entry, lookup_cache_entry
from wdf_compile import bundle
from wdf_keys import compile_cache_key
from wdf_runtime.models import BuildInfo
from wdf_runtime.pins import COMPONENTIZE_PY_VERSION, WIT_VERSION

from ..testing.control import (
    CacheAnswer,
    CacheInvalidate,
    CacheLookup,
    CacheRecord,
    control_error,
)
from .db import connect, typed_errors

VERSIONS = {"componentize_py_version": COMPONENTIZE_PY_VERSION, "wit_version": WIT_VERSION}


def bundle_key(path: Path, entry: str) -> str | None:
    """The compile cache key of a bundle and entry under the pins of this build, or None for a
    bundle that `wdf_compile.build` refuses before it builds: one that breaks the bundle format,
    has no lockfile or lacks the entry."""
    try:
        info = bundle.info(path)
    except bundle.Refused:
        return None
    if info.lockfile_sha256 is None or entry not in info.modules:
        return None
    return compile_cache_key(
        info.source_sha256, info.lockfile_sha256, COMPONENTIZE_PY_VERSION, WIT_VERSION, entry
    )


def _retire(conn, key: str, wasm_uri: str) -> bool:
    """Retires the live entry of the key only while it names the wasm, which the entry's row lock
    keeps true until the entry is retired."""
    with conn.transaction():
        live = conn.execute(
            "SELECT wasm_uri FROM artifact WHERE kind = 'compile-cache' AND cache_key = %s"
            " AND deleted_at IS NULL FOR UPDATE",
            (key,),
        ).fetchone()
        if live is None or live[0] != wasm_uri:
            return False
        return isinstance(invalidate_cache_entry(conn, key, wall_now()), Invalidated)


class CompileCache:
    """Reads sources and build-infos with a store client of the `workflow` role."""

    def __init__(self, dsn: str, store: ArtifactClient):
        self.dsn = dsn
        self.store = store
        # ponytail: unbounded, one key per distinct source and entry, an LRU if that grows
        self.keys: dict[tuple[str, str], str | None] = {}

    def _key(self, request: CacheLookup) -> str | None:
        memo = (request.source.sha256, request.entry)
        if memo not in self.keys:
            with tempfile.TemporaryDirectory(prefix="wdf-cache-") as work:
                path = Path(work) / bundle.NAME
                self.store.get(request.source, path)
                self.keys[memo] = bundle_key(path, request.entry)
        return self.keys[memo]

    @activity.defn(name="cache_lookup")
    def cache_lookup(self, request: CacheLookup) -> CacheAnswer:
        """The key of the source and entry and, on a hit, the entry linked to the job.

        A fault of the store or the database is raised and is never a miss, so an outage cannot
        queue a build for every job.
        """
        with typed_errors():
            key = self._key(request)
            if key is None:
                return CacheAnswer(key=None, component=None, build_info=None)
            with connect(self.dsn, autocommit=True) as conn:
                found = cache.lookup(conn, self.store, key)
                if isinstance(found, cache.Miss):
                    # A live entry whose wasm is gone is retired, so the rebuild can record.
                    if (stale := lookup_cache_entry(conn, key)) is not None:
                        _retire(conn, key, stale.wasm_uri)
                    return CacheAnswer(key=key, component=None, build_info=None)
                cache.use(conn, found, request.job_id)
        return CacheAnswer(key=key, component=found.wasm, build_info=found.build_info)

    @activity.defn(name="cache_record")
    def cache_record(self, request: CacheRecord) -> None:
        """Records the entry of a committed compile, the first writer of a key winning.

        A build-info that is not of the key computed for the source is refused, since its
        component was built from other inputs than the key names.
        """
        try:
            with typed_errors():
                body = self.store.get_bytes(request.build_info, kind_caps()["build-info"])
            built = BuildInfo.model_validate_json(body)
        except (ValidationError, TooLarge) as error:
            raise control_error(
                "ARTIFACT_CORRUPT", f"{request.build_info.uri} is not a build-info: {error}"
            ) from error
        if built.cache_key != request.key:
            raise control_error(
                "CommitRefused",
                f"{request.build_info.uri} is of cache key {built.cache_key}, not {request.key}",
            )
        if (built.component.sha256, built.component.size) != (
            request.component.sha256,
            request.component.size,
        ):
            raise control_error(
                "CommitRefused",
                f"{request.build_info.uri} describes another component than "
                f"{request.component.uri}",
            )
        with connect(self.dsn, autocommit=True) as conn:
            recorded = cache.record(
                conn, request.component, request.build_info, request.key, VERSIONS
            )
        if isinstance(recorded, WasmGone):
            # The job holds its own links to the component, so only later jobs lose the hit.
            activity.logger.warning(
                "no cache entry for %s of job %s: its component or build-info row is gone",
                request.key,
                request.job_id,
            )

    @activity.defn(name="cache_invalidate")
    def cache_invalidate(self, request: CacheInvalidate) -> bool:
        """Retires the live entry of the key only while it names the lost component.

        The entry is locked when it is read, so an entry that another job records for the key
        before this transaction ends is never the one retired.
        """
        with connect(self.dsn, autocommit=True) as conn:
            return _retire(conn, request.key, request.component.uri)
