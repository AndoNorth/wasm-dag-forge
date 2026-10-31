"""The replay corpus: recorded histories per release, and the runner that replays them.

A corpus file is `{"build": ..., "workflow_type": ..., "history": ...}` at
`corpus/<release>/<name>.json`.
`corpus/owners.yaml` maps a workflow type to the epic that owns its histories.

    python -m wdf_temporal.corpus record <release>
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

import yaml
from temporalio.client import Client, WorkflowHistory
from temporalio.worker import Replayer, Worker
from wdf_events import from_bytes
from wdf_events.models import JobParams

from .converter import event_data_converter
from .standin import RUNNER, TASK_QUEUE, StandInJob, run_task

PACKAGE = Path(__file__).resolve().parents[2]
CORPUS = PACKAGE / "corpus"
VECTORS = PACKAGE.parents[1] / "contracts/events/vectors/accepted/valid"

# One recorded run per accepted vector, so a Unicode attribute is in the corpus.
RECORDINGS = {"standin-plain": "accepted.json", "standin-unicode": "tracestate-and-unicode.json"}


# A fixed client and worker identity keeps the host name and process id out of a committed history.
IDENTITY = "wdf-corpus"


class CorpusError(Exception):
    """One or more histories did not replay, or could not be replayed at all."""


def build_label() -> str:
    """Names the build that recorded a history: `WDF_BUILD_LABEL`, else the short commit."""
    if label := os.environ.get("WDF_BUILD_LABEL"):
        return label
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=PACKAGE,
    ).stdout.strip()


def owners(root: Path) -> dict[str, str]:
    path = root / "owners.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def workflow_type_of(cls: type) -> str:
    return getattr(cls, "__temporal_workflow_definition").name


def histories(root: Path) -> list[Path]:
    return sorted(root.glob("*/*.json"))


async def _replay(workflows: Sequence[type], root: Path) -> list[str]:
    owned = owners(root)
    given = {workflow_type_of(cls): cls for cls in workflows}
    failures = []
    for path in histories(root):
        name = f"{path.parent.name}/{path.name}"
        document = json.loads(path.read_text())
        workflow_type = document["workflow_type"]
        if workflow_type not in given:
            if workflow_type in owned:
                failures.append(
                    f"{name}: workflow type {workflow_type} is owned by {owned[workflow_type]}"
                    " and no class for it was given to the runner"
                )
            else:
                failures.append(
                    f"{name}: workflow type {workflow_type} has no owners.yaml entry"
                    " and no class was given to the runner"
                )
            continue
        replayer = Replayer(
            workflows=[given[workflow_type]],
            workflow_runner=RUNNER,
            data_converter=event_data_converter,
        )
        try:
            await replayer.replay_workflow(
                WorkflowHistory.from_json(path.stem, document["history"])
            )
        except Exception as exc:
            failures.append(f"{name} (build {document.get('build')}): {type(exc).__name__}: {exc}")
    return failures


def replay_corpus(workflows: Sequence[type], root: Path = CORPUS) -> int:
    """Replays every history under `root` with the classes given and returns how many replayed.

    Raises CorpusError, naming each history and its owner, for any that failed or had no class.
    A history is never skipped.
    """
    failures = asyncio.run(_replay(workflows, root))
    if failures:
        raise CorpusError("\n".join(failures))
    return len(histories(root))


async def _record(port: int, release: Path) -> list[Path]:
    client = None
    for _ in range(120):
        try:
            client = await Client.connect(
                f"127.0.0.1:{port}", data_converter=event_data_converter, identity=IDENTITY
            )
            break
        except RuntimeError:
            await asyncio.sleep(0.5)
    if client is None:
        raise RuntimeError("the Temporal server did not answer")
    written = []
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[StandInJob],
        activities=[run_task],
        workflow_runner=RUNNER,
        identity=IDENTITY,
    ):
        for name, vector in RECORDINGS.items():
            accepted = from_bytes((VECTORS / vector).read_bytes())
            handle = await client.start_workflow(
                StandInJob.run,
                args=[accepted, JobParams(seed=7, parameters={"label": "recorded"})],
                id=f"corpus-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )
            await handle.result()
            history = await handle.fetch_history()
            path = release / f"{name}.json"
            path.write_text(
                json.dumps(
                    {
                        "build": build_label(),
                        "workflow_type": workflow_type_of(StandInJob),
                        "history": json.loads(history.to_json()),
                    },
                    indent=1,
                    sort_keys=True,
                )
                + "\n"
            )
            written.append(path)
    return written


def record(release: str, root: Path = CORPUS) -> list[Path]:
    """Records the stand-in runs of the current code into `root/<release>/`, in one T1 container."""
    from wdf_itest import Session

    directory = root / release
    directory.mkdir(parents=True, exist_ok=True)
    with Session() as t1:
        with t1.container(
            "temporalio/temporal",
            ports={0: 7233},
            command=("server", "start-dev", "--ip", "0.0.0.0", "--headless"),
        ) as container:
            container.wait_ready(0)
            return asyncio.run(_record(container.host_ports[0], directory))


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "record":
        print("usage: python -m wdf_temporal.corpus record <release>", file=sys.stderr)
        return 2
    for path in record(argv[2]):
        print(path.relative_to(PACKAGE))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
