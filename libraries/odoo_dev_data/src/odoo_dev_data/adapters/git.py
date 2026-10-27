"""Git helpers: the ``owner/repo`` label of a checkout."""

import subprocess


def derive_repo_label(remote_url: str) -> str:
    """Return the ``owner/repo`` label for a git remote URL.

    Strips a trailing ``.git`` and keeps the last two path segments so both ssh
    (``git@github.com:owner/repo.git``) and https
    (``https://github.com/owner/repo.git``) forms collapse to ``owner/repo``.
    Falls back to the cleaned URL when it has fewer than two segments.
    """
    cleaned = remote_url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    # Treat ``:`` (scp-like ssh separator, scheme ``://``) as a path separator so
    # both URL shapes split into the same segment list.
    segments = [seg for seg in cleaned.replace(":", "/").split("/") if seg]
    if len(segments) >= 2:
        return "/".join(segments[-2:])
    return cleaned or "(unknown)"


def current_repo_label() -> str:
    """Return the normalized ``owner/repo`` label for the cwd's git remote, or ''.

    Best-effort display metadata for events written from a working tree: a
    non-git cwd or a missing remote is not an error. It yields ``""``, which
    sessionizes as a repo-less session. ssh and https clones of one repo
    converge because :func:`derive_repo_label` normalizes both URL shapes.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""
    label = derive_repo_label(result.stdout.strip())
    return "" if label == "(unknown)" else label
