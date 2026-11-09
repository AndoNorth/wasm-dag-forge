"""One results.xml read as parsed rows, refused when it breaks wdf.sim.v1."""


class ParseFailed(ValueError):
    """A result that is not well formed or breaks wdf.sim.v1, naming its task."""


def read_result(task, path):
    raise NotImplementedError("pending the kernel reader")
