"""Tests for the optional client-side RPC concurrency cap in ``guarded_execute``.

``ODOO_MAX_CONCURRENT_RPC`` set to a positive integer bounds how many RPCs may
be in flight at once through the single transport chokepoint; unset or invalid
values must preserve the long-standing unlimited behavior with zero added
synchronization. The bounded test saturates the cap with blocked executors and
asserts no extra call sneaks in; the unbounded test proves genuine parallelism
by making every call rendezvous at one barrier that only passes if all threads
are inside ``execute`` simultaneously.
"""

import contextlib
import os
import threading
import time
import unittest
from typing import Any
from unittest.mock import patch

from odoo_sdk.transport import executor as executor_module
from odoo_sdk.transport.errors import DeletionNotSupportedError
from odoo_sdk.transport.executor import (
    MAX_CONCURRENT_RPC_ENV_VAR,
    OdooExecutor,
    _concurrency_gate,
    _max_concurrent_rpc,
    guarded_execute,
)


def _reset_gate_cache() -> None:
    """Reset the process-wide semaphore cache so tests never share gate state."""
    with executor_module._gate_lock:
        executor_module._gate_limit = None
        executor_module._gate_semaphore = None


class _ConcurrencyTrackingExecutor(OdooExecutor):
    """Record peak concurrent ``execute`` calls; each call blocks until released."""

    def __init__(self, release: threading.Event) -> None:
        self._release = release
        self._state_lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        with self._state_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self._release.wait(timeout=10.0)
        with self._state_lock:
            self.active -= 1
        return "ok"


class ConcurrencyCapTestCase(unittest.TestCase):
    """Base fixture: isolate both the env var and the cached semaphore per test."""

    def setUp(self) -> None:
        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop(MAX_CONCURRENT_RPC_ENV_VAR, None)
        _reset_gate_cache()
        self.addCleanup(_reset_gate_cache)


class TestMaxConcurrentRpcParsing(ConcurrencyCapTestCase):
    def test_unset_env_means_unlimited(self) -> None:
        self.assertIsNone(_max_concurrent_rpc())

    def test_positive_integer_is_parsed(self) -> None:
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "3"
        self.assertEqual(_max_concurrent_rpc(), 3)

    def test_non_integer_means_unlimited(self) -> None:
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "lots"
        self.assertIsNone(_max_concurrent_rpc())

    def test_empty_string_means_unlimited(self) -> None:
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = ""
        self.assertIsNone(_max_concurrent_rpc())

    def test_zero_means_unlimited(self) -> None:
        # A zero cap would deadlock every call; it must degrade to unlimited.
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "0"
        self.assertIsNone(_max_concurrent_rpc())

    def test_negative_means_unlimited(self) -> None:
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "-2"
        self.assertIsNone(_max_concurrent_rpc())


class TestConcurrencyGate(ConcurrencyCapTestCase):
    def test_unset_env_yields_null_gate(self) -> None:
        self.assertIsInstance(_concurrency_gate(), contextlib.nullcontext)

    def test_invalid_env_yields_null_gate(self) -> None:
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "not-a-number"
        self.assertIsInstance(_concurrency_gate(), contextlib.nullcontext)

    def test_configured_cap_yields_semaphore(self) -> None:
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "2"
        gate = _concurrency_gate()
        self.assertIsInstance(gate, threading.BoundedSemaphore)

    def test_gate_is_reused_while_limit_is_unchanged(self) -> None:
        # All callers must contend on ONE semaphore, so repeated lookups with the
        # same configured limit have to return the same instance.
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "2"
        self.assertIs(_concurrency_gate(), _concurrency_gate())

    def test_gate_is_rebuilt_when_limit_changes(self) -> None:
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "2"
        first = _concurrency_gate()
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "3"
        second = _concurrency_gate()
        self.assertIsNot(first, second)
        # The rebuilt gate must carry the NEW bound: exactly three non-blocking
        # acquires succeed and the fourth is refused.
        acquired = [second.acquire(blocking=False) for _ in range(4)]
        self.assertEqual(acquired, [True, True, True, False])
        for _ in range(3):
            second.release()


class TestGuardedExecuteConcurrency(ConcurrencyCapTestCase):
    def _spawn_calls(
        self, executor: OdooExecutor, count: int
    ) -> tuple[list[threading.Thread], list[BaseException]]:
        errors: list[BaseException] = []

        def call() -> None:
            try:
                guarded_execute(executor, "res.partner", "read", [1])
            except BaseException as exc:  # pragma: no cover - defensive capture
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(count)]
        for thread in threads:
            thread.start()
        return threads, errors

    def test_cap_bounds_in_flight_rpcs(self) -> None:
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "2"
        release = threading.Event()
        tracked = _ConcurrencyTrackingExecutor(release)

        threads, errors = self._spawn_calls(tracked, 6)
        try:
            # Wait until the cap is saturated, then hold it there long enough for
            # any over-admitted thread to reveal itself before releasing.
            deadline = time.monotonic() + 5.0
            while tracked.active < 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(tracked.active, 2)
            time.sleep(0.1)
            self.assertEqual(tracked.active, 2)
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=10.0)

        self.assertEqual(errors, [])
        self.assertEqual(tracked.max_active, 2)

    def test_unset_env_leaves_concurrency_unbounded(self) -> None:
        # All threads rendezvous at ONE barrier inside execute: the barrier only
        # passes if every call is in flight simultaneously, so any accidental
        # bounding would break the barrier (timeout) instead of silently passing.
        thread_count = 5
        rendezvous = threading.Barrier(thread_count)

        class _BarrierExecutor(OdooExecutor):
            def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
                rendezvous.wait(timeout=5.0)
                return "ok"

        threads, errors = self._spawn_calls(_BarrierExecutor(), thread_count)
        for thread in threads:
            thread.join(timeout=10.0)

        self.assertEqual(errors, [])

    def test_unlink_guard_fires_before_the_gate(self) -> None:
        # A forbidden call must be rejected before it can occupy (or block on) a
        # concurrency slot: with a cap of 1 already held elsewhere, unlink still
        # fails immediately instead of queueing.
        os.environ[MAX_CONCURRENT_RPC_ENV_VAR] = "1"
        gate = _concurrency_gate()
        self.assertTrue(gate.acquire(blocking=False))
        caught: list[BaseException] = []

        def call_unlink() -> None:
            try:
                guarded_execute(
                    _ConcurrencyTrackingExecutor(threading.Event()),
                    "res.partner",
                    "unlink",
                    [1],
                )
            except BaseException as exc:
                caught.append(exc)

        # Run in a thread so a regression (guard behind the gate) surfaces as a
        # still-alive blocked thread instead of hanging the whole test run.
        thread = threading.Thread(target=call_unlink, daemon=True)
        try:
            thread.start()
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(caught), 1)
            self.assertIsInstance(caught[0], DeletionNotSupportedError)
        finally:
            gate.release()
