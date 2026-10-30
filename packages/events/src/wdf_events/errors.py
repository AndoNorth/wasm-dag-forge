"""Typed failures of the event library.

`reason` is the stable code that check.sh prints and metrics label on.
`dlq_reason` is the application dead letter reason a broker consumer settles with.
"""


class EventError(Exception):
    dlq_reason = "MALFORMED"

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class UnsupportedMajor(EventError):
    dlq_reason = "UNSUPPORTED_VERSION"


class SchemaInvalid(EventError):
    dlq_reason = "MALFORMED"


class EventTooLarge(EventError):
    dlq_reason = "MALFORMED"
