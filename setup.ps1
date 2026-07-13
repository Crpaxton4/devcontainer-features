#Requires -Version 5.1
<#
.SYNOPSIS
    One-time host setup for personal-features bind mounts on a native Windows host.

.DESCRIPTION
    The PowerShell counterpart to setup.sh, for hosts where setup.sh cannot run.
    Creates the host-side paths that are bind-mounted into the container. Run it
    once per machine before starting any dev container that uses the
    personal-features Feature.

    This is NOT optional: a bind mount whose source doesn't exist is a hard
    container-create failure ("bind source path does not exist"), not a fallback.

    Note this is about the host that *launches* VS Code, not the repo's location.
    If you open the repo through Remote-WSL, the container's mounts resolve
    against the WSL home directory, so run setup.sh inside WSL instead.

    The path list below must match persisted-paths.tsv (next to the Feature's
    install.sh), the single source of truth shared with setup.sh and
    devcontainer-feature.json. This list is hand-maintained rather than read from
    the manifest so that .github/scripts/test_host_setup_parity.py can still catch
    it drifting from the manifest - there is no Windows CI runner to catch it at
    runtime. Keep it in sync with the manifest row-for-row when adding a path.

    Safe to re-run: New-Item -Force is a no-op when the targets already exist.

.EXAMPLE
    .\setup.ps1

    If script execution is blocked by policy:
    powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$isWindowsHost = $env:OS -eq 'Windows_NT'

# The feature's mount sources are written as "${localEnv:HOME}${localEnv:USERPROFILE}/..."
# because the devcontainer spec has no conditional: exactly one of the two is
# expected to be defined, so they concatenate into a valid path on every host.
# Windows defines USERPROFILE; Linux/WSL/macOS define HOME. Prefer USERPROFILE
# here so this script targets the same directory the container will mount, and
# fall back to HOME so it stays runnable under pwsh on Linux (which is how CI
# exercises it).
$base = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
if (-not $base) {
    throw 'Neither USERPROFILE nor HOME is set; cannot locate the home directory.'
}

# The one real failure mode of that concat pattern: if HOME is *also* defined on
# Windows, both variables expand and the mount source becomes garbage - e.g.
# "/c/Users/ChrisC:\Users\Chris/.claude" - and the container fails to start.
# Git Bash sets HOME inside its own shell, so launching VS Code with `code .`
# from Git Bash leaks it into the container's environment. A HOME persisted as a
# User/Machine variable leaks into every launch, including from the Start menu.
# There is no config-level fix, so warn loudly and give the remediation.
if ($isWindowsHost) {
    $homeSources = @()
    if ($env:HOME) { $homeSources += 'the current process' }
    foreach ($scope in 'User', 'Machine') {
        if ([Environment]::GetEnvironmentVariable('HOME', $scope)) {
            $homeSources += "the $scope environment"
        }
    }

    if ($homeSources.Count -gt 0) {
        Write-Warning @"
HOME is set on this Windows host (in $($homeSources -join ', ')).

The feature's mount sources expand both HOME and USERPROFILE, expecting only one
to be defined. With both set, they concatenate into an invalid path and the
container will fail to start with a mount error naming a source that contains
both '/c/Users/...' and 'C:\Users\...'.

To fix, either:
  * remove the persisted HOME variable, or
  * launch VS Code from PowerShell or the Start menu rather than from Git Bash.
"@
    }
}

# Home-relative mount sources; keep in sync with persisted-paths.tsv (and hence
# setup.sh) row-for-row. All are directories today; a future file source would
# need New-Item -ItemType File instead.
#
# Unlike setup.sh, this script does NOT apply the manifest's mode column (#233):
# NTFS has no POSIX modes to chmod, dirs under the user profile are already
# private to the user by default ACL, and Docker Desktop on Windows synthesizes
# the in-container permission bits for shared paths regardless of anything set
# here - so there is nothing meaningful to enforce host-side on Windows.
$paths = @(
    '.claude'
    '.config/gh'
    '.config/odoo_sdk'
    '.config/pr-automation'
    '.config/coderabbit'
    '.config/devcontainer/shell-history'
)

foreach ($path in $paths) {
    $full = Join-Path $base $path
    New-Item -ItemType Directory -Force -Path $full | Out-Null
    Write-Host "ok  $full"
}
