"""Headless curses drive test for the TUI, via a pseudo-tty and ``pyte``.

The real ``curses`` render loop is exercised end-to-end without a physical
terminal: a child process attaches ``curses`` to a pty slave and runs the driver
loop against an in-memory fake registry (no live Odoo). The parent feeds
keystrokes over the pty, replays the byte stream through a ``pyte`` screen, and
asserts the loop paints a recognizable frame and survives a terminal resize
(``KEY_RESIZE``) before quitting cleanly.
"""

import os
import struct
import sys
import termios
import time
import unittest

try:
    import fcntl
    import pty

    _HAS_PTY = True
except ImportError:  # pragma: no cover - non-POSIX fallback
    _HAS_PTY = False

import pyte

ROWS, COLS = 24, 100


def _child_main(slave_fd: int) -> None:  # pragma: no cover - runs in child process
    """Attach curses to ``slave_fd`` and run the driver loop, then exit."""
    try:
        os.setsid()
    except OSError:
        pass
    fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
    os.dup2(slave_fd, 0)
    os.dup2(slave_fd, 1)
    os.dup2(slave_fd, 2)
    os.environ["TERM"] = "xterm-256color"

    import curses

    from odoo_sdk.tui.app import _loop
    from tests.test_tui._fake import build_fake_deps

    deps = build_fake_deps()
    try:
        curses.wrapper(_loop, deps)
    except Exception:
        os._exit(1)
    os._exit(0)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def _drain(master_fd: int, stream: "pyte.ByteStream", timeout: float = 2.0) -> None:
    """Feed pty output into the pyte stream until it goes quiet or times out.

    The child needs a moment to initialize curses and paint; a single poll can
    race that startup, so this keeps polling for the whole ``timeout`` and only
    stops early after a short idle gap once output has begun.
    """
    import select

    deadline = time.time() + timeout
    idle_deadline = None
    while time.time() < deadline:
        readable, _, _ = select.select([master_fd], [], [], 0.05)
        if not readable:
            if idle_deadline is not None and time.time() >= idle_deadline:
                break
            continue
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            break
        if not data:
            break
        stream.feed(data)
        idle_deadline = time.time() + 0.2


@unittest.skipUnless(_HAS_PTY, "pty/fcntl unavailable on this platform")
class TestPyteDrive(unittest.TestCase):
    def test_loop_paints_frame_resizes_and_quits(self):
        master_fd, slave_fd = pty.openpty()
        _set_winsize(slave_fd, ROWS, COLS)

        pid = os.fork()
        if pid == 0:  # pragma: no cover - child process
            os.close(master_fd)
            _child_main(slave_fd)

        os.close(slave_fd)
        screen = pyte.Screen(COLS, ROWS)
        stream = pyte.ByteStream(screen)

        # Let the first frame paint, retrying the drain until the header shows.
        first_frame = ""
        for _ in range(5):
            _drain(master_fd, stream)
            first_frame = "\n".join(screen.display)
            if "odoo-tui" in first_frame:
                break
        self.assertIn("odoo-tui", first_frame)

        # Drive a window move (Left arrow) and a resize, then quit.
        os.write(master_fd, b"\x1b[D")  # KEY_LEFT
        _drain(master_fd, stream)

        _set_winsize(master_fd, 20, 80)
        os.kill(pid, __import__("signal").SIGWINCH)
        _drain(master_fd, stream)

        os.write(master_fd, b"q")  # quit
        _drain(master_fd, stream)

        _pid, status = os.waitpid(pid, 0)
        os.close(master_fd)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 0)


if __name__ == "__main__":
    unittest.main()
