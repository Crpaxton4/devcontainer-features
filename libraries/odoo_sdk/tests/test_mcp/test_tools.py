"""Tests for explicit MCP tools that compose commands with ctx.elicit/sample."""

import asyncio
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from odoo_sdk.mcp.tools import build_explicit_tools
from odoo_sdk.mcp.tools.start_task import make_start_task_tool
from odoo_sdk.mcp.tools.stop_task import make_stop_task_tool

_SP_PATCH = "odoo_sdk.mcp.tools.start_task.subprocess"


def _run(coro):
    return asyncio.run(coro)


def _accepted(data) -> MagicMock:
    r = MagicMock()
    r.action = "accept"
    r.data = data
    return r


def _confirmed() -> MagicMock:
    """A data-less accepted elicitation (pure confirm via the ``_ConfirmGate``
    fieldless schema — the accept action itself is the answer)."""
    r = MagicMock()
    r.action = "accept"
    r.data = None
    return r


def _cancelled() -> MagicMock:
    r = MagicMock()
    r.action = "cancel"
    return r


def _declined() -> MagicMock:
    r = MagicMock()
    r.action = "decline"
    return r


def _make_sp(
    current_branch="main",
    branches=("main",),
    dirty=False,
    dirty_kind="tracked",
    existing_branches=(),
    remote_branches=(),
) -> MagicMock:
    """Fake ``subprocess`` for the git helpers in ``start_task``.

    :param current_branch: Value returned by ``git rev-parse --abbrev-ref HEAD``.
    :param branches: Local branches listed by ``git branch``.
    :param dirty: Whether ``git status --porcelain`` reports changes.
    :param dirty_kind: ``"tracked"`` (``git stash push`` creates an entry) or
        ``"untracked"`` (only ``push -u`` creates an entry).
    :param existing_branches: Local branch names for which ``git rev-parse
        --verify refs/heads/<b>`` (the ``_branch_exists`` probe) reports success.
    :param remote_branches: Base names for which ``git rev-parse --verify
        refs/remotes/origin/<b>`` reports success — i.e. an ``origin/<b>``
        remote-tracking ref exists after ``git fetch`` (#454).
    """
    sp = MagicMock()
    state = {"stash_entries": 0}

    def _r(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        if args[1] == "rev-parse" and "--verify" in args:
            spec = args[-1]
            ref = spec.rsplit("/", 1)[-1]
            known = remote_branches if spec.startswith("refs/remotes/") else existing_branches
            r.returncode = 0 if ref in known else 1
        elif args[1] == "rev-parse":
            r.stdout = f"{current_branch}\n"
        elif args[1] == "branch":
            r.stdout = "".join(f"{b}\n" for b in branches)
        elif args[1] == "status":
            r.stdout = "?? f.py\n" if dirty else ""
        elif args[1:3] == ["stash", "list"]:
            r.stdout = "".join(
                f"stash@{{{i}}}: entry\n" for i in range(state["stash_entries"])
            )
        elif args[1:3] == ["stash", "push"]:
            # Plain push saves nothing for untracked-only trees; -u carries them.
            if dirty_kind == "tracked" or "-u" in args:
                state["stash_entries"] += 1
        elif args[1:3] == ["stash", "pop"]:
            if state["stash_entries"] == 0:
                r.returncode = 1
            else:
                state["stash_entries"] -= 1
        return r

    sp.run.side_effect = _r
    return sp


class _FakeRegistry:
    """Minimal registry: maps command name -> object with execute()."""

    def __init__(self, client=None, **commands):
        self._client = client if client is not None else MagicMock()
        self._commands = {}
        for name, fn in commands.items():
            self._commands[name] = fn

    def __getitem__(self, name):
        cmd = MagicMock()
        impl = self._commands[name]
        cmd.execute.side_effect = impl
        cmd._client = self._client
        return cmd


def _search_projects_returning(*results):
    it = iter(results)

    def _fn(query, limit=10):
        return next(it)

    return _fn


def _ctx(*responses) -> MagicMock:
    ctx = MagicMock()
    ctx.elicit = AsyncMock(side_effect=list(responses))
    return ctx


class TestBuildExplicitTools(unittest.TestCase):
    def test_builds_full_tool_surface(self):
        from odoo_sdk.commands import Registry
        from odoo_sdk.commands.builtin import BUILTIN_COMMANDS, register_builtins

        registry = register_builtins(Registry(MagicMock()))
        tools = build_explicit_tools(registry)
        # The MCP surface is a subset of the builtin surface, not a mirror of it:
        # MCP names its tools explicitly, so a builtin the LLM has no use for
        # (``get_employee_id``, used by the unattended export path) registers as
        # a command without becoming a tool (#499).
        self.assertLessEqual(set(tools), set(BUILTIN_COMMANDS))
        self.assertNotIn("get_employee_id", tools)
        # Every tool carries a non-empty description sourced from its command,
        # so no tool ships to the MCP wire schema without documentation.
        for name, (_, description) in tools.items():
            self.assertNotEqual(description, "", f"{name} has an empty description")
        # Each entry is a (callable, description) pair.
        start_fn, start_desc = tools["start_task"]
        stop_fn, _ = tools["stop_task"]
        self.assertTrue(asyncio.iscoroutinefunction(start_fn))
        self.assertTrue(asyncio.iscoroutinefunction(stop_fn))
        self.assertIn("track", start_desc.lower())

    def test_description_empty_when_command_missing(self):
        class _EmptyRegistry:
            def __getitem__(self, name):
                raise KeyError(name)

        tools = build_explicit_tools(_EmptyRegistry())
        # Tools are still built; descriptions default to empty string.
        _, desc = tools["get_uid"]
        self.assertEqual(desc, "")


class TestStartTaskTool(unittest.TestCase):
    def _registry(self, *, projects, tasks, start_result):
        return _FakeRegistry(
            search_projects=lambda query, limit=10: projects,
            search_tasks=lambda query, project_id, limit=10: tasks,
            start_task=lambda **kw: {**start_result, **kw},
        )

    def test_single_project_and_task_confirmed(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Accounting"}],
            tasks=[{"id": 10, "name": "Fix VAT"}],
            start_result={"run_id": 1, "task_id": 10},
        )
        ctx = _ctx(
            _confirmed(),  # confirm
            _accepted(MagicMock(selection=1)),  # branch pick
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool("VAT", ctx, "Accounting"))
        self.assertEqual(result["task_id"], 10)
        self.assertEqual(result["project_name"], "Accounting")

    def test_no_projects_returns_error(self):
        reg = self._registry(projects=[], tasks=[], start_result={})
        tool = make_start_task_tool(reg)
        result = _run(tool("x", MagicMock(), "Nope"))
        self.assertIn("error", result)
        self.assertIn("No projects", result["error"])

    def test_declined_confirmation_cancels(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}],
            tasks=[{"id": 10, "name": "T"}],
            start_result={},
        )
        ctx = _ctx(_declined())
        tool = make_start_task_tool(reg)
        result = _run(tool("T", ctx))
        self.assertEqual(result, {"error": "Task start cancelled."})

    def test_cancelled_confirmation_cancels(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}],
            tasks=[{"id": 10, "name": "T"}],
            start_result={},
        )
        ctx = _ctx(_cancelled())
        tool = make_start_task_tool(reg)
        result = _run(tool("T", ctx))
        self.assertEqual(result, {"error": "Task start cancelled."})

    def test_confirmation_is_a_dataless_gate(self):
        # The confirm prompt must be a single accept/decline checkpoint, not a
        # form with a ``confirmed`` field: it is elicited with the fieldless
        # ``_ConfirmGate`` schema and accepting proceeds even though ``data``
        # carries no user-entered value (issue #121).
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}],
            tasks=[{"id": 10, "name": "Fix"}],
            start_result={"run_id": 1, "task_id": 10},
        )
        ctx = _ctx(_confirmed(), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool("Fix", ctx))
        self.assertEqual(result["task_id"], 10)
        confirm_call = ctx.elicit.await_args_list[0]
        schema_cls = confirm_call.args[1]
        # Confirm gate uses the fieldless _ConfirmGate schema, not response_type=None.
        self.assertEqual(schema_cls.__name__, "_ConfirmGate")
        self.assertIsNone(confirm_call.kwargs.get("response_type"))
        # The schema exposes no properties the user must fill (single Accept/Decline).
        self.assertEqual(schema_cls.model_json_schema().get("properties", {}), {})
        self.assertNotIn("required", schema_cls.model_json_schema())

    def test_confirmation_schema_emits_no_deprecation_warning(self):
        # Driving the real Context.elicit with the confirm gate's schema must
        # NOT trigger the FastMCPDeprecationWarning that response_type=None does
        # in FastMCP 3.4.x. This guards the exact schema start_task passes.
        from fastmcp.exceptions import FastMCPDeprecationWarning
        from fastmcp.server.context import Context
        from odoo_sdk.mcp.tools.start_task import _ConfirmGate

        session = MagicMock()
        session.elicit = AsyncMock(
            return_value=SimpleNamespace(action="accept", content={})
        )
        ctx = MagicMock(spec=Context)
        ctx.is_background_task = False
        ctx.request_id = "1"
        ctx.session = session

        def _deprecations(coro):
            with warnings.catch_warnings(record=True) as recorded:
                warnings.simplefilter("always")
                result = _run(coro)
            return result, [
                w
                for w in recorded
                if issubclass(w.category, FastMCPDeprecationWarning)
            ]

        gate_result, gate_deprecations = _deprecations(
            Context.elicit(ctx, "Confirm?", _ConfirmGate)
        )
        _, none_deprecations = _deprecations(Context.elicit(ctx, "Confirm?", None))

        self.assertEqual(gate_result.action, "accept")
        # The confirm gate schema raises no deprecation warning ...
        self.assertEqual(gate_deprecations, [])
        # ... while the deprecated response_type=None sentinel still does,
        # proving the test would catch a regression back to it.
        self.assertTrue(none_deprecations, "response_type=None should still warn")

    def test_disambiguates_multiple_projects(self):
        reg = self._registry(
            projects=[{"id": 1, "name": "HR"}, {"id": 2, "name": "Acct"}],
            tasks=[{"id": 10, "name": "Fix VAT"}],
            start_result={"run_id": 1, "task_id": 10},
        )
        ctx = _ctx(
            _accepted(MagicMock(selection=2)),  # pick project
            _confirmed(),  # confirm
            _accepted(MagicMock(selection=1)),  # branch pick
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool("VAT", ctx))
        self.assertEqual(result["task_id"], 10)

    def test_cancelled_project_selection_errors(self):
        reg = self._registry(
            projects=[{"id": 1, "name": "HR"}, {"id": 2, "name": "IT"}],
            tasks=[],
            start_result={},
        )
        ctx = _ctx(_cancelled())
        tool = make_start_task_tool(reg)
        result = _run(tool("x", ctx))
        self.assertIn("error", result)

    def test_no_tasks_returns_error(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}], tasks=[], start_result={}
        )
        ctx = MagicMock()
        tool = make_start_task_tool(reg)
        result = _run(tool("x", ctx))
        self.assertIn("error", result)

    def test_task_id_bypasses_name_search(self):
        client = MagicMock()
        client.execute.return_value = [
            {"id": 10, "name": "Fix VAT", "project_id": [5, "Accounting"]}
        ]
        called = {"search": False}

        def _search(*a, **k):
            called["search"] = True
            return []

        reg = _FakeRegistry(
            client=client,
            search_projects=_search,
            search_tasks=_search,
            start_task=lambda **kw: {"run_id": 1, **kw},
        )
        ctx = _ctx(
            _confirmed(),
            _accepted(MagicMock(selection=1)),
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool("Fix VAT", ctx, task_id=10))
        self.assertFalse(called["search"])
        self.assertEqual(result["project_name"], "Accounting")

    def test_task_id_not_found_no_query_errors(self):
        client = MagicMock()
        client.execute.return_value = []
        reg = _FakeRegistry(
            client=client,
            search_projects=lambda *a, **k: [],
            search_tasks=lambda *a, **k: [],
            start_task=lambda **kw: {},
        )
        result = _run(make_start_task_tool(reg)("", MagicMock(), task_id=999))
        self.assertIn("999", result["error"])

    def test_task_id_fallback_to_name_search_warns(self):
        client = MagicMock()
        client.execute.return_value = []  # id lookup fails
        reg = _FakeRegistry(
            client=client,
            search_projects=lambda *a, **k: [{"id": 5, "name": "Acct"}],
            search_tasks=lambda *a, **k: [{"id": 10, "name": "Fix"}],
            start_task=lambda **kw: {"run_id": 1, **kw},
        )
        ctx = _ctx(
            _confirmed(),
            _accepted(MagicMock(selection=1)),
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool("Fix", ctx, task_id=99))
        self.assertIn("warning", result)

    def test_skips_branch_when_already_on_task_branch(self):
        client = MagicMock()
        client.execute.return_value = [
            {"id": 10, "name": "Fix", "project_id": [5, "Acct"]}
        ]
        reg = _FakeRegistry(
            client=client,
            search_projects=lambda *a, **k: [],
            search_tasks=lambda *a, **k: [],
            start_task=lambda **kw: {"run_id": 1, **kw},
        )
        ctx = _ctx(_confirmed())
        ctx.sample = AsyncMock()
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp(current_branch="10#x")):
            result = _run(tool("Fix", ctx, task_id=10))
        ctx.sample.assert_not_called()
        self.assertEqual(ctx.elicit.call_count, 1)
        self.assertIsNone(result.get("branch_name"))

    def test_branch_selection_cancelled(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}],
            tasks=[{"id": 10, "name": "Fix"}],
            start_result={},
        )
        ctx = _ctx(_confirmed(), _cancelled())
        ctx.sample = AsyncMock()
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool("Fix", ctx))
        self.assertEqual(result, {"error": "Branch selection cancelled."})

    def test_no_branches_available_errors(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}],
            tasks=[{"id": 10, "name": "Fix"}],
            start_result={},
        )
        ctx = _ctx(_confirmed())
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp(branches=())):
            result = _run(tool("Fix", ctx))
        self.assertIn("No local git branches", result["error"])

    def test_auto_stashes_dirty_tree(self):
        client = MagicMock()
        client.execute.return_value = [
            {"id": 10, "name": "Fix", "project_id": [5, "Acct"]}
        ]
        reg = _FakeRegistry(
            client=client,
            search_projects=lambda *a, **k: [],
            search_tasks=lambda *a, **k: [],
            start_task=lambda **kw: {"run_id": 1, **kw},
        )
        ctx = _ctx(
            _confirmed(),
            _accepted(MagicMock(selection=1)),
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        sp = _make_sp(dirty=True)
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            _run(tool("Fix", ctx, task_id=10))
        called = [c.args[0] for c in sp.run.call_args_list]
        # push must carry untracked files (-u) so the balanced pop has an entry.
        self.assertTrue(any(c[:3] == ["git", "stash", "push"] and "-u" in c for c in called))
        self.assertIn(["git", "stash", "pop"], called)

    def test_rolls_back_branch_when_start_command_fails(self):
        # #164: a branch created this run must be undone (switch back + delete)
        # when the start command raises, and the error must propagate.
        def _boom(**kw):
            raise RuntimeError("odoo unreachable")

        reg = _FakeRegistry(
            search_projects=lambda *a, **k: [{"id": 5, "name": "Acct"}],
            search_tasks=lambda *a, **k: [{"id": 10, "name": "Fix"}],
            start_task=_boom,
        )
        ctx = _ctx(_confirmed(), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        sp = _make_sp(current_branch="main")
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(RuntimeError):
                _run(tool("Fix", ctx))
        called = [c.args[0] for c in sp.run.call_args_list]
        # Switched back to the original branch, then deleted the task branch.
        self.assertIn(["git", "checkout", "main"], called)
        self.assertIn(["git", "branch", "-D", "10#fix"], called)

    def test_rolls_back_and_reraises_typed_already_running(self):
        # Raise-based error contract (#223): the epic-C start command raises the
        # typed ``TaskAlreadyRunningError`` when the task is already tracked. The
        # composition tool must roll back the branch created this run AND let the
        # *typed* exception propagate unchanged (for the #222 boundary to format)
        # — it must not be caught and swallowed into a passthrough error dict.
        from odoo_sdk.state import TaskAlreadyRunningError

        def _boom(**kw):
            raise TaskAlreadyRunningError(
                "Task 'Fix' already has an active session (id=1, state=running)."
            )

        reg = _FakeRegistry(
            search_projects=lambda *a, **k: [{"id": 5, "name": "Acct"}],
            search_tasks=lambda *a, **k: [{"id": 10, "name": "Fix"}],
            start_task=_boom,
        )
        ctx = _ctx(_confirmed(), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        sp = _make_sp(current_branch="main")
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(TaskAlreadyRunningError):
                _run(tool("Fix", ctx))
        called = [c.args[0] for c in sp.run.call_args_list]
        self.assertIn(["git", "checkout", "main"], called)
        self.assertIn(["git", "branch", "-D", "10#fix"], called)

    def test_no_rollback_when_no_branch_created(self):
        # #164: when already on the task branch, no branch is created this run,
        # so a failing start command must not delete anything.
        def _boom(**kw):
            raise RuntimeError("odoo unreachable")

        client = MagicMock()
        client.execute.return_value = [
            {"id": 10, "name": "Fix", "project_id": [5, "Acct"]}
        ]
        reg = _FakeRegistry(
            client=client,
            search_projects=lambda *a, **k: [],
            search_tasks=lambda *a, **k: [],
            start_task=_boom,
        )
        ctx = _ctx(_confirmed())
        ctx.sample = AsyncMock()
        sp = _make_sp(current_branch="10#x")
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(RuntimeError):
                _run(tool("Fix", ctx, task_id=10))
        called = [c.args[0] for c in sp.run.call_args_list]
        self.assertFalse(any(c[:2] == ["git", "branch"] and "-D" in c for c in called))

    def test_no_rollback_when_branch_pre_existed(self):
        # #164: an idempotent checkout of a pre-existing task branch was not
        # created this run, so a failing start command must not delete it.
        def _boom(**kw):
            raise RuntimeError("odoo unreachable")

        reg = _FakeRegistry(
            search_projects=lambda *a, **k: [{"id": 5, "name": "Acct"}],
            search_tasks=lambda *a, **k: [{"id": 10, "name": "Fix"}],
            start_task=_boom,
        )
        ctx = _ctx(_confirmed(), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        sp = _make_sp(current_branch="main", existing_branches=("10#fix",))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(RuntimeError):
                _run(tool("Fix", ctx))
        called = [c.args[0] for c in sp.run.call_args_list]
        self.assertFalse(any(c[:2] == ["git", "branch"] and "-D" in c for c in called))


class TestCreateTaskBranch(unittest.TestCase):
    """`_create_task_branch` is idempotent (#149) and stash-safe (#150)."""

    @staticmethod
    def _calls(sp):
        return [c.args[0] for c in sp.run.call_args_list]

    def test_creates_branch_when_absent(self):
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp()
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")
        calls = self._calls(sp)
        self.assertIn(["git", "checkout", "-b", "10#fix", "main"], calls)

    def test_checks_out_existing_branch_idempotently(self):
        # #149: a re-run where the target branch already exists must not
        # `checkout -b` (git exit 128); it checks out the existing branch.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(existing_branches=("10#fix",))
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")
        calls = self._calls(sp)
        self.assertIn(["git", "checkout", "10#fix"], calls)
        self.assertFalse(
            any(c[:3] == ["git", "checkout", "-b"] for c in calls),
            "must not recreate an existing branch",
        )

    def test_untracked_only_tree_does_not_pop_without_entry(self):
        # #150: a plain stash on an untracked-only tree saves nothing; using
        # `push -u` creates an entry, so the balanced pop succeeds.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(dirty=True, dirty_kind="untracked")
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")  # must not raise
        calls = self._calls(sp)
        self.assertTrue(any(c[:3] == ["git", "stash", "push"] and "-u" in c for c in calls))
        self.assertIn(["git", "stash", "pop"], calls)

    def test_clean_tree_does_not_stash(self):
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(dirty=False)
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")
        calls = self._calls(sp)
        self.assertFalse(any(c[:2] == ["git", "stash"] for c in calls))

    def test_forks_from_remote_tip_after_fetch(self):
        # #454: a new branch must fork from the fetched ``origin/<base>`` tip so
        # it contains all merged work, not the possibly-stale local base ref.
        # The fetch runs before the checkout, and the checkout uses origin/main.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(remote_branches=("main",))
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")
        calls = self._calls(sp)
        self.assertIn(["git", "fetch", "origin", "main"], calls)
        self.assertIn(["git", "checkout", "-b", "10#fix", "origin/main"], calls)
        self.assertNotIn(
            ["git", "checkout", "-b", "10#fix", "main"],
            calls,
            "must not fork from the stale local base ref",
        )
        fetch_idx = calls.index(["git", "fetch", "origin", "main"])
        checkout_idx = calls.index(["git", "checkout", "-b", "10#fix", "origin/main"])
        self.assertLess(fetch_idx, checkout_idx, "fetch must precede the fork")

    def test_falls_back_to_local_base_when_no_remote_ref(self):
        # No ``origin/<base>`` remote-tracking ref (single-repo / offline): the
        # fetch is still attempted but the fork degrades to the local base ref
        # rather than hard-failing on a missing origin/<base>.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(remote_branches=())
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")
        calls = self._calls(sp)
        self.assertIn(["git", "fetch", "origin", "main"], calls)
        self.assertIn(["git", "checkout", "-b", "10#fix", "main"], calls)

    def test_existing_branch_is_not_re_forked_and_skips_fetch(self):
        # Idempotent checkout of an existing task branch never re-forks, so it
        # must not fetch or resolve a remote base at all.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(existing_branches=("10#fix",), remote_branches=("main",))
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")
        calls = self._calls(sp)
        self.assertNotIn(["git", "fetch", "origin", "main"], calls)


def _sampling_ctx(*responses, supports_sampling=True) -> MagicMock:
    """ctx whose client advertises (or not) the sampling capability."""
    ctx = _ctx(*responses)
    ctx.session.check_client_capability.return_value = supports_sampling
    return ctx


class TestBranchDescriptionSampling(unittest.TestCase):
    """`_generate_branch_description` degrades gracefully without sampling."""

    def test_no_sampling_capability_uses_deterministic_slug(self):
        from odoo_sdk.mcp.tools.start_task import _generate_branch_description

        ctx = MagicMock()
        ctx.session.check_client_capability.return_value = False
        ctx.sample = AsyncMock(side_effect=ValueError("Client does not support sampling"))
        slug = _run(_generate_branch_description(ctx, "Fix VAT rounding", "Acct"))
        self.assertEqual(slug, "fix-vat-rounding")
        ctx.sample.assert_not_called()

    def test_sampling_capability_uses_sampled_slug(self):
        from odoo_sdk.mcp.tools.start_task import _generate_branch_description

        ctx = MagicMock()
        ctx.session.check_client_capability.return_value = True
        ctx.sample = AsyncMock(return_value=MagicMock(text="  Sampled Slug!  "))
        slug = _run(_generate_branch_description(ctx, "Fix VAT", "Acct"))
        self.assertEqual(slug, "sampled-slug")

    def test_empty_sample_result_falls_back_to_task_name(self):
        from odoo_sdk.mcp.tools.start_task import _generate_branch_description

        ctx = MagicMock()
        ctx.session.check_client_capability.return_value = True
        ctx.sample = AsyncMock(return_value=MagicMock(text="   !!!   "))
        slug = _run(_generate_branch_description(ctx, "Fix VAT", "Acct"))
        self.assertEqual(slug, "fix-vat")

    def test_sample_failure_falls_back_to_task_name(self):
        from odoo_sdk.mcp.tools.start_task import _generate_branch_description

        ctx = MagicMock()
        ctx.session.check_client_capability.return_value = True
        ctx.sample = AsyncMock(side_effect=ValueError("Client does not support sampling"))
        slug = _run(_generate_branch_description(ctx, "Fix VAT", "Acct"))
        self.assertEqual(slug, "fix-vat")

    def test_missing_session_falls_back_gracefully(self):
        from odoo_sdk.mcp.tools.start_task import _generate_branch_description

        ctx = MagicMock()
        ctx.session.check_client_capability.side_effect = AttributeError("no session")
        ctx.sample = AsyncMock()
        slug = _run(_generate_branch_description(ctx, "Fix VAT", "Acct"))
        self.assertEqual(slug, "fix-vat")
        ctx.sample.assert_not_called()

    def test_resolved_fastpath_completes_without_sampling(self):
        # Fully-resolved call (task_id) on a client that cannot sample must
        # complete into a session using the deterministic branch slug.
        client = MagicMock()
        client.execute.return_value = [
            {"id": 10, "name": "Fix VAT", "project_id": [5, "Accounting"]}
        ]
        reg = _FakeRegistry(
            client=client,
            search_projects=lambda *a, **k: [],
            search_tasks=lambda *a, **k: [],
            start_task=lambda **kw: {"run_id": 1, **kw},
        )
        ctx = _sampling_ctx(
            _accepted(MagicMock(confirmed=True)),
            _accepted(MagicMock(selection=1)),
            supports_sampling=False,
        )
        ctx.sample = AsyncMock(side_effect=ValueError("Client does not support sampling"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool("Fix VAT", ctx, "Accounting", task_id=10))
        self.assertEqual(result["run_id"], 1)
        self.assertEqual(result["branch_name"], "10#fix-vat")
        ctx.sample.assert_not_called()


class TestStopTaskTool(unittest.TestCase):
    def test_reviews_and_stops(self):
        reg = _FakeRegistry(
            stop_task=lambda task_id, desc: {"task_id": task_id, "description": desc}
        )
        ctx = MagicMock()
        ctx.elicit = AsyncMock(
            return_value=_accepted(MagicMock(description="Reviewed text"))
        )
        result = _run(make_stop_task_tool(reg)(1, "orig", ctx))
        self.assertEqual(result["description"], "Reviewed text")

    def test_falls_back_to_supplied_description(self):
        reg = _FakeRegistry(
            stop_task=lambda task_id, desc: {"task_id": task_id, "description": desc}
        )
        ctx = MagicMock()
        ctx.elicit = AsyncMock(return_value=_accepted(MagicMock(description="")))
        result = _run(make_stop_task_tool(reg)(1, "fallback", ctx))
        self.assertEqual(result["description"], "fallback")

    def test_cancel_returns_error(self):
        reg = _FakeRegistry(stop_task=lambda task_id, desc: {})
        ctx = MagicMock()
        ctx.elicit = AsyncMock(return_value=_cancelled())
        result = _run(make_stop_task_tool(reg)(1, "x", ctx))
        self.assertEqual(result, {"error": "Stop task cancelled."})

    def test_command_failure_propagates_to_boundary(self):
        # Raise-based error contract (#223): after the description review is
        # accepted, a stop command failure (no active session) raises the typed
        # ``TaskNotRunningError``. This flow does no cleanup, so the exception is
        # deliberately left to propagate to the #222 boundary rather than being
        # caught and re-wrapped into an ``{"error": ...}`` dict.
        from odoo_sdk.state import TaskNotRunningError

        def _boom(task_id, desc):
            raise TaskNotRunningError(f"No active session for task {task_id}.")

        reg = _FakeRegistry(stop_task=_boom)
        ctx = MagicMock()
        ctx.elicit = AsyncMock(
            return_value=_accepted(MagicMock(description="done"))
        )
        with self.assertRaises(TaskNotRunningError):
            _run(make_stop_task_tool(reg)(1, "orig", ctx))


if __name__ == "__main__":
    unittest.main()


class TestAtomicToolInvocation(unittest.TestCase):
    """Each atomic tool delegates to its like-named command's execute()."""

    def _registry(self):
        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: f"{name}-result"
                cmd.description = f"{name} description"
                return cmd

        return _Reg()

    def test_all_atomic_tools_route_to_command(self):
        from odoo_sdk.mcp.tools.atomic import ATOMIC_TOOL_FACTORIES

        calls = {
            "get_uid": (),
            "get_models": (),
            "get_tasks": (),
            "get_todo": (5,),
            "get_task": (5,),
            "get_task_chatter": (5,),
            "get_mail_status": ("project.task", 5),
            "get_task_attachments": (5,),
            "read_attachment": (5,),
            "create_task": ("n", 1, "d"),
            "search_projects": ("q",),
            "search_tasks": ("q", 1),
            "resume_task": (5,),
            "abort_task": (5,),
            "abort_run": (1,),
            "assign_event": ([5], 1),
            "discover_runs": (),
            "list_runs": (),
            "report_runs": (),
            "stop_run": (1,),
            "stop_all": (),
            "normalize_timesheets": (),
            "search_chatter": ("q",),
            "search_count": ("project.task",),
            "search_knowledge_articles": ("q",),
            "read_knowledge_article": (5,),
            "task_status": (),
            "task_note": (5, "note"),
            "task_list": (),
            "task_aging": (),
            "task_question": (5, "q?"),
            "optimize_sessions": (),
            "query_sessions": (),
            "resync": (),
            "timesheet_summary": ("2026-07-01", "2026-07-31"),
            "unbilled_hours": (),
            "unlogged_time_report": ("2026-07-01", "2026-07-15"),
        }
        # Pin the atomic tool set to this explicit map so a dropped or renamed
        # @atomic_tool decorator fails here instead of silently going untested.
        self.assertEqual(set(ATOMIC_TOOL_FACTORIES), set(calls))
        for name, factory in ATOMIC_TOOL_FACTORIES.items():
            tool = factory(self._registry())
            result = tool(*calls[name])
            self.assertEqual(result, f"{name}-result")


class TestGetTaskToolSchema(unittest.TestCase):
    """Introspect the get_task tool's wire schema as the server builds it."""

    def _tool(self):
        from fastmcp.tools.tool import Tool

        from odoo_sdk.mcp.tools.atomic import make_get_task_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"task_id": a[0]}
                return cmd

        fn = make_get_task_tool(_Reg())
        return Tool.from_function(fn, name="get_task")

    def test_include_selector_in_input_schema(self):
        schema = self._tool().parameters
        self.assertIn("include", schema["properties"])

    def test_task_id_only_call_still_valid(self):
        # ``task_id`` is the sole required property, so a task_id-only call is
        # schema-valid (backwards compatibility).
        schema = self._tool().parameters
        self.assertEqual(schema["required"], ["task_id"])
        self.assertIn("task_id", schema["properties"])

    def test_include_defaults_to_none(self):
        schema = self._tool().parameters
        self.assertEqual(schema["properties"]["include"].get("default"), None)

    def test_task_id_only_invocation_routes(self):
        fn = make_get_task_tool_reg()
        self.assertEqual(fn(5), {"task_id": 5})


def make_get_task_tool_reg():
    from odoo_sdk.mcp.tools.atomic import make_get_task_tool

    class _Reg:
        def __getitem__(self, name):
            cmd = MagicMock()
            cmd.execute.side_effect = lambda *a, **k: {"task_id": a[0]}
            return cmd

    return make_get_task_tool(_Reg())


class TestCompositionToolDecorator(unittest.TestCase):
    """``@composition_tool("name")`` populates ``COMPOSITION_TOOL_FACTORIES``."""

    def test_registers_the_shipped_composition_tools(self):
        from odoo_sdk.mcp.tools.composition import COMPOSITION_TOOL_FACTORIES

        # The decorator populates the registry at import time — no hand-edited
        # dict literal. Pin the set so a dropped/renamed decorator fails here.
        self.assertEqual(
            set(COMPOSITION_TOOL_FACTORIES), {"start_task", "stop_task"}
        )
        self.assertIs(
            COMPOSITION_TOOL_FACTORIES["start_task"], make_start_task_tool
        )
        self.assertIs(
            COMPOSITION_TOOL_FACTORIES["stop_task"], make_stop_task_tool
        )

    def test_registers_factory_under_explicit_name(self):
        from odoo_sdk.mcp.tools.composition import (
            COMPOSITION_TOOL_FACTORIES,
            composition_tool,
        )

        def _factory(registry):  # pragma: no cover - never invoked
            return lambda: None

        with patch.dict(COMPOSITION_TOOL_FACTORIES, clear=False):
            returned = composition_tool("probe_tool")(_factory)
            # The decorator is transparent and keys by the explicit name.
            self.assertIs(returned, _factory)
            self.assertIs(COMPOSITION_TOOL_FACTORIES["probe_tool"], _factory)
        self.assertNotIn("probe_tool", COMPOSITION_TOOL_FACTORIES)

    def test_duplicate_name_raises(self):
        from odoo_sdk.mcp.tools.composition import (
            COMPOSITION_TOOL_FACTORIES,
            composition_tool,
        )

        def _factory(registry):  # pragma: no cover - never invoked
            return lambda: None

        original = COMPOSITION_TOOL_FACTORIES["start_task"]
        with self.assertRaises(ValueError) as ctx:
            composition_tool("start_task")(_factory)
        self.assertIn("start_task", str(ctx.exception))
        # The collision left the genuine factory in place (no silent overwrite).
        self.assertIs(COMPOSITION_TOOL_FACTORIES["start_task"], original)


class TestAtomicToolDecorator(unittest.TestCase):
    """``@atomic_tool("name")`` populates ``ATOMIC_TOOL_FACTORIES``."""

    def test_registers_factory_under_explicit_name(self):
        from odoo_sdk.mcp.tools.atomic import ATOMIC_TOOL_FACTORIES, atomic_tool

        def _factory(registry):  # pragma: no cover - never invoked
            return lambda: None

        # patch.dict restores ATOMIC_TOOL_FACTORIES after the block so the probe
        # registration never leaks into the real atomic tool surface.
        with patch.dict(ATOMIC_TOOL_FACTORIES, clear=False):
            returned = atomic_tool("probe_tool")(_factory)
            # The decorator is transparent and keys by the explicit name.
            self.assertIs(returned, _factory)
            self.assertIs(ATOMIC_TOOL_FACTORIES["probe_tool"], _factory)
        self.assertNotIn("probe_tool", ATOMIC_TOOL_FACTORIES)

    def test_name_decouples_from_command_name(self):
        # The explicit tool name may differ from the command the body looks up.
        from odoo_sdk.mcp.tools.atomic import ATOMIC_TOOL_FACTORIES, atomic_tool

        def _factory(registry):  # pragma: no cover - never invoked
            return lambda: registry["backing_command"].execute()

        with patch.dict(ATOMIC_TOOL_FACTORIES, clear=False):
            atomic_tool("public_alias")(_factory)
            self.assertIn("public_alias", ATOMIC_TOOL_FACTORIES)
            self.assertNotIn("backing_command", ATOMIC_TOOL_FACTORIES)

    def test_duplicate_name_raises(self):
        from odoo_sdk.mcp.tools.atomic import ATOMIC_TOOL_FACTORIES, atomic_tool

        def _factory(registry):  # pragma: no cover - never invoked
            return lambda: None

        original = ATOMIC_TOOL_FACTORIES["get_uid"]
        with self.assertRaises(ValueError) as ctx:
            atomic_tool("get_uid")(_factory)
        self.assertIn("get_uid", str(ctx.exception))
        # The collision left the genuine factory in place (no silent overwrite).
        self.assertIs(ATOMIC_TOOL_FACTORIES["get_uid"], original)
