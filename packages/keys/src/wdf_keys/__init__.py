"""Identity and cache key derivation, pinned by contracts/events/vectors/keys.json.

The formulas are written in contracts/events/README.md.
"""

__all__ = [
    "attempt_identity",
    "batch_id",
    "cloudevent_id",
    "compile_cache_key",
    "job_id",
    "workflow_id",
]

NOT_IMPLEMENTED = "not implemented: owned by lane K"


def job_id(tenant, submission_key):
    raise NotImplementedError(NOT_IMPLEMENTED)


def batch_id(tenant, batch_key):
    raise NotImplementedError(NOT_IMPLEMENTED)


def workflow_id(job_id):
    raise NotImplementedError(NOT_IMPLEMENTED)


def cloudevent_id(job_id, task_key, attempt_identity, kind, sequence):
    raise NotImplementedError(NOT_IMPLEMENTED)


def attempt_identity(activity_attempt, resume_counter):
    raise NotImplementedError(NOT_IMPLEMENTED)


def compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version):
    raise NotImplementedError(NOT_IMPLEMENTED)
