"""The ``owner/repo`` label helpers."""

import subprocess
from unittest.mock import patch

import pytest

from odoo_dev_data.adapters.git import current_repo_label, derive_repo_label


@pytest.mark.parametrize(
    "remote, label",
    [
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("ssh://git@host:2222/group/sub/repo.git", "sub/repo"),
        ("repo", "repo"),
        ("", "(unknown)"),
    ],
)
def test_derive_repo_label(remote: str, label: str) -> None:
    assert derive_repo_label(remote) == label


def test_current_repo_label_reads_the_origin_remote() -> None:
    done = subprocess.CompletedProcess([], 0, stdout="git@github.com:o/r.git\n")
    with patch("odoo_dev_data.adapters.git.subprocess.run", return_value=done):
        assert current_repo_label() == "o/r"


def test_current_repo_label_is_empty_without_a_remote() -> None:
    error = subprocess.CalledProcessError(2, ["git"])
    with patch("odoo_dev_data.adapters.git.subprocess.run", side_effect=error):
        assert current_repo_label() == ""


def test_current_repo_label_is_empty_without_git() -> None:
    with patch(
        "odoo_dev_data.adapters.git.subprocess.run", side_effect=FileNotFoundError
    ):
        assert current_repo_label() == ""
