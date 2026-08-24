import contextlib
import os
import threading
from abc import ABC
from typing import Any, ContextManager, Optional

from odoo_sdk.transport.errors import forbid_unlink

#: Environment variable naming the optional client-side RPC concurrency cap.
#: A positive integer bounds how many :func:`guarded_execute` calls may be
#: in flight at once; unset, non-integer, zero, or negative values all mean
#: unlimited so existing deployments keep their current behavior.
MAX_CONCURRENT_RPC_ENV_VAR = "ODOO_MAX_CONCURRENT_RPC"

# The semaphore is process-wide state guarded by its own lock: every executor in
# the process fans out against the same Odoo instance, so the cap must be shared
# across executors rather than per-instance. The limit it was built with is
# cached alongside it so a changed environment value rebuilds the semaphore
# instead of silently keeping the stale bound.
_gate_lock = threading.Lock()
_gate_limit: Optional[int] = None
_gate_semaphore: Optional[threading.BoundedSemaphore] = None


def _max_concurrent_rpc() -> Optional[int]:
    """Parse :data:`MAX_CONCURRENT_RPC_ENV_VAR` into a positive cap, else ``None``.

    Unset, non-integer, zero, and negative values all return ``None`` (unlimited)
    because a misconfigured cap must degrade to the long-standing default rather
    than deadlock or reject calls.
    """
    raw = os.environ.get(MAX_CONCURRENT_RPC_ENV_VAR)
    if raw is None:
        return None
    try:
        limit = int(raw)
    except ValueError:
        return None
    if limit <= 0:
        return None
    return limit


def _concurrency_gate() -> ContextManager[Any]:
    """Return the context manager bounding one RPC's execution.

    With no configured cap this is a :func:`contextlib.nullcontext` — zero
    synchronization, preserving the historical unlimited behavior. With a cap it
    is a process-wide :class:`threading.BoundedSemaphore` of that size, rebuilt
    only when the configured limit changes so concurrent callers all contend on
    the same instance.
    """
    limit = _max_concurrent_rpc()
    if limit is None:
        return contextlib.nullcontext()
    global _gate_limit, _gate_semaphore
    with _gate_lock:
        if _gate_semaphore is None or _gate_limit != limit:
            _gate_limit = limit
            _gate_semaphore = threading.BoundedSemaphore(limit)
        return _gate_semaphore


class OdooExecutor(ABC):
    """Define the minimal execution contract shared by SDK facade objects.

    The executor interface is necessary because models, queries, clients, and test
    doubles all need one stable way to issue Odoo operations without depending on a
    specific transport implementation.
    """

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one method on an Odoo model; concrete executors must override."""
        raise NotImplementedError("Subclasses must implement `execute`")


def guarded_execute(
    executor: OdooExecutor, model: str, method: str, *args: Any, **kwargs: Any
) -> Any:
    """Route one model-method call through the single guarded transport seam.

    This gateway is the ONE chokepoint every model-method call crosses. Both
    :meth:`OdooClient.execute` and :meth:`OdooRecordset._execute` delegate here so
    the cross-cutting :func:`forbid_unlink` guard is applied in exactly one place,
    before any executor delegation — so even an injected test executor cannot let
    an explicit ``unlink`` through.

    Being the one chokepoint also makes it the right place for the optional
    client-side concurrency cap: when :data:`MAX_CONCURRENT_RPC_ENV_VAR` is set
    to a positive integer, at most that many calls execute at once; otherwise
    execution is unbounded exactly as before. The ``unlink`` guard runs before
    the gate so a forbidden call never occupies a concurrency slot.

    :raises DeletionNotSupportedError: When ``method`` is ``unlink``.
    """
    forbid_unlink(method)
    with _concurrency_gate():
        return executor.execute(model, method, *args, **kwargs)
