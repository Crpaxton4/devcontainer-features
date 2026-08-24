"""Tests for explicit MCP tools that compose commands with ctx.elicit/sample."""

import asyncio
import os
import unittest
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


def _cancelled() -> MagicMock:
    r = MagicMock()
    r.action = "cancel"
    return r


def _make_sp(
    current_branch="main",
    branches=("main",),
    dirty=False,
    dirty_kind="tracked",
    existing_branches=(),
    remote_branches=(),
    origin_head="",
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
    :param origin_head: Value returned by ``git symbolic-ref --short
        refs/remotes/origin/HEAD`` (e.g. ``"origin/main"``); empty means the
        remote default branch is unknown, so ``_default_base_branch`` falls back
        to the current branch.
    """
    sp = MagicMock()
    state = {"stash_entries": 0}

    def _r(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        if args[1] == "symbolic-ref":
            r.stdout = f"{origin_head}\n" if origin_head else ""
            r.returncode = 0 if origin_head else 1
        elif args[1] == "rev-parse" and "--verify" in args:
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


def _idle_state() -> MagicMock:
    """A fake LocalStateClient reporting no session for any task."""
    state = MagicMock()
    state.get_active_run.return_value = None
    return state


def _running_state(run_id=1) -> MagicMock:
    """A fake LocalStateClient reporting an already-RUNNING session (#621)."""
    from odoo_sdk.state import TaskState

    state = MagicMock()
    state.get_active_run.return_value = SimpleNamespace(
        id=run_id, state=TaskState.RUNNING
    )
    return state


class _FakeRegistry:
    """Minimal registry: maps command name -> object with execute()."""

    def __init__(self, client=None, state=None, **commands):
        self._client = client if client is not None else MagicMock()
        self._state = state if state is not None else _idle_state()
        self._commands = {}
        for name, fn in commands.items():
            self._commands[name] = fn

    def __getitem__(self, name):
        cmd = MagicMock()
        impl = self._commands[name]
        cmd.execute.side_effect = impl
        cmd._client = self._client
        cmd.state = self._state
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


class TestDefaultToolSurface(unittest.TestCase):
    """`default_tool_surface` gates the narrow-context tools off by default (#512).

    The lever is tool *count*: 39 exposed tools trip Claude Code's client-side
    lazy deferral, so the everyday surface holds the maintenance/triage/introspection
    tools back — without deleting them from the built surface.
    """

    #: The count that trips client-side lazy deferral (#512); the default surface
    #: must stay strictly below it.
    _DEFERRAL_TRIP_COUNT = 39

    def _full(self):
        from odoo_sdk.commands import Registry
        from odoo_sdk.commands.builtin import register_builtins

        registry = register_builtins(Registry(MagicMock()))
        return build_explicit_tools(registry)

    def test_gated_names_are_real_tools(self):
        # Every gated name must exist in the built surface, or a rename would
        # silently gate nothing.
        from odoo_sdk.mcp.tools import GATED_TOOL_NAMES

        self.assertTrue(GATED_TOOL_NAMES)
        self.assertLessEqual(GATED_TOOL_NAMES, set(self._full()))

    def test_default_excludes_exactly_the_gated_tools(self):
        from odoo_sdk.mcp.tools import GATED_TOOL_NAMES, default_tool_surface

        full = self._full()
        default = default_tool_surface(full, include_gated=False)
        self.assertEqual(set(default), set(full) - GATED_TOOL_NAMES)
        # None of the gated tools leak onto the default surface ...
        self.assertEqual(set(default) & GATED_TOOL_NAMES, set())
        # ... and every kept tool retains its (callable, description) spec.
        for name, spec in default.items():
            self.assertIs(spec, full[name])

    def test_default_surface_is_below_the_deferral_threshold(self):
        from odoo_sdk.mcp.tools import default_tool_surface

        default = default_tool_surface(self._full(), include_gated=False)
        self.assertLess(len(default), self._DEFERRAL_TRIP_COUNT)

    def test_working_set_tools_stay_on_the_default_surface(self):
        from odoo_sdk.mcp.tools import default_tool_surface

        default = default_tool_surface(self._full(), include_gated=False)
        # A representative slice of the everyday task/project/reporting flow,
        # including the composition tools and the tools the status-report skill
        # drives, must never be gated.
        for name in (
            "start_task",
            "stop_task",
            "get_task",
            "get_tasks",
            "task_list",
            "task_note",
            "task_question",
            "resume_task",
            "search_projects",
            "search_tasks",
            "search_knowledge_articles",
            "create_task",
            "timesheet_summary",
            "unbilled_hours",
            "task_aging",
        ):
            self.assertIn(name, default)

    def test_include_gated_true_returns_the_full_surface(self):
        from odoo_sdk.mcp.tools import default_tool_surface

        full = self._full()
        self.assertEqual(
            set(default_tool_surface(full, include_gated=True)), set(full)
        )

    def test_env_opt_in_restores_the_full_surface(self):
        from odoo_sdk.mcp.tools import GATED_TOOLS_ENV, default_tool_surface

        full = self._full()
        with patch.dict(os.environ, {GATED_TOOLS_ENV: "1"}):
            # include_gated=None defers to the env flag.
            self.assertEqual(set(default_tool_surface(full)), set(full))

    def test_env_absent_gates_by_default(self):
        from odoo_sdk.mcp.tools import GATED_TOOL_NAMES, default_tool_surface

        full = self._full()
        with patch.dict(os.environ, {}, clear=True):
            default = default_tool_surface(full)
        self.assertEqual(set(default) & GATED_TOOL_NAMES, set())


class TestStartTaskTool(unittest.TestCase):
    def _registry(self, *, projects, tasks, start_result, state=None):
        return _FakeRegistry(
            state=state,
            search_projects=lambda query, limit=10: projects,
            search_tasks=lambda query, project_id, limit=10: tasks,
            start_task=lambda **kw: {**start_result, **kw},
        )

    def test_single_project_and_task_starts_without_confirmation(self):
        # #621: no confirmation gate — the only elicitation on the name path is
        # the base-branch pick.
        reg = self._registry(
            projects=[{"id": 5, "name": "Accounting"}],
            tasks=[{"id": 10, "name": "Fix VAT"}],
            start_result={"run_id": 1, "task_id": 10},
        )
        ctx = _ctx(_accepted(MagicMock(selection=1)))  # branch pick
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool(ctx, "VAT", "Accounting"))
        self.assertEqual(result["task_id"], 10)
        self.assertEqual(result["project_name"], "Accounting")
        self.assertEqual(ctx.elicit.await_count, 1)
        prompt = ctx.elicit.await_args_list[0].args[0]
        self.assertIn("base branch", prompt)

    def test_missing_task_id_and_query_errors(self):
        reg = self._registry(projects=[], tasks=[], start_result={})
        result = _run(make_start_task_tool(reg)(MagicMock()))
        self.assertIn("error", result)
        self.assertIn("task_id", result["error"])

    def test_no_projects_returns_error(self):
        reg = self._registry(projects=[], tasks=[], start_result={})
        tool = make_start_task_tool(reg)
        result = _run(tool(MagicMock(), "x", "Nope"))
        self.assertIn("error", result)
        self.assertIn("No projects", result["error"])

    def test_disambiguates_multiple_projects(self):
        reg = self._registry(
            projects=[{"id": 1, "name": "HR"}, {"id": 2, "name": "Acct"}],
            tasks=[{"id": 10, "name": "Fix VAT"}],
            start_result={"run_id": 1, "task_id": 10},
        )
        ctx = _ctx(
            _accepted(MagicMock(selection=2)),  # pick project
            _accepted(MagicMock(selection=1)),  # branch pick
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool(ctx, "VAT"))
        self.assertEqual(result["task_id"], 10)

    def test_cancelled_project_selection_errors(self):
        reg = self._registry(
            projects=[{"id": 1, "name": "HR"}, {"id": 2, "name": "IT"}],
            tasks=[],
            start_result={},
        )
        ctx = _ctx(_cancelled())
        tool = make_start_task_tool(reg)
        result = _run(tool(ctx, "x"))
        self.assertIn("error", result)

    def test_no_tasks_returns_error(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}], tasks=[], start_result={}
        )
        ctx = MagicMock()
        tool = make_start_task_tool(reg)
        result = _run(tool(ctx, "x"))
        self.assertIn("error", result)

    def test_task_id_only_is_headless_with_zero_search_calls(self):
        # #614: with a task_id the tool performs ZERO name-search calls and
        # ZERO elicitations — no task_name_query is needed at all.
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
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        ctx.session.check_client_capability.return_value = False
        ctx.sample = AsyncMock()
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool(ctx, task_id=10))
        self.assertFalse(called["search"])
        ctx.elicit.assert_not_awaited()
        ctx.sample.assert_not_called()
        self.assertEqual(result["project_name"], "Accounting")
        # Deterministic slug + hyphenated convention (#622).
        self.assertEqual(result["branch_name"], "10-fix-vat")

    def test_task_id_not_found_errors_without_search_fallback(self):
        # #614: an unknown id is an error — never a fuzzy name-search fallback
        # that could land on the wrong task, even when a name query was given.
        client = MagicMock()
        client.execute.return_value = []
        called = {"search": False}

        def _search(*a, **k):
            called["search"] = True
            return [{"id": 10, "name": "Fix"}]

        reg = _FakeRegistry(
            client=client,
            search_projects=_search,
            search_tasks=_search,
            start_task=lambda **kw: {},
        )
        result = _run(make_start_task_tool(reg)(MagicMock(), "Fix", task_id=999))
        self.assertIn("999", result["error"])
        self.assertFalse(called["search"])

    def test_already_running_short_circuits_with_zero_side_effects(self):
        # #621: pre-flight state check fires BEFORE any side effect — a RUNNING
        # session yields the command's no-op result with no git call and no
        # elicitation at all.
        client = MagicMock()
        client.execute.return_value = [
            {"id": 10, "name": "Fix", "project_id": [5, "Acct"]}
        ]
        reg = _FakeRegistry(
            client=client,
            state=_running_state(),
            search_projects=lambda *a, **k: [],
            search_tasks=lambda *a, **k: [],
            start_task=lambda **kw: {"run_id": 1, "already_running": True, **kw},
        )
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        ctx.sample = AsyncMock()
        sp = _make_sp()
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            result = _run(tool(ctx, task_id=10))
        self.assertTrue(result["already_running"])
        self.assertNotIn("branch_name", result)
        ctx.elicit.assert_not_awaited()
        ctx.sample.assert_not_called()
        sp.run.assert_not_called()

    def test_awaiting_answers_proceeds_to_the_command(self):
        # #621: a non-RUNNING active session (AWAITING_ANSWERS) is not the
        # no-op path — the command is invoked to transition it back to RUNNING.
        from odoo_sdk.state import TaskState

        state = MagicMock()
        state.get_active_run.return_value = SimpleNamespace(
            id=1, state=TaskState.AWAITING_ANSWERS
        )
        client = MagicMock()
        client.execute.return_value = [
            {"id": 10, "name": "Fix", "project_id": [5, "Acct"]}
        ]
        reg = _FakeRegistry(
            client=client,
            state=state,
            search_projects=lambda *a, **k: [],
            search_tasks=lambda *a, **k: [],
            start_task=lambda **kw: {"run_id": 1, "already_running": False, **kw},
        )
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        ctx.session.check_client_capability.return_value = False
        ctx.sample = AsyncMock()
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp(current_branch="10-fix")):
            result = _run(tool(ctx, task_id=10))
        self.assertFalse(result["already_running"])
        self.assertEqual(result["run_id"], 1)

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
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        ctx.sample = AsyncMock()
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp(current_branch="10-x")):
            result = _run(tool(ctx, task_id=10))
        ctx.sample.assert_not_called()
        ctx.elicit.assert_not_awaited()
        self.assertIsNone(result.get("branch_name"))

    def test_branch_selection_cancelled(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}],
            tasks=[{"id": 10, "name": "Fix"}],
            start_result={},
        )
        ctx = _ctx(_cancelled())
        ctx.sample = AsyncMock()
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool(ctx, "Fix"))
        self.assertEqual(result, {"error": "Branch selection cancelled."})

    def test_no_branches_available_errors(self):
        reg = self._registry(
            projects=[{"id": 5, "name": "Acct"}],
            tasks=[{"id": 10, "name": "Fix"}],
            start_result={},
        )
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp(branches=())):
            result = _run(tool(ctx, "Fix"))
        self.assertIn("No local git branches", result["error"])

    def test_task_branches_are_excluded_from_the_base_pick(self):
        # #622: the base-branch picker never offers a ``<id>-<slug>`` task
        # branch as a fork base.
        from odoo_sdk.mcp.tools.start_task import _list_local_branches

        sp = _make_sp(branches=("main", "10-fix-vat", "feat/x", "28788-slug"))
        with patch(_SP_PATCH, sp):
            self.assertEqual(_list_local_branches(), ["main", "feat/x"])

    def test_headless_base_prefers_the_remote_default_branch(self):
        # #621 headless path: with origin/HEAD known, the fork base is the
        # remote default branch — no elicitation involved.
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
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        ctx.session.check_client_capability.return_value = False
        ctx.sample = AsyncMock()
        sp = _make_sp(
            current_branch="feature/other",
            origin_head="origin/develop",
            remote_branches=("develop",),
        )
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            result = _run(tool(ctx, task_id=10))
        self.assertEqual(result["branch_name"], "10-fix")
        called = [c.args[0] for c in sp.run.call_args_list]
        self.assertIn(["git", "checkout", "-b", "10-fix", "origin/develop"], called)
        ctx.elicit.assert_not_awaited()

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
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        sp = _make_sp(dirty=True)
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            _run(tool(ctx, task_id=10))
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
        ctx = _ctx(_accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        sp = _make_sp(current_branch="main")
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(RuntimeError):
                _run(tool(ctx, "Fix"))
        called = [c.args[0] for c in sp.run.call_args_list]
        # Switched back to the original branch, then deleted the task branch.
        self.assertIn(["git", "checkout", "main"], called)
        self.assertIn(["git", "branch", "-D", "10-fix"], called)

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
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        ctx.sample = AsyncMock()
        sp = _make_sp(current_branch="10-x")
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(RuntimeError):
                _run(tool(ctx, task_id=10))
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
        ctx = _ctx(_accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        sp = _make_sp(current_branch="main", existing_branches=("10-fix",))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(RuntimeError):
                _run(tool(ctx, "Fix"))
        called = [c.args[0] for c in sp.run.call_args_list]
        self.assertFalse(any(c[:2] == ["git", "branch"] and "-D" in c for c in called))


class TestStartTaskToolSchema(unittest.TestCase):
    """The start_task wire schema after #614: task_id alone must suffice."""

    def _schema(self):
        from fastmcp.tools.tool import Tool

        fn = make_start_task_tool(MagicMock())
        return Tool.from_function(fn, name="start_task").parameters

    def test_task_name_query_is_not_required(self):
        # #614: the schema no longer forces a name query onto ID-holding
        # automation — nothing is schema-required (validation is runtime).
        self.assertEqual(self._schema().get("required", []), [])

    def test_task_id_and_queries_are_all_in_the_schema(self):
        props = self._schema()["properties"]
        for name in ("task_id", "task_name_query", "project_name_query"):
            self.assertIn(name, props)

    def test_ctx_not_in_schema(self):
        self.assertNotIn("ctx", self._schema()["properties"])


class TestCreateTaskBranch(unittest.TestCase):
    """`_create_task_branch` is idempotent (#149) and stash-safe (#150)."""

    @staticmethod
    def _calls(sp):
        return [c.args[0] for c in sp.run.call_args_list]

    def test_creates_branch_when_absent(self):
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp()
        with patch(_SP_PATCH, sp):
            _create_task_branch("10-fix", "main")
        calls = self._calls(sp)
        self.assertIn(["git", "checkout", "-b", "10-fix", "main"], calls)

    def test_checks_out_existing_branch_idempotently(self):
        # #149: a re-run where the target branch already exists must not
        # `checkout -b` (git exit 128); it checks out the existing branch.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(existing_branches=("10-fix",))
        with patch(_SP_PATCH, sp):
            _create_task_branch("10-fix", "main")
        calls = self._calls(sp)
        self.assertIn(["git", "checkout", "10-fix"], calls)
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
            _create_task_branch("10-fix", "main")  # must not raise
        calls = self._calls(sp)
        self.assertTrue(any(c[:3] == ["git", "stash", "push"] and "-u" in c for c in calls))
        self.assertIn(["git", "stash", "pop"], calls)

    def test_clean_tree_does_not_stash(self):
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(dirty=False)
        with patch(_SP_PATCH, sp):
            _create_task_branch("10-fix", "main")
        calls = self._calls(sp)
        self.assertFalse(any(c[:2] == ["git", "stash"] for c in calls))

    def test_forks_from_remote_tip_after_fetch(self):
        # #454: a new branch must fork from the fetched ``origin/<base>`` tip so
        # it contains all merged work, not the possibly-stale local base ref.
        # The fetch runs before the checkout, and the checkout uses origin/main.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(remote_branches=("main",))
        with patch(_SP_PATCH, sp):
            _create_task_branch("10-fix", "main")
        calls = self._calls(sp)
        self.assertIn(["git", "fetch", "origin", "main"], calls)
        self.assertIn(["git", "checkout", "-b", "10-fix", "origin/main"], calls)
        self.assertNotIn(
            ["git", "checkout", "-b", "10-fix", "main"],
            calls,
            "must not fork from the stale local base ref",
        )
        fetch_idx = calls.index(["git", "fetch", "origin", "main"])
        checkout_idx = calls.index(["git", "checkout", "-b", "10-fix", "origin/main"])
        self.assertLess(fetch_idx, checkout_idx, "fetch must precede the fork")

    def test_falls_back_to_local_base_when_no_remote_ref(self):
        # No ``origin/<base>`` remote-tracking ref (single-repo / offline): the
        # fetch is still attempted but the fork degrades to the local base ref
        # rather than hard-failing on a missing origin/<base>.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(remote_branches=())
        with patch(_SP_PATCH, sp):
            _create_task_branch("10-fix", "main")
        calls = self._calls(sp)
        self.assertIn(["git", "fetch", "origin", "main"], calls)
        self.assertIn(["git", "checkout", "-b", "10-fix", "main"], calls)

    def test_existing_branch_is_not_re_forked_and_skips_fetch(self):
        # Idempotent checkout of an existing task branch never re-forks, so it
        # must not fetch or resolve a remote base at all.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(existing_branches=("10-fix",), remote_branches=("main",))
        with patch(_SP_PATCH, sp):
            _create_task_branch("10-fix", "main")
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
        ctx = _sampling_ctx(supports_sampling=False)
        ctx.sample = AsyncMock(side_effect=ValueError("Client does not support sampling"))
        tool = make_start_task_tool(reg)
        with patch(_SP_PATCH, _make_sp()):
            result = _run(tool(ctx, "Fix VAT", "Accounting", task_id=10))
        self.assertEqual(result["run_id"], 1)
        self.assertEqual(result["branch_name"], "10-fix-vat")
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
        result = _run(make_stop_task_tool(reg)(1, ctx, "orig"))
        self.assertEqual(result["description"], "Reviewed text")

    def test_falls_back_to_supplied_description(self):
        reg = _FakeRegistry(
            stop_task=lambda task_id, desc: {"task_id": task_id, "description": desc}
        )
        ctx = MagicMock()
        ctx.elicit = AsyncMock(return_value=_accepted(MagicMock(description="")))
        result = _run(make_stop_task_tool(reg)(1, ctx, "fallback"))
        self.assertEqual(result["description"], "fallback")

    def test_description_omitted_skips_elicitation(self):
        # Time logging moved to the odoo-tui/ETL path (#482): with no
        # description there is nothing to review, so the tool must not prompt.
        reg = _FakeRegistry(
            stop_task=lambda task_id, desc: {"task_id": task_id, "description": desc}
        )
        ctx = MagicMock()
        ctx.elicit = AsyncMock()
        result = _run(make_stop_task_tool(reg)(1, ctx))
        ctx.elicit.assert_not_awaited()
        self.assertIsNone(result["description"])

    def test_cancel_returns_error(self):
        reg = _FakeRegistry(stop_task=lambda task_id, desc: {})
        ctx = MagicMock()
        ctx.elicit = AsyncMock(return_value=_cancelled())
        result = _run(make_stop_task_tool(reg)(1, ctx, "x"))
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
            _run(make_stop_task_tool(reg)(1, ctx, "orig"))


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


class TestGetTasksToolSchema(unittest.TestCase):
    """The get_tasks tool's wire schema and include passthrough (#630)."""

    def _make(self):
        from odoo_sdk.mcp.tools.atomic import make_get_tasks_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return make_get_tasks_tool(_Reg())

    def _schema(self):
        from fastmcp.tools.tool import Tool

        return Tool.from_function(self._make(), name="get_tasks").parameters

    def test_include_selector_in_input_schema(self):
        self.assertIn("include", self._schema()["properties"])

    def test_include_optional_and_defaults_to_none(self):
        schema = self._schema()
        # No property becomes required: a bare get_tasks() call stays
        # schema-valid (backwards compatibility).
        self.assertEqual(schema.get("required", []), [])
        self.assertEqual(schema["properties"]["include"].get("default"), None)

    def test_include_forwarded_to_command(self):
        fn = self._make()
        domain = [("stage_id", "=", 3)]
        result = fn(domain=domain, limit=5, include=["description"])
        self.assertEqual(
            result["kwargs"],
            {"domain": domain, "limit": 5, "include": ["description"]},
        )

    def test_plain_call_forwards_none_include(self):
        # Omitted ``include`` reaches the command as None, keeping the
        # summary-only behavior unchanged.
        fn = self._make()
        result = fn()
        self.assertEqual(
            result["kwargs"], {"domain": None, "limit": 10, "include": None}
        )


class TestTaskNoteToolSchema(unittest.TestCase):
    """The task_note tool's wire schema after #604 (attachments) and #610 (cap)."""

    def _make(self):
        from odoo_sdk.mcp.tools.atomic import make_task_note_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return make_task_note_tool(_Reg())

    def _schema(self):
        from fastmcp.tools.tool import Tool

        return Tool.from_function(self._make(), name="task_note").parameters

    def test_attachments_in_input_schema(self):
        self.assertIn("attachments", self._schema()["properties"])

    def test_attachments_optional_and_defaults_to_none(self):
        schema = self._schema()
        # ``task_id`` and ``note`` stay the only required properties, so an
        # attachment-less call remains schema-valid (backwards compatibility).
        self.assertEqual(schema["required"], ["task_id", "note"])
        self.assertEqual(
            schema["properties"]["attachments"].get("default"), None
        )

    def test_dedupe_key_optional_and_defaults_to_none(self):
        # #631: the idempotency key is opt-in and never required.
        schema = self._schema()
        self.assertIn("dedupe_key", schema["properties"])
        self.assertEqual(schema["required"], ["task_id", "note"])
        self.assertEqual(schema["properties"]["dedupe_key"].get("default"), None)

    def test_docstring_names_the_300_char_limit(self):
        # MCP callers must see the #610 cap up front, in the tool itself as
        # well as in the command-sourced description.
        self.assertIn("300", self._make().__doc__)

    def test_attachments_forwarded_to_command(self):
        fn = self._make()
        specs = [{"path": "/tmp/report.csv"}]
        result = fn(5, "note", specs)
        self.assertEqual(result["args"], (5, "note"))
        self.assertEqual(
            result["kwargs"], {"attachments": specs, "dedupe_key": None}
        )

    def test_dedupe_key_forwarded_to_command(self):
        fn = self._make()
        result = fn(5, "note", None, "note-abc")
        self.assertEqual(
            result["kwargs"], {"attachments": None, "dedupe_key": "note-abc"}
        )

    def test_plain_call_forwards_none_attachments(self):
        fn = self._make()
        result = fn(5, "note")
        self.assertEqual(
            result["kwargs"], {"attachments": None, "dedupe_key": None}
        )


class TestGetTaskChatterToolSchema(unittest.TestCase):
    """The get_task_chatter tool's wire schema after the #624 since-cursor."""

    def _make(self):
        from odoo_sdk.mcp.tools.atomic import make_get_task_chatter_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return make_get_task_chatter_tool(_Reg())

    def _schema(self):
        from fastmcp.tools.tool import Tool

        return Tool.from_function(self._make(), name="get_task_chatter").parameters

    def test_since_optional_and_defaults_to_none(self):
        schema = self._schema()
        self.assertIn("since", schema["properties"])
        # ``task_id`` stays the sole required property (backwards compat).
        self.assertEqual(schema["required"], ["task_id"])
        self.assertEqual(schema["properties"]["since"].get("default"), None)

    def test_since_forwarded_to_command(self):
        fn = self._make()
        result = fn(5, 20, 77)
        self.assertEqual(result["args"], (5,))
        self.assertEqual(result["kwargs"], {"limit": 20, "since": 77})

    def test_plain_call_forwards_none_since(self):
        fn = self._make()
        result = fn(5)
        self.assertEqual(result["kwargs"], {"limit": 100, "since": None})


class TestTaskQuestionToolSchema(unittest.TestCase):
    """The task_question tool's wire schema after the #631 dedupe key."""

    def _make(self):
        from odoo_sdk.mcp.tools.atomic import make_task_question_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return make_task_question_tool(_Reg())

    def _schema(self):
        from fastmcp.tools.tool import Tool

        return Tool.from_function(self._make(), name="task_question").parameters

    def test_dedupe_key_optional_and_defaults_to_none(self):
        schema = self._schema()
        self.assertIn("dedupe_key", schema["properties"])
        self.assertEqual(schema["required"], ["task_id", "question"])
        self.assertEqual(schema["properties"]["dedupe_key"].get("default"), None)

    def test_dedupe_key_forwarded_to_command(self):
        fn = self._make()
        result = fn(5, "q?", "q-abc")
        self.assertEqual(result["args"], (5, "q?"))
        self.assertEqual(result["kwargs"], {"dedupe_key": "q-abc"})

    def test_plain_call_forwards_none_dedupe_key(self):
        fn = self._make()
        result = fn(5, "q?")
        self.assertEqual(result["kwargs"], {"dedupe_key": None})


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
