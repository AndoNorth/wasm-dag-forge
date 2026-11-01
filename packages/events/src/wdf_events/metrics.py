"""In-process instruments of the event contract, as contracts/instrumentation pins them.

An exporter reads `registry`, and nothing here depends on one.
"""

from collections import Counter
from dataclasses import dataclass, field

import yaml

from .profile import CONTRACT

_PINNED = yaml.safe_load((CONTRACT / "metrics.yaml").read_text())["metrics"]


@dataclass
class Histogram:
    bounds: list[float]
    counts: list[int] = field(default_factory=list)
    total: float = 0
    count: int = 0

    def __post_init__(self):
        self.counts = [0] * len(self.bounds)

    def observe(self, value: float) -> None:
        self.total += value
        self.count += 1
        for i, bound in enumerate(self.bounds):
            if value <= bound:
                self.counts[i] += 1


class Registry:
    def __init__(self):
        self.counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self.histograms = {
            name: Histogram(list(spec["buckets"]))
            for name, spec in _PINNED.items()
            if spec["type"] == "histogram"
        }

    def inc(self, name: str, **labels: str) -> None:
        if set(labels) != set(_PINNED[name]["labels"]):
            raise ValueError(f"{name} takes the labels {sorted(_PINNED[name]['labels'])}")
        self.counters[(name, tuple(sorted(labels.items())))] += 1

    def count(self, name: str, **labels: str) -> int:
        return self.counters[(name, tuple(sorted(labels.items())))]

    def observe(self, name: str, value: float) -> None:
        self.histograms[name].observe(value)


registry = Registry()
