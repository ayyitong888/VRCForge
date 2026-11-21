"""Wait on the result of a previously started tool operation."""

from time import monotonic


def await_tool_observation(read, wait, *, is_pending, timeout, interval, clock=monotonic, initial=None):
    """Return the last observation and whether the caller's wait budget expired.

    The caller binds the read to the original operation. A false result from
    wait leaves cancellation to its owner and preserves the last observation.
    Pass timeout=None when the operation's supervisor owns the deadline.
    """
    deadline = None if timeout is None else clock() + timeout
    payload = read() if initial is None else initial
    while is_pending(payload):
        remaining = interval if deadline is None else deadline - clock()
        if remaining <= 0:
            return payload, True
        if not wait(min(interval, remaining)):
            return payload, False
        if deadline is not None and clock() >= deadline:
            return payload, True
        payload = read()
    return payload, False
