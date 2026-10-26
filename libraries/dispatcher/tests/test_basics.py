"""Lock, gate, queue, settings, schema and the ten dead-letter causes."""

import dataclasses
import json
import multiprocessing

import pytest
from odoo_sdk.state.jobs import DeadReason, JobState, JobStore

from dispatcher import deadletter, gates, queue, schema
from dispatcher.deadletter import Ending, classify
from dispatcher.lock import AlreadyRunningError, DispatcherLock
from dispatcher.settings import (
    DEFAULT_ENTRY,
    ConfigResolutionError,
    Settings,
    SettingsError,
    normalize_project,
)
from support import GOOD

# -- lock ---------------------------------------------------------------


def _try_lock(path, out):
    try:
        DispatcherLock(path).acquire()
        out.put("acquired")
    except AlreadyRunningError:
        out.put("refused")


def test_lock_refuses_a_second_holder_across_processes(tmp_path):
    path = tmp_path / "dispatcher.lock"
    first = DispatcherLock(path).acquire()
    assert first.held
    out = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_try_lock, args=(path, out))
    proc.start()
    proc.join(10)
    assert out.get(timeout=5) == "refused"
    first.release()
    assert not first.held
    with DispatcherLock(path):  # free again once released
        assert path.read_text().strip().isdigit()


def test_lock_is_whole_file_flock_not_record_lock(tmp_path):
    """A second descriptor closing must not drop the lock (the lockf trap)."""
    import os

    path = tmp_path / "dispatcher.lock"
    lock = DispatcherLock(path).acquire()
    os.close(os.open(path, os.O_RDONLY))  # POSIX record locks would die here
    with pytest.raises(AlreadyRunningError):
        DispatcherLock(path).acquire()
    lock.release()
    lock.release()  # idempotent


# -- gates --------------------------------------------------------------


def _meminfo(tmp_path, text):
    path = tmp_path / "meminfo"
    path.write_text(text)
    return path


def test_gate_reads_available_not_free(tmp_path):
    info = _meminfo(
        tmp_path, "MemTotal: 100 kB\nMemFree: 10 kB\nMemAvailable: 8388608 kB\n"
    )
    gate = gates.AdmissionGate(6, meminfo=info)
    assert gates.read_available_kb(info) == 8388608
    assert gate.has_room()  # free is ~0 but 8 GB is available


def test_gate_threshold_arithmetic(tmp_path):
    gate = gates.AdmissionGate(
        6, meminfo=_meminfo(tmp_path, "MemAvailable: 6291455 kB\n")
    )
    assert not gate.has_room()  # one kB under 6 GiB
    gate = gates.AdmissionGate(
        6, meminfo=_meminfo(tmp_path, "MemAvailable: 6291456 kB\n")
    )
    assert gate.has_room()


def test_gate_settles_between_admissions(tmp_path):
    slept = []
    gate = gates.AdmissionGate(1, 7, meminfo=tmp_path, sleep=slept.append)
    gate.settle()
    gates.AdmissionGate(1, 0, sleep=slept.append).settle()
    assert slept == [7]


@pytest.mark.parametrize(
    "text", ["MemFree: 1 kB\n", "MemAvailable: lots kB\n", "MemAvailable:\n"]
)
def test_gate_never_guesses(tmp_path, text):
    with pytest.raises(gates.MemoryReadError):
        gates.read_available_kb(_meminfo(tmp_path, text))
    with pytest.raises(gates.MemoryReadError):
        gates.read_available_kb(tmp_path / "missing")


# -- queue --------------------------------------------------------------


