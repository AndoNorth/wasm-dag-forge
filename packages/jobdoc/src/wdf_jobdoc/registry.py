"""The task type registry and the aggregation registry, exported as contracts/job/registry.json.

A type fixes the pool its tasks run in, the kinds of in-job inputs it accepts and the outputs it
declares, so the document never names a pool.
An aggregation fixes the arguments it takes and their types.
"""

# The types a parameter declaration and a placeholder position can have.
PARAMETER_TYPES = ("integer", "string", "boolean", "seed", "string-list")

# `outputs` maps an output name to the artifact kind it carries.
# `inputs.accepts` lists the artifact kinds an in-job input may carry, `min` and `max` bound their
# count, and a `max` of null is unbounded.
TASK_TYPES = {
    "compile": {
        "pool": "compile",
        "inputs": {"accepts": [], "min": 0, "max": 0},
        "outputs": {"component": "component", "build-info": "build-info"},
    },
    "simulate": {
        "pool": "simulate",
        "inputs": {"accepts": ["component"], "min": 1, "max": 1},
        "outputs": {"result": "result"},
    },
    "aggregate": {
        "pool": "aggregate",
        "inputs": {"accepts": ["result"], "min": 0, "max": None},
        "outputs": {"summary": "summary"},
    },
}

# `args` maps an argument name to its parameter type and whether it is required.
AGGREGATIONS = {
    "summary_stats": {
        "args": {
            "blocks": {"type": "string-list", "required": False},
            "columns": {"type": "string-list", "required": False},
        },
        "reads": "result",
        "writes": "summary",
    },
    "digest_list": {"args": {}, "reads": "result", "writes": "summary"},
}

REGISTRY = {
    "parameter_types": list(PARAMETER_TYPES),
    "task_types": TASK_TYPES,
    "aggregations": AGGREGATIONS,
}
