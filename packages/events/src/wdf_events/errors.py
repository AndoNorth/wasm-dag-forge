"""Typed failures of the event library.

`reason` is the stable code that check.sh prints and metrics label on.
`dlq_reason` is the application dead letter reason a broker consumer settles with.
`metric_reason` is the value of the `reason` label of `wdf_event_rejected_total`.
"""


class EventError(Exception):
    dlq_reason = "MALFORMED"
    metric_reason = "schema-invalid"

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class UnsupportedMajor(EventError):
    dlq_reason = "UNSUPPORTED_VERSION"
    metric_reason = "unsupported-major"


class SchemaInvalid(EventError):
    dlq_reason = "MALFORMED"


class EventTooLarge(EventError):
    dlq_reason = "MALFORMED"
    metric_reason = "event-too-large"
