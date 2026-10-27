"""Adapters that read external systems into the event store.

* :mod:`.git` - authored commits from local checkouts, and repo labels.
* :mod:`.github` - authored PRs, reviews and comments from the GitHub account.
* :mod:`.odoo` - the user's Odoo task chatter.
"""

from .git import current_repo_label, derive_repo_label, sync_git_log
from .github import sync_github
from .odoo import sync_odoo_chatter

__all__ = [
    "current_repo_label",
    "derive_repo_label",
    "sync_git_log",
    "sync_github",
    "sync_odoo_chatter",
]
