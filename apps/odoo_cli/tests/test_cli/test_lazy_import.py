"""The state-maintenance subcommands must not load the logic layer or the clients."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

#: Print every module name that is loaded after ``main`` returns.
_PROBE = """\
import sys
from odoo_cli.__main__ import main

code = main(sys.argv[1:])
print("\\n".join(sorted(sys.modules)))
sys.exit(code)
"""

_HEAVY = ("odoo_dev_core", "odoo_client", "markitdown", "fastmcp", "textual")


@pytest.mark.parametrize("argv", [["repo-map", "list"], ["doctor"], ["--version"]])
def test_command_does_not_import_the_logic_layer(
    argv: list[str], tmp_path: Path
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    env = {**os.environ, "ODOO_DEV_HOME": str(home)}
    done = subprocess.run(
        [sys.executable, str(probe), *argv],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    loaded = set(done.stdout.split())
    assert "odoo_cli.__main__" in loaded
    leaked = sorted(m for m in loaded if m.split(".")[0] in _HEAVY)
    assert leaked == []
