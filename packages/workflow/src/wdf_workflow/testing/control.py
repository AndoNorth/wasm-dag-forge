"""The argument and result of each control activity, an in-memory fake of them, and the
conformance test that the real ones pass.

Every control activity takes one model and returns one value of the type that SIGNATURES pins,
and raises only the ApplicationError types of `error_types()`.
"""

import hashlib
import inspect
import json
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, StrictBool
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from wdf_artifactclient.keys import content_key, uri_of
from wdf_artifactclient.models import IndexEntry
from wdf_events import Event
from wdf_events.models import (
    UUID_PATTERN,
    ArtifactReference,
    CheckpointReference,
    ContentReference,
    JobParams,
    ManifestReference,
)
from wdf_jobdoc.models import SHA256_PATTERN, TASK_ID_PATTERN
from wdf_jobstate import status_vocabulary
from wdf_plan import Plan
from wdf_temporal import event_data_converter

from ..contract import artifact_codes, naming

JobId = Annotated[str, Field(pattern=UUID_PATTERN)]
TaskId = Annotated[str, Field(pattern=TASK_ID_PATTERN)]
CacheKey = Annotated[str, Field(pattern=SHA256_PATTERN)]
Attempt = Annotated[str, Field(pattern="^r[0-9]+a[0-9]+$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LoadPlan(_Model):
    manifest: ManifestReference
    params: JobParams


class LoadedPlan(_Model):
    """The bound plan and the history limit of its limits profile."""

    plan: Plan
    max_history_events: PositiveInt


class CacheLookup(_Model):
    source: ContentReference
    entry: str = Field(min_length=1)
    job_id: JobId


class CacheAnswer(_Model):
    """The key of a source and entry, null when the bundle has none, and on a hit the component
    and build-info that the hit links to the job."""

    key: CacheKey | None
    component: ContentReference | None
    build_info: ContentReference | None


class CacheRecord(_Model):
    key: CacheKey
    component: ContentReference
    build_info: ContentReference
    job_id: JobId


class CacheInvalidate(_Model):
    """Retires the live entry of the key only while it still names the lost component."""

    key: CacheKey
    component: ContentReference


class Commit(_Model):
    job_id: JobId
    task: TaskId
    attempt: Attempt
    references: list[ArtifactReference] = Field(min_length=1)


class IndexCheckpoint(_Model):
    reference: CheckpointReference
    job_id: JobId
    task: TaskId
    attempt: Attempt


class BuildIndex(_Model):
    """The recorded results of an aggregate task's inputs, whose index is put and committed."""

    job_id: JobId
    tenant: str = Field(pattern="^[^/]+$")
    task: TaskId
    partial: StrictBool
    entries: list[IndexEntry] = Field(min_length=1)


class Transition(_Model):
    expected: str
    to: str


class Emit(_Model):
    """A batch of events, written once each by id, and the status move that goes with them."""

    job_id: JobId
    events: list[Event]
    transition: Transition | None


class Emitted(_Model):
    """The number of events written, and the row's status when the move lost, in which case no
    event of the batch was written."""

    inserted: NonNegativeInt
    lost: str | None


# The argument and result type of each control activity, keyed by its name in naming.yaml.
SIGNATURES: dict[str, tuple[type, Any]] = {
    "load_plan": (LoadPlan, LoadedPlan),
    "cache_lookup": (CacheLookup, CacheAnswer),
    "cache_record": (CacheRecord, None),
    "cache_invalidate": (CacheInvalidate, bool),
    "commit": (Commit, None),
    "index_checkpoint": (IndexCheckpoint, None),
    "build_index": (BuildIndex, ContentReference),
    "emit": (Emit, Emitted),
}


@cache
def error_types() -> dict[str, bool]:
    """Each ApplicationError type a control activity may raise, with whether it is
    non-retryable: the artifact codes, a refused commit and the two state library errors."""
    return {
        **artifact_codes(),
        "CommitRefused": True,
        "IllegalTransition": True,
        "JobNotFound": True,
    }


def control_error(error_type: str, message: str) -> ApplicationError:
    """The ApplicationError of a typed error, non-retryable as error_types() says."""
    return ApplicationError(
        f"{error_type}: {message}", type=error_type, non_retryable=error_types()[error_type]
    )


def _content(kind: str, name: str, body: bytes) -> ContentReference:
    sha256 = hashlib.sha256(body).hexdigest()
    return ContentReference(
        uri=uri_of(kind, content_key(kind, sha256)),
        sha256=sha256,
        size=len(body),
        name=name,
        kind=kind,
    )


def _check_producer(reference, job_id: str, task: str, attempt: str) -> None:
    producer = getattr(reference, "producer", None)
    if producer is not None and (producer.job, producer.task, producer.attempt) != (
        job_id,
        task,
        attempt,
    ):
        raise control_error("CommitRefused", f"{reference.uri} is not of {task} {attempt}")


class FakeControl:
    """The control activities in memory, under their names in naming.yaml.

    `plans` maps a manifest uri to the plan that load_plan returns. `status` is the job row's
    status, which a test moves to make emit lose. `errors` maps an activity name to the error
    types its next calls raise, one per call. A source whose uri is in `keyless` has no cache key.
    """

    def __init__(
        self,
        plans: Mapping[str, LoadedPlan] | None = None,
        status: str | None = "admitted",
        errors: Mapping[str, list[str]] | None = None,
        keyless: set[str] | None = None,
    ):
        self.plans = dict(plans or {})
        self.status = status
        self.errors = {name: list(types) for name, types in (errors or {}).items()}
        self.keyless = set(keyless or ())
        self.cache: dict[str, tuple[ContentReference, ContentReference]] = {}
        self.uses: list[tuple[str, str]] = []
        self.committed: dict[tuple[str, str], list] = {}
        self.checkpoints: list[CheckpointReference] = []
        self.events: dict[str, Event] = {}
        self.calls: list[str] = []

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.errors.get(name):
            raise control_error(self.errors[name].pop(0), "scripted by FakeControl")

    @activity.defn(name="load_plan")
    async def load_plan(self, request: LoadPlan) -> LoadedPlan:
        self._enter("load_plan")
        if request.manifest.uri not in self.plans:
            raise control_error("ARTIFACT_LOST", f"{request.manifest.uri} is not seeded")
        return self.plans[request.manifest.uri]

    @activity.defn(name="cache_lookup")
    async def cache_lookup(self, request: CacheLookup) -> CacheAnswer:
        self._enter("cache_lookup")
        if request.source.uri in self.keyless:
            return CacheAnswer(key=None, component=None, build_info=None)
        key = hashlib.sha256(f"{request.source.sha256} {request.entry}".encode()).hexdigest()
        if key not in self.cache:
            return CacheAnswer(key=key, component=None, build_info=None)
        component, build_info = self.cache[key]
        self.uses.append((request.job_id, component.uri))
        return CacheAnswer(key=key, component=component, build_info=build_info)

    @activity.defn(name="cache_record")
    async def cache_record(self, request: CacheRecord) -> None:
        self._enter("cache_record")
        self.cache.setdefault(request.key, (request.component, request.build_info))

    @activity.defn(name="cache_invalidate")
    async def cache_invalidate(self, request: CacheInvalidate) -> bool:
        self._enter("cache_invalidate")
        live = self.cache.get(request.key)
        if live is None or live[0] != request.component:
            return False
        del self.cache[request.key]
        return True

    @activity.defn(name="commit")
    async def commit(self, request: Commit) -> None:
        self._enter("commit")
        for reference in request.references:
            _check_producer(reference, request.job_id, request.task, request.attempt)
        self.committed[(request.job_id, request.task)] = list(request.references)

    @activity.defn(name="index_checkpoint")
    async def index_checkpoint(self, request: IndexCheckpoint) -> None:
        self._enter("index_checkpoint")
        _check_producer(request.reference, request.job_id, request.task, request.attempt)
        self.checkpoints.append(request.reference)

    @activity.defn(name="build_index")
    async def build_index(self, request: BuildIndex) -> ContentReference:
        self._enter("build_index")
        tasks = [entry.task for entry in request.entries]
        if tasks != sorted(set(tasks)):
            raise control_error("OUTPUT_UPLOAD_FAILED", "the entries are not in task id order")
        body = json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()
        reference = _content("index", "index.jsonl", body)
        self.committed[(request.job_id, request.task)] = [reference]
        return reference

    @activity.defn(name="emit")
    async def emit(self, request: Emit) -> Emitted:
        self._enter("emit")
        if self.status is None:
            raise control_error("JobNotFound", f"no row for {request.job_id}")
        move = request.transition
        if move is not None:
            row_only = status_vocabulary().transitions.get((move.expected, move.to))
            if row_only is not False:
                raise control_error("IllegalTransition", f"{move.expected} to {move.to}")
            if self.status != move.expected:
                return Emitted(inserted=0, lost=self.status)
            self.status = move.to
        new = [event for event in request.events if event.id not in self.events]
        self.events.update((event.id, event) for event in new)
        return Emitted(inserted=len(new), lost=None)

    def control_activities(self) -> list[Callable]:
        """The regular activities, which the control queue of a build serves."""
        return [
            self.load_plan,
            self.cache_lookup,
            self.cache_record,
            self.cache_invalidate,
            self.commit,
            self.index_checkpoint,
            self.build_index,
        ]

    def activities(self) -> dict[str, Callable]:
        """Every control activity by name, emit included."""
        return {fn.__name__: fn for fn in [*self.control_activities(), self.emit]}


@dataclass(frozen=True)
class ControlCase:
    """One call of a control activity: the error type it must raise, or else a success, which
    for emit answers `lost` with the row's status or null."""

    activity: str
    argument: BaseModel
    raises: str | None = None
    lost: str | None = None


def _activity_name(fn: Callable) -> str | None:
    definition = activity._Definition.from_callable(fn)
    return definition.name if definition else None


def _shape_problems(name: str, fn: Callable) -> list[str]:
    if name not in SIGNATURES:
        return [f"{name}: not a control activity of naming.yaml"]
    if _activity_name(fn) != name:
        return [f"{name}: its activity name is {_activity_name(fn)}"]
    argument, result = SIGNATURES[name]
    hints = typing.get_type_hints(fn)
    returns = hints.pop("return", "missing")
    problems = []
    if list(hints.values()) != [argument]:
        problems.append(f"{name}: takes {list(hints.values())}, not one {argument.__name__}")
    if returns != (type(None) if result is None else result):
        problems.append(f"{name}: returns {returns}, not {result}")
    return problems


def _round_trips(value: Any, result: Any) -> bool:
    converter = event_data_converter.payload_converter
    [payload] = converter.to_payloads([value])
    return converter.from_payloads([payload], [result or type(None)])[0] == value


async def control_activity_contract(impl: Mapping[str, Callable], cases: list[ControlCase]) -> None:
    """Raises AssertionError listing every rule that the activities of `impl`, keyed by name,
    break over `cases`, which run in order.

    Each activity has the name and the argument and result types of SIGNATURES, and each case
    either raises its ApplicationError type, retryable as error_types() says, or returns a value
    of the result type that survives the payload converter. Every activity has a succeeding case,
    and an emit has a case whose move lost, which writes no event.
    """
    names = {a.name for a in naming().activities.control} | {
        a.name for a in naming().activities.local
    }
    assert set(SIGNATURES) <= names, f"{set(SIGNATURES) - names} are not in naming.yaml"
    problems = [p for name, fn in impl.items() for p in _shape_problems(name, fn)]
    environment = ActivityEnvironment()
    for case in cases:
        where = f"{case.activity}({type(case.argument).__name__})"
        try:
            value = environment.run(impl[case.activity], case.argument)
            if inspect.isawaitable(value):
                value = await value
        except ApplicationError as error:
            if case.raises is None:
                problems.append(f"{where}: raised {error.type}: {error}")
            elif error.type != case.raises:
                problems.append(f"{where}: raised {error.type}, not {case.raises}")
            elif error.non_retryable != error_types().get(error.type):
                problems.append(f"{where}: {error.type} has non_retryable {error.non_retryable}")
            continue
        except Exception as error:  # noqa: BLE001 - a plain exception is what the contract refuses
            problems.append(f"{where}: raised a plain {type(error).__name__}: {error}")
            continue
        if case.raises is not None:
            problems.append(f"{where}: returned {value!r}, not {case.raises}")
            continue
        result = SIGNATURES[case.activity][1]
        if not (value is None if result is None else isinstance(value, result)):
            problems.append(f"{where}: returned {value!r}, not {result}")
        elif not _round_trips(value, result):
            problems.append(f"{where}: {value!r} does not survive the payload converter")
        elif case.activity == "emit" and value.lost != case.lost:
            problems.append(f"{where}: answered lost {value.lost}, not {case.lost}")
        elif case.activity == "emit" and value.lost is not None and value.inserted:
            problems.append(f"{where}: a lost move wrote {value.inserted} events")
    for name in impl:
        if not any(c.activity == name and c.raises is None for c in cases):
            problems.append(f"{name}: no case succeeds")
    if "emit" in impl and not any(c.activity == "emit" and c.lost for c in cases):
        problems.append("emit: no case loses its move")
    assert not problems, "control activity contract:\n" + "\n".join(problems)
