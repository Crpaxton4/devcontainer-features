"""Test doubles. Every subprocess is mocked: nothing here builds a container."""

import io
import json
import signal
import threading

from odoo_dev_data.home import task_dir

from dispatcher.lifecycle import Completed


class PipeHeldOpen:
    """A stdout whose pipe a grandchild still holds: iteration never reaches EOF."""

    def __init__(self, lines=()):
        self._lines = [line + "\n" for line in lines]
        self.never = threading.Event()

    def __iter__(self):
        yield from self._lines
        self.never.wait(30)


class FakeProc:
    """A Popen stand-in fed from a script of stdout lines."""

    def __init__(self, lines=(), returncode=0, stderr="", hang=False, pipe_held=False):
        self.stdout = (
            PipeHeldOpen(lines)
            if pipe_held
            else io.StringIO("".join(line + "\n" for line in lines))
        )
        self.stderr = PipeHeldOpen() if pipe_held else io.StringIO(stderr)
        self._rc = returncode
        self._done = threading.Event()
        if not hang:
            self._done.set()
        self.killed = False
        self.terminated = False
        self.pid = 4242

    def poll(self):
        return self._rc if self._done.is_set() else None

    def wait(self):
        self._done.wait(5)
        return self._rc

    def kill(self):
        self.killed = True
        self._rc = -9
        self._done.set()

    def terminate(self):
        self.terminated = True
        self._rc = -15
        self._done.set()


class FakeShell:
    """Scripted Shell. ``run`` answers by the first matching prefix."""

    def __init__(self):
        self.calls = []
        self.timeouts = []
        self.groups = []
        self.spawned = []
        self.replies = []  # (prefix list, Completed)
        self.queued = []  # (prefix list, Completed), each used once, first
        self.proc = FakeProc()
        self.spawn_error = None

    def reply(self, prefix, completed):
        self.replies.append((prefix, completed))

    def queue_reply(self, prefix, *completed):
        """Answer the next matching calls in order, then fall back to ``reply``."""
        self.queued.extend((prefix, one) for one in completed)

    def run(self, argv, *, env, timeout=None):
        self.calls.append((list(argv), env))
        self.timeouts.append((list(argv), timeout))
        for index, (prefix, completed) in enumerate(self.queued):
            if argv[: len(prefix)] == prefix:
                del self.queued[index]
                return completed
        for prefix, completed in self.replies:
            if argv[: len(prefix)] == prefix:
                return completed
        return Completed(0, "", "")

    def killpg(self, proc, sig):
        self.groups.append(sig)
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()

    def spawn(self, argv, *, env):
        self.spawned.append((list(argv), env))
        if self.spawn_error:
            raise self.spawn_error
        return self.proc

    def commands(self, *prefix):
        return [c for c, _ in self.calls if c[: len(prefix)] == list(prefix)]


def stream(session_id="sess-1", structured=None, **result):
    """stream-json lines: an init event and a result envelope."""
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": session_id,
        "num_turns": 3,
    }
    if structured is not None:
        envelope["structured_output"] = structured
    envelope.update(result)
    return [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "claude_code_version": "9.9.9",
            }
        ),
        json.dumps(envelope),
    ]


def write_task_record(home, task_id, text):
    """Write the agent's record where the plugin does: tasks/<task_id>/task.json."""
    path = task_dir(task_id, home) / "task.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


GOOD = {
    "summary": "did the thing",
    "outcome": "done",
    "modules": ["m"],
    "changes": [],
    "tests": {"ran": 1, "passed": True, "failures": []},
    "questions": [],
    "friction": [],
}