def _brief(tmp_path, name="b.md", text="do it"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_submission_dereferences_the_brief_once(tmp_path):
    path = _brief(tmp_path)
    arg = json.dumps(
        [
            {"repo": "qoc", "task": "1", "brief": path},
            {"repo": "x", "task": "2", "brief": path, "round": 2},
        ]
    )
    specs = queue.parse_submission(arg)
    assert [(s.repo, s.task_id, s.brief, s.round) for s in specs] == [
        ("qoc", "1", "do it", 1),
        ("x", "2", "do it", 2),
    ]


@pytest.mark.parametrize(
    "argument",
    [
        "not json",
        "{}",
        "[]",
        "[1]",
        '[{"repo":"a","task":"1"}]',
        '[{"repo":"a/b","task":"1","brief":"x"}]',
        '[{"repo":"a","task":"1","brief":"/nonexistent"}]',
        '[{"repo":"a","task":"1","brief":"x","extra":1}]',
        '[{"repo":"a","task":"1","brief":"x","round":0}]',
        '[{"repo":"a","task":"1","brief":""}]',
    ],
)
def test_submission_rejects_the_whole_call(argument):
    with pytest.raises(queue.SubmissionError):
        queue.parse_submission(argument)


@pytest.mark.parametrize("repo", [".github", ".github-private"])
def test_repo_name_may_start_with_a_dot(tmp_path, repo):
    arg = json.dumps([{"repo": repo, "task": "1", "brief": _brief(tmp_path)}])
    assert [s.repo for s in queue.parse_submission(arg)] == [repo]


@pytest.mark.parametrize("repo", [".", "..", ".../x", "a/b", "", "..x"])
def test_repo_name_refuses_dot_and_traversal(tmp_path, repo):
    arg = json.dumps([{"repo": repo, "task": "1", "brief": _brief(tmp_path)}])
    with pytest.raises(queue.SubmissionError, match="plain name"):
        queue.parse_submission(arg)


def test_task_id_still_refuses_a_leading_dot(tmp_path):
    arg = json.dumps([{"repo": "a", "task": ".1", "brief": _brief(tmp_path)}])
    with pytest.raises(queue.SubmissionError, match="plain name"):
        queue.parse_submission(arg)


def test_empty_brief_is_rejected(tmp_path):
    arg = json.dumps(
        [{"repo": "a", "task": "1", "brief": _brief(tmp_path, text="  \n")}]
    )
    with pytest.raises(queue.SubmissionError, match="empty"):
        queue.parse_submission(arg)


def test_submit_groups_by_project_and_rejects_duplicates(tmp_path):
    store = JobStore()
    specs = [
        queue.JobSpec("a", "1", "x"),
        queue.JobSpec("b", "2", "x"),
        queue.JobSpec("a", "3", "x"),
        queue.JobSpec("b", "2", "x"),  # live twin
    ]
    jobs = queue.submit(store, specs, "batch")
    assert [(j.repo, j.task_id) for j in jobs] == [
        ("a", "1"),
        ("a", "3"),
        ("b", "2"),
        ("b", "2"),
    ]
    assert jobs[3].state is JobState.DEAD_LETTER
    assert jobs[3].dead_reason == DeadReason.DUPLICATE_TASK_IN_FLIGHT.value
    assert [j.job_id for j in queue.pending_jobs(store)] == [j.job_id for j in jobs[:3]]


def test_reconcile_orphans_running_jobs():
    store = JobStore()
    (job,) = queue.submit(store, [queue.JobSpec("a", "1", "x")], "b")
    store.mark_running(job.job_id)
    assert queue.reconcile(store) == [job.job_id]
    dead = store.get(job.job_id)
    assert dead.dead_reason == DeadReason.ORPHANED_BY_RESTART.value
    assert "previous dispatcher" in dead.detail
    # the duplicate gate is open again
    queue.submit(store, [queue.JobSpec("a", "1", "x")], "b2")
    assert queue.reconcile(store) == []


# -- settings -----------------------------------------------------------


def test_settings_from_env(tmp_path):
    prompts = tmp_path / "p" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "unattended-invariants.md").write_text("default text", "utf-8")
    env = {
        "REPOS_DIR": "/r",
        "DISPATCH_CONFIG_ROOT": "/c",
        "DISPATCH_REPO_OWNER": "acme",
        "DISPATCH_PLUGIN_ROOT": str(tmp_path / "p"),
        "MIN_FREE_GB": "3",
        "DISPATCH_CLAUDE_HOME": "/h",
    }
    config = tmp_path / "c" / "github.com" / "acme" / "qoc" / ".devcontainer"
    config.mkdir(parents=True)
    (config / "devcontainer.json").write_text("{}", "utf-8")
    env["DISPATCH_CONFIG_ROOT"] = str(tmp_path / "c")
    s = Settings.from_env(env)
    assert str(s.workspace("qoc")) == "/r/qoc"
    assert s.config("qoc") == config / "devcontainer.json"
    assert Settings.from_env({**env, "DISPATCH_REPO_OWNER": ""}).owner == ""
    assert s.invariants_file == str(prompts / "unattended-invariants.md")
    assert s.invariants == "default text"
    assert (s.min_available_gb, s.deadline_s, s.entry) == (
        3,
        21600,
        "/odoo-dev:implement",
    )


