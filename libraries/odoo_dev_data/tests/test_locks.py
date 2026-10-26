import os
import subprocess
import sys
import threading

import pytest

from odoo_dev_data.locks import LockHeldError, exclusive


def test_second_holder_times_out_then_succeeds(tmp_path):
    with exclusive("a", home=tmp_path) as path:
        with pytest.raises(LockHeldError) as info:
            with exclusive("a", home=tmp_path, timeout=0.2):
                pass
        assert info.value.name == "a"
        assert info.value.path == path
    with exclusive("a", home=tmp_path, timeout=0.2):
        pass


def test_other_name_is_independent(tmp_path):
    with exclusive("a", home=tmp_path):
        with exclusive("b", home=tmp_path):
            pass


def test_waiter_gets_lock_after_release(tmp_path):
    got = []

    def waiter():
        with exclusive("a", home=tmp_path, timeout=3.0):
            got.append(True)

    with exclusive("a", home=tmp_path):
        thread = threading.Thread(target=waiter)
        thread.start()
    thread.join(5)
    assert got == [True]


def test_other_process_is_blocked(tmp_path):
    code = (
        "import sys; from pathlib import Path\n"
        "from odoo_dev_data.locks import exclusive, LockHeldError\n"
        "try:\n"
        "    with exclusive('a', home=Path(sys.argv[1])): pass\n"
        "except LockHeldError:\n"
        "    sys.exit(7)\n"
    )
    with exclusive("a", home=tmp_path):
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)], cwd="/tmp", check=False
        )
    assert result.returncode == 7


def test_lock_file_persists_with_pid(tmp_path):
    with exclusive("a", home=tmp_path) as path:
        assert path == tmp_path / "locks" / "a.lock"
        assert path.read_text() == f"{os.getpid()}\n"
    assert path.exists()
    assert (tmp_path / "tasks").is_dir()


@pytest.mark.parametrize("bad", ["", "a/b", ".x", "-x", "a b"])
def test_bad_names(bad, tmp_path):
    with pytest.raises(ValueError, match="lock name"):
        with exclusive(bad, home=tmp_path):
            pass
