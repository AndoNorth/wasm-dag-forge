"""SplitMix64, the one generator behind every value a workload writes."""

MASK = (1 << 64) - 1
GAMMA = 0x9E3779B97F4A7C15


class SplitMix64:
    """The stream of 64-bit values that a seed starts, the same on every Python."""

    def __init__(self, seed: int):
        self.state = seed & MASK

    def next(self) -> int:
        self.state = (self.state + GAMMA) & MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
        return z ^ (z >> 31)

    def float(self) -> float:
        """A float in [0, 1) from the top 53 bits of the next value."""
        return (self.next() >> 11) * 2.0**-53

    def bytes(self, size: int) -> bytes:
        """size bytes taken from the next values, each written big endian."""
        words = (size + 7) // 8
        return b"".join(self.next().to_bytes(8, "big") for _ in range(words))[:size]