def test_settings_defaults_and_errors(tmp_path):
    invariants = tmp_path / "inv.md"
    invariants.write_text("host text", "utf-8")
    env = {
        "DISPATCH_REPOS_DIR": "/r",
        "DISPATCH_CONFIG_ROOT": "/c",
        "DISPATCH_REPO_OWNER": "o",
        "DISPATCH_INVARIANTS_FILE": str(invariants),
    }
    s = Settings.from_env(
        {**env, "CLAUDE_PLUGIN_ROOT": "/cp", "CLAUDE_CONFIG_DIR": "/cc"}
    )
    assert str(s.plugin_root) == "/cp" and str(s.claude_home) == "/cc"
    assert Settings.from_env(env).plugin_root.name == "odoo-dev"
    default = '{"ghcr.io/crpaxton4/devcontainer-features/personal-features":{}}'
    assert Settings.from_env(env).additional_features == default
    empty = {**env, "DISPATCH_ADDITIONAL_FEATURES": ""}
    assert Settings.from_env(empty).additional_features == default
    explicit = {**env, "DISPATCH_ADDITIONAL_FEATURES": '{"x":{}}'}
    assert Settings.from_env(explicit).additional_features == '{"x":{}}'
    assert Settings.from_env(env).invariants == "host text"
    missing = tmp_path / "gone.md"
    with pytest.raises(SettingsError, match="invariants file") as caught:
        Settings.from_env({**env, "DISPATCH_INVARIANTS_FILE": str(missing)})
    assert str(missing) in str(caught.value)
    with pytest.raises(SettingsError, match="DISPATCH_CONFIG_ROOT"):
        Settings.from_env({"DISPATCH_REPOS_DIR": "/r"})
    assert (
        Settings.from_env(
            {k: v for k, v in env.items() if k != "DISPATCH_REPO_OWNER"}
        ).owner
        == ""
    )
    with pytest.raises(SettingsError, match="malformed"):
        Settings.from_env({**env, "MIN_FREE_GB": "x"})
    assert Settings.from_env(env).admit_timeout_s == 1800
    assert Settings.from_env({**env, "DISPATCH_ENTRY": ""}).entry == DEFAULT_ENTRY
    assert Settings.from_env({**env, "DISPATCH_ENTRY": "/p:w"}).entry == "/p:w"
    with pytest.raises(SettingsError, match="DISPATCH_AGENT is retired") as stale:
        Settings.from_env({**env, "DISPATCH_AGENT": "odoo-dev:odoo-dev-implementer"})
    assert "DISPATCH_ENTRY" in str(stale.value)
    assert (
        Settings.from_env({**env, "DISPATCH_ADMIT_TIMEOUT_S": "90"}).admit_timeout_s
        == 90
    )
    with pytest.raises(SettingsError, match="malformed"):
        Settings.from_env({**env, "DISPATCH_ADMIT_TIMEOUT_S": "soon"})
    assert normalize_project("QOC_Repo.x") == "qoc_repox"


def test_settings_refuse_a_bundle_root_the_container_cannot_see(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    invariants = tmp_path / "inv.md"
    invariants.write_text("host text", "utf-8")
    env = {
        "DISPATCH_REPOS_DIR": "/r",
        "DISPATCH_CONFIG_ROOT": "/c",
        "DISPATCH_REPO_OWNER": "o",
        "DISPATCH_INVARIANTS_FILE": str(invariants),
    }
    Settings.from_env(env)  # unset: the default sits under the odoo-dev directory
    inside = str(tmp_path / "home" / ".config" / "odoo-dev" / "x" / "artifacts")
    Settings.from_env({**env, "DISPATCH_ARTIFACTS_DIR": inside})
    with pytest.raises(SettingsError, match="not under") as caught:
        Settings.from_env({**env, "DISPATCH_ARTIFACTS_DIR": str(tmp_path / "out")})
    assert "DISPATCH_ARTIFACTS_DIR" in str(caught.value)


def _make_config(root, owner, repo):
    path = root / "github.com" / owner / repo / ".devcontainer" / "devcontainer.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")
    return path


