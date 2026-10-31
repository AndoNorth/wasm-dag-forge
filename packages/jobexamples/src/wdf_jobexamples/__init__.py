"""The example library, the parameter vectors and the generator."""

__all__ = ["examples", "generate", "load", "seed_list"]


def examples():
    raise NotImplementedError("not implemented: owned by lane F")


def load(name):
    raise NotImplementedError("not implemented: owned by lane F")


def generate(shape, chains, **kw):
    raise NotImplementedError("not implemented: owned by lane F")


def seed_list(count, start=0):
    raise NotImplementedError("not implemented: owned by lane F")
