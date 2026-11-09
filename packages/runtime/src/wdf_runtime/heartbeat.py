"""The heartbeats of a guest's stderr, the progress lines of the guest contract."""

from collections.abc import Iterable, Iterator

from .contract import Refused, heartbeat


def beats(lines: Iterable[str]) -> Iterator[dict]:
    """The progress of each heartbeat line. A broken heartbeat is not counted, and free output is
    every line that is not a heartbeat."""
    for line in lines:
        try:
            beat = heartbeat(line.rstrip("\r\n"))
        except Refused:
            continue
        if beat is not None:
            yield beat
