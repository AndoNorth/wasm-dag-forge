"""The toolchain versions and guest memory figures that a cache key and a guest run depend on,
importable without componentize-py or wasmtime installed."""

from .contract import wit_version

COMPONENTIZE_PY_VERSION = "0.25.1"
WIT_VERSION = wit_version()
WASMTIME_VERSION = "48.0.7"
# What wasmtime holds beyond the guest's linear memory in a warm run. It holds only when a separate
# warm-up process has filled the module cache, because a cold first run keeps its compile memory.
GUEST_OVERHEAD_MIB = 128
# The initial memory, in 64 KiB pages, that the workload component declares. A guest cap below it
# fails at instantiation, so the runtime refuses it before starting wasmtime.
GUEST_MIN_PAGES = 208