def test_config_is_resolved_per_repo_under_its_own_owner(settings, tmp_path):
    root = tmp_path / "multi"
    a = _make_config(root, "owner-a", "repo-a")
    b = _make_config(root, "owner-b", "repo-b")
    s = dataclasses.replace(settings, config_root=root, owner="")
    assert (s.config("repo-a"), s.config("repo-b")) == (a, b)
    # A tie-breaker that names the wrong owner falls back to the glob.
    wrong = dataclasses.replace(s, owner="owner-b")
    assert wrong.config("repo-a") == a


def test_config_owner_breaks_a_tie(settings, tmp_path):
    root = tmp_path / "tie"
    _make_config(root, "owner-a", "shared")
    b = _make_config(root, "owner-b", "shared")
    s = dataclasses.replace(settings, config_root=root, owner="owner-b")
    assert s.config("shared") == b


def test_config_with_no_match_names_the_repo_and_pattern(settings, tmp_path):
    s = dataclasses.replace(settings, config_root=tmp_path / "none", owner="")
    with pytest.raises(ConfigResolutionError) as caught:
        s.config("ghost")
    assert "ghost" in caught.value.detail
    assert "github.com/*/ghost/.devcontainer/devcontainer.json" in caught.value.detail


def test_config_with_duplicate_matches_lists_the_candidates(settings, tmp_path):
    root = tmp_path / "dup"
    a = _make_config(root, "owner-a", "shared")
    b = _make_config(root, "owner-b", "shared")
    s = dataclasses.replace(settings, config_root=root, owner="")
    with pytest.raises(ConfigResolutionError) as caught:
        s.config("shared")
    assert "more than one" in caught.value.detail
    assert str(a) in caught.value.detail and str(b) in caught.value.detail


# -- schema and the success test ----------------------------------------


def test_schema_argument_is_compact_json():
    arg = schema.schema_argument()
    assert json.loads(arg) == schema.RUN_SCHEMA
    assert " " not in arg.replace("jsonschema", "")


@pytest.mark.parametrize(
    "envelope",
    [
        None,
        {},
        {"structured_output": "x"},
        {"structured_output": {"summary": "", "outcome": "done"}},
        {"structured_output": {"summary": "s", "outcome": "great"}},
        {"structured_output": {"outcome": "done"}},
    ],
)
def test_unusable_structured_values(envelope):
    assert schema.usable_structured(envelope) is None


def test_question_count():
    assert schema.question_count({"questions": [1, 2]}) == 2
    assert schema.question_count({}) == 0


# -- the ten dead-letter causes -----------------------------------------


def test_every_cause_has_a_full_statement():
    assert set(deadletter.STATEMENTS) == set(DeadReason)
    assert all(s.endswith(".") and len(s) > 30 for s in deadletter.STATEMENTS.values())


def test_success_is_never_a_status_field():
    # subtype success + is_error true, yet a usable value arrived: success.
    env = {"subtype": "success", "is_error": True, "structured_output": GOOD}
    assert classify(Ending(1, saw_output=True, envelope=env)) is None
    # subtype success + is_error false but no structured value: a failure.
    env = {"subtype": "success", "is_error": False}
    reason, detail = classify(Ending(0, saw_output=True, envelope=env))
    assert reason is DeadReason.STRUCTURED_OUTPUT_MISSING
    assert "subtype=success" in detail and "is_error=false" in detail


CASES = [
    (Ending(-9, deadline_hit=True, saw_output=True), DeadReason.KILLED_BY_DEADLINE),
    (Ending(-15, saw_output=True), DeadReason.KILLED_BY_SIGNAL),
    (Ending(127), DeadReason.SESSION_LAUNCH_FAILED),
    (Ending(0), DeadReason.SESSION_LAUNCH_FAILED),
    (Ending(2, saw_output=True), DeadReason.SESSION_EXIT_NONZERO),
    (
        Ending(1, saw_output=True, envelope={"subtype": "error_max_turns"}),
        DeadReason.STRUCTURED_OUTPUT_MISSING,
    ),
    (
        Ending(0, saw_output=True, unreadable=True),
        DeadReason.STRUCTURED_OUTPUT_UNREADABLE,
    ),
    (Ending(0, saw_output=True), DeadReason.STRUCTURED_OUTPUT_UNREADABLE),
]


@pytest.mark.parametrize("ending,expected", CASES)
def test_classification(ending, expected):
    reason, detail = classify(ending)
    assert reason is expected
    assert deadletter.STATEMENTS[expected] in detail


