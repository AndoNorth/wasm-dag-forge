"""The error that every stub raises until the lane that owns it delivers the real behaviour."""


def not_implemented(lane: str) -> NotImplementedError:
    return NotImplementedError(f"not implemented: owned by lane {lane}")
