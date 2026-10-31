"""How long a previous major stays readable, and the catalogue rule that keeps the window honest."""

from datetime import date

# Placeholders until the operator sets real values.
MAX_JOB_DURATION_DAYS = 7
DEAD_LETTER_RETENTION_DAYS = 14


def readable_days() -> int:
    """Days a previous major stays readable after its last producer stopped emitting it.

    A message can be dead lettered on the last day, replayed on the last day of its retention and
    then start a job that runs for the maximum duration.
    """
    return DEAD_LETTER_RETENTION_DAYS + MAX_JOB_DURATION_DAYS


def retired(introduced: str, today: date) -> bool:
    """Whether the major before one introduced on this ISO date has outlived its window."""
    return (today - date.fromisoformat(introduced)).days > readable_days()


def check_catalogue(introduced: dict[tuple[str, int], str]) -> list[str]:
    """The majors introduced too soon after the one before, as one message each.

    A major introduced no later than the window after its predecessor could meet a reader that
    still has to read the major two steps back, so the gap has to exceed the window.
    """
    problems = []
    for name, major in sorted(introduced):
        before = introduced.get((name, major - 1))
        if before is None:
            continue
        gap = (date.fromisoformat(introduced[(name, major)]) - date.fromisoformat(before)).days
        if gap <= readable_days():
            problems.append(
                f"{name} v{major} is introduced {gap} days after v{major - 1}, "
                f"the window is {readable_days()} days"
            )
    return problems