def test_detail_carries_everything_seen():
    ending = Ending(
        -9,
        saw_output=True,
        envelope={"terminal_reason": "api_error", "errors": ["x"], "num_turns": 4},
        stderr_tail="boom",
    )
    _, detail = classify(ending)
    assert "signal=9" in detail and "terminal_reason=api_error" in detail
    assert (
        'errors=["x"]' in detail
        and "num_turns=4" in detail
        and "stderr tail: boom" in detail
    )


def test_brief_with_a_nul_or_too_large_is_rejected_at_submission(tmp_path):
    nul = _brief(tmp_path, "nul.md", "a\0b")
    big = _brief(tmp_path, "big.md", "x" * (queue.MAX_PROMPT_BYTES + 1))
    for path, needle in ((nul, "NUL"), (big, "too large")):
        arg = json.dumps([{"repo": "a", "task": "1", "brief": path}])
        with pytest.raises(queue.SubmissionError, match=needle):
            queue.parse_submission(arg)
    ok = _brief(tmp_path, "ok.md", "x" * (queue.MAX_PROMPT_BYTES - 10))
    queue.parse_submission(json.dumps([{"repo": "a", "task": "1", "brief": ok}]))
    assert JobStore().list_by_state(JobState.PENDING) == []  # nothing stored


def test_lock_error_names_the_holder(tmp_path):
    import os

    path = tmp_path / "dispatcher.lock"
    with DispatcherLock(path):
        with pytest.raises(AlreadyRunningError, match=f"pid {os.getpid()}"):
            DispatcherLock(path).acquire()
    path.write_text("not a pid")
    held = os.open(path, os.O_RDWR)
    import fcntl

    fcntl.flock(held, fcntl.LOCK_EX)
    with pytest.raises(AlreadyRunningError, match="pid unknown"):
        DispatcherLock(path).acquire()
    os.close(held)


def test_valid_events_without_a_result_is_missing_not_unreadable():
    ending = Ending(0, saw_output=True, parsed_any=True)
    assert classify(ending)[0] is DeadReason.STRUCTURED_OUTPUT_MISSING


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.pop("tests"),
        lambda v: v.pop("modules"),
        lambda v: v.update(tests={"ran": True, "passed": True, "failures": []}),
        lambda v: v.update(tests={"ran": 1, "passed": True}),
        lambda v: v.update(modules="m"),
        lambda v: v.update(questions=[{"question": "q"}]),
        lambda v: v.update(friction=[{"tool": "t"}]),
        lambda v: v.update(summary="   "),
        lambda v: v.update(changes=[1]),
    ],
)
def test_incomplete_structured_values_are_not_usable(mutate):
    value = json.loads(json.dumps(GOOD))
    mutate(value)
    assert schema.usable_structured({"structured_output": value}) is None
    assert schema.usable_structured({"structured_output": GOOD}) is not None


def test_prompt_size_boundary_is_inclusive(tmp_path):
    def arg(size):
        return json.dumps(
            [
                {
                    "repo": "a",
                    "task": "1",
                    "brief": _brief(tmp_path, f"b{size}", "x" * size),
                }
            ]
        )

    exactly = queue.MAX_PROMPT_BYTES - len("1\n\n")
    queue.parse_submission(arg(exactly))  # the limit itself is allowed
    with pytest.raises(queue.SubmissionError, match="too large"):
        queue.parse_submission(arg(exactly + 1))


def test_a_nul_in_the_brief_path_is_a_submission_error():
    arg = json.dumps([{"repo": "a", "task": "1", "brief": "a\0b"}])
    with pytest.raises(queue.SubmissionError, match="not readable"):
        queue.parse_submission(arg)


def test_outcome_enum_is_checked():
    value = {**GOOD, "outcome": "great"}
    assert schema.usable_structured({"structured_output": value}) is None


def test_an_integral_float_is_an_integer_but_a_bool_is_not():
    ok = {**GOOD, "tests": {"ran": 3.0, "passed": True, "failures": []}}
    assert schema.usable_structured({"structured_output": ok}) is not None
    for bad in (3.5, True, "3"):
        value = {**GOOD, "tests": {"ran": bad, "passed": True, "failures": []}}
        assert schema.usable_structured({"structured_output": value}) is None
