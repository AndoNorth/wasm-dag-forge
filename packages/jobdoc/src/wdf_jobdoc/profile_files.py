"""The one place that reads the limits file and the slots files: `load_profile`."""

from pathlib import Path

import yaml

from .profile import Limits, LimitsFile, LimitsProfile, pool_table


def load_profile(preset: str, root: Path, slots_dir: Path | None = None) -> LimitsProfile:
    """The limits profile `preset`, with the pool table of its slots file.

    `root` is the repository root.
    `slots_dir` replaces `deploy/presets/slots`, and tests pass it to read the slots fixture.
    The `permissive` profile reads the slots of `ci`, since its ceilings need no preset.
    Raises SlotsIncomplete naming the pool when the slots file lacks one.
    """
    root = Path(root)
    limits = LimitsFile.model_validate(
        yaml.safe_load((root / "contracts/job/limits.yaml").read_text())
    )
    if preset not in limits.profiles:
        raise ValueError(
            f"unknown profile {preset!r}, the profiles are {', '.join(limits.profiles)}"
        )
    slots_file = (
        slots_dir or root / "deploy/presets/slots"
    ) / f"{'ci' if preset == 'permissive' else preset}.yaml"
    pools = pool_table(yaml.safe_load(slots_file.read_text()))
    numbers: Limits = limits.profiles[preset]
    return LimitsProfile(name=preset, pools=pools, **numbers.model_dump())
