"""Identity and cache key derivation, pinned by contracts/events/vectors/keys.json.

The formulas are written in contracts/events/README.md.
"""

import uuid

__all__ = [
    "attempt_identity",
    "batch_id",
    "cloudevent_id",
    "compile_cache_key",
    "job_id",
    "workflow_id",
]

NOT_IMPLEMENTED = "not implemented: owned by lane K"

NAMESPACE = uuid.UUID("9a3dcb38-1d98-5483-9f76-afc1a01e3f49")


def _frame(domain, *fields):
    """Length-prefix every value, so no field boundary can move without changing the bytes."""
    return "".join(f"{len(value.encode())}:{value}" for value in (domain, *fields)).encode()


def _derive(domain, *fields):
    return str(uuid.uuid5(NAMESPACE, _frame(domain, *fields)))


def job_id(tenant, submission_key):
    return _derive("job", tenant, submission_key)


def batch_id(tenant, batch_key):
    return _derive("batch", tenant, batch_key)


def workflow_id(job_id):
    return f"job-{job_id}"


def cloudevent_id(job_id, task_key, attempt_identity, kind, sequence):
    if sequence < 0:
        raise ValueError(f"sequence must be 0 or more, got {sequence}")
    return _derive("event", job_id, task_key, attempt_identity, kind, str(sequence))


def attempt_identity(activity_attempt, resume_counter):
    if activity_attempt < 1:
        raise ValueError(f"activity_attempt must be 1 or more, got {activity_attempt}")
    if resume_counter < 0:
        raise ValueError(f"resume_counter must be 0 or more, got {resume_counter}")
    return f"r{resume_counter}a{activity_attempt}"


def compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version):
    raise NotImplementedError(NOT_IMPLEMENTED)
