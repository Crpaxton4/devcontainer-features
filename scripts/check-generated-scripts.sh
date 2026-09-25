#!/usr/bin/env bash
# check-generated-scripts.sh — syntax-check every program install.sh generates,
# and every Python program embedded in it (#872).
#
# install.sh writes its provisioning programs into place with quoted heredocs:
#
#     cat > /usr/local/bin/sync-claude-mcp << 'EOF'
#     ...
#     EOF
#
# To install.sh's OWN parser that body is inert literal text, so
# `bash -n install.sh` stays green no matter how broken the generated program
# is. The damage exists only in the file the heredoc writes, and until this
# gate nothing ever parsed that file.
#
# The specific trap from #872: inside those bodies, Python is handed to
# `python3 -c` as a SINGLE-QUOTED shell string, so one apostrophe anywhere in
# the program — an ordinary English one in a comment is enough — closes the
# quote early, truncates the program and mangles the remainder into shell
# words. Observed live: a comment reading "this script's private file" shipped
# a broken sync-claude-mcp, and every local gate was green.
#
# What this does, for each install.sh it is given:
#
#   1. `bash -n` over the file as a whole, so this is one command rather than
#      two.
#   2. Every `cat > <target> << '<DELIM>'` block is found BY PATTERN — never by
#      a hard-coded list of names, because that list changes every release —
#      and its body is extracted and parsed: `sh -n` or `bash -n` per its
#      shebang, plus `shellcheck -S error` in the matching dialect when the
#      linter is on PATH.
#   3. Every embedded Python program, in those extracted bodies AND in
#      install.sh itself, in both spellings it is written in here —
#      `python3 - [args] <<'DELIM'` heredocs and `python3 -c '<program>'`
#      single-quoted strings — is ast.parse()d. The single-quoted form is
#      extracted the way the shell itself parses a single-quoted string (a bare
#      apostrophe ends it; the escape idioms are '\'' and '"'"'), so an
#      apostrophe that would break the shell surfaces here as a truncated
#      program that fails to parse. Nothing is expanded: inside single quotes
#      and quoted heredocs the program text is literal, which is exactly why it
#      can be parsed statically at all.
#
# Blocks whose heredoc delimiter is UNQUOTED are reported and skipped: the
# shell expands those bodies, so the literal text is not the program that ships.
#
# Usage: check-generated-scripts.sh [--help] [--allow-no-blocks] [<install.sh> ...]
# Exit codes: 0 when everything parses, 1 on any finding.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_TARGET="$HERE/../devcontainer-features/src/personal-features/install.sh"

usage() {
    cat <<'USAGE'
usage: check-generated-scripts.sh [--help] [--allow-no-blocks] [<install.sh> ...]

Extracts every script install.sh generates with a quoted heredoc and syntax-
checks it, then ast.parse()s every Python program embedded in those scripts and
in install.sh itself. With no argument, checks the personal-features install.sh.

Options:
  --allow-no-blocks  do not fail when a file generates no scripts at all
                     (by default that is an error: it means the extraction
                     pattern has drifted and this gate is checking nothing)
  --help             show this message

Exit codes: 0 clean, 1 on any finding.
USAGE
}

ALLOW_NO_BLOCKS=0
TARGETS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --help | -h)
            usage
            exit 0
            ;;
        --allow-no-blocks)
            ALLOW_NO_BLOCKS=1
            shift
            ;;
        --)
            shift
            while [ $# -gt 0 ]; do
                TARGETS+=("$1")
                shift
            done
            ;;
        -*)
            printf 'check-generated-scripts.sh: unknown option %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
        *)
            TARGETS+=("$1")
            shift
            ;;
    esac
done
if [ "${#TARGETS[@]}" -eq 0 ]; then
    TARGETS=("$DEFAULT_TARGET")
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/check-generated-scripts.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

FINDINGS=0
finding() {
    printf 'FAIL %s\n' "$*" >&2
    FINDINGS=$((FINDINGS + 1))
}
note() { printf 'note: %s\n' "$*"; }

if command -v shellcheck >/dev/null 2>&1; then
    HAVE_SHELLCHECK=1
else
    HAVE_SHELLCHECK=0
    note "shellcheck is not on PATH; generated scripts get sh -n/bash -n only (CI installs shellcheck)"
fi

# ---------------------------------------------------------------------------
# The embedded-Python scanner. Written out here rather than kept beside this
# script so the gate stays a single file to copy, run and reason about.
# ---------------------------------------------------------------------------
PYCHECK="$WORK/embedded-python-check.py"
cat > "$PYCHECK" <<'EMBEDDED_PYTHON_CHECK'
"""Parse every Python program embedded in a shell file (#872).

argv: <file-to-scan> <display-path> <label> <line-offset>

Line numbers are reported against <display-path> after adding <line-offset>, so
a program extracted out of an install.sh heredoc is reported at its real
install.sh line rather than at its line in some temp file.

Two spellings are recognised, because install.sh uses both:

    python3 - [args] <<'DELIM'      ... quoted heredoc, literal text
    python3 -c '<program>'          ... single-quoted shell string, literal text

The second is the #872 bug's home: the extraction below reproduces the shell's
own parse of a single-quoted string, so an apostrophe inside the program ends
the string early here exactly as it would in the shell, and what gets parsed is
the truncated program the container would actually have run.
"""

import ast
import re
import sys

# `python3 -` (a script on stdin), any args, then a quoted heredoc delimiter.
# The lookahead keeps this from matching `python3 -c`.
HEREDOC_RE = re.compile(
    r"""python3\s+-(?=[\s'"]|$)[^\n]*?<<(-?)\s*(['"])([A-Za-z_][A-Za-z0-9_]*)\2"""
)
DASH_C_RE = re.compile(r"""python3\s+-c\s+(['"])""")


def parse_error(program):
    """Return (line-within-program, message) for a bad program, else None."""
    try:
        ast.parse(program)
    except SyntaxError as exc:
        return (exc.lineno or 1, "%s (line %d of the program)" % (exc.msg, exc.lineno or 1))
    except ValueError as exc:  # e.g. a NUL byte from a mangled extraction
        return (1, str(exc))
    return None


def read_single_quoted(text, pos):
    """Read a shell single-quoted string whose opening quote ends at pos.

    A bare apostrophe ends the string. The two idioms that put an apostrophe
    INSIDE one are concatenations, not escapes: '\\'' and '"'"'. Two adjacent
    quoted strings are concatenated too. Returns (value, end) or (None, end)
    when the quote is never closed.
    """
    out = []
    while True:
        end = text.find("'", pos)
        if end == -1:
            return (None, len(text))
        out.append(text[pos:end])
        if text[end + 1 : end + 4] == "\\''":
            out.append("'")
            pos = end + 4
            continue
        if text[end + 1 : end + 5] == "\"'\"'":
            out.append("'")
            pos = end + 5
            continue
        if text[end + 1 : end + 2] == "'":  # '' — adjacent strings concatenate
            pos = end + 2
            continue
        return ("".join(out), end + 1)


# What may legally follow the closing quote of a `python3 -c '...'` word. An
# ordinary character there means the quote closed in the middle of the program
# — i.e. an apostrophe inside the program ended it early (#872) — and the rest
# of the "program" is being parsed by the shell as words.
AFTER_QUOTE_OK = set(" \t\n;)|&<>")


def main():
    path, display, label, offset = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    where = "%s%s" % (display, (": " + label) if label else "")

    with open(path, "r", encoding="utf-8", errors="surrogateescape") as handle:
        text = handle.read()
    lines = text.split("\n")

    findings = []
    notices = []
    checked = 0
    consumed = set()

    index = 0
    while index < len(lines):
        match = HEREDOC_RE.search(lines[index])
        if not match:
            index += 1
            continue
        dash, _quote, delim = match.group(1), match.group(2), match.group(3)
        start = index + 1
        stop = start
        while stop < len(lines):
            candidate = lines[stop].strip() if dash else lines[stop]
            if candidate == delim:
                break
            stop += 1
        if stop >= len(lines):
            findings.append((index + 1, "python3 heredoc <<%s is never closed" % delim))
            index += 1
            continue
        consumed.update(range(start, stop))
        checked += 1
        error = parse_error("\n".join(lines[start:stop]))
        if error:
            findings.append((start + error[0], "python3 <<%s: %s" % (delim, error[1])))
        index = stop + 1

    # Heredoc bodies are blanked (not removed) before the `-c` sweep: the line
    # count has to survive so reported line numbers stay true, and a `python3
    # -c '` that lives inside a Python heredoc must not be scanned twice.
    masked = "\n".join("" if i in consumed else line for i, line in enumerate(lines))

    for match in DASH_C_RE.finditer(masked):
        line_no = masked.count("\n", 0, match.end()) + 1
        if match.group(1) == '"':
            notices.append(
                (line_no, 'python3 -c "..." is double-quoted, so the shell expands it; not parsed here')
            )
            continue
        program, end = read_single_quoted(masked, match.end())
        if program is None:
            findings.append((line_no, "python3 -c: single quote is never closed"))
            continue
        checked += 1
        if end < len(masked) and masked[end] not in AFTER_QUOTE_OK:
            findings.append(
                (
                    masked.count("\n", 0, end) + 1,
                    "python3 -c: the single-quoted program ends at an apostrophe here, mid-program — "
                    "an apostrophe inside the program closes the shell quote early and ships a "
                    "truncated program (#872); write it as '\\'' or drop the apostrophe",
                )
            )
        error = parse_error(program)
        if error:
            findings.append((line_no + error[0] - 1, "python3 -c: %s" % error[1]))

    for line_no, message in notices:
        print("note: %s:%d: %s" % (display, line_no + offset, message))
    for line_no, message in findings:
        print("FAIL %s:%d: %s" % (where, line_no + offset, message), file=sys.stderr)
    if findings:
        return 1
    if checked:
        print("%s: %d embedded python program(s) ok" % (where, checked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
EMBEDDED_PYTHON_CHECK

# ---------------------------------------------------------------------------

# `cat > <target> << <delim>`, delimiter quoted or not — both are captured so an
# unquoted one can be REPORTED rather than silently ignored.
BLOCK_RE='^[[:space:]]*cat[[:space:]]*>>?[[:space:]]*([^[:space:]]+)[[:space:]]*<<(-?)[[:space:]]*([^[:space:]]+)[[:space:]]*$'
# Any heredoc-writing cat at all. A line that is one of these but does not match
# BLOCK_RE means the form has drifted and this gate would check nothing, which
# is the #872 failure mode all over again — so it is a finding, not a shrug.
CAT_HEREDOC_RE='^[[:space:]]*cat[[:space:]]*>>?.*<<'

check_embedded_python() {  # <file> <display> <label> <offset>
    if python3 "$PYCHECK" "$1" "$2" "$3" "$4"; then
        return 0
    fi
    FINDINGS=$((FINDINGS + 1))
    return 1
}

# sh/bash/shellcheck all report against the extracted temp file. Rewrite that
# path to the block it came from and say how to turn its line numbers into
# install.sh line numbers — otherwise the reader is handed a line number for a
# file that no longer exists by the time they read it.
report_tool_output() {  # <body-file> <display> <name>
    sed -e "s#$1#$2 ($3)#g" -e 's/^/    /' "$WORK/err" >&2
}

check_block() {  # <display> <name> <body-file> <offset>
    local display="$1" name="$2" body="$3" offset="$4"
    local shebang dialect ok=1
    local LINE_HINT="generated-script line N is $display line N+$offset"
    shebang="$(head -n 1 "$body")"
    case "$shebang" in
        '#!'*python*) dialect="python" ;;
        '#!'*bash*) dialect="bash" ;;
        # #!/bin/sh, or no shebang at all: read it as POSIX sh, the stricter of
        # the two — a bashism in a /bin/sh script is a runtime failure in the
        # container, not a style point.
        *) dialect="sh" ;;
    esac

    if [ "$dialect" = "python" ]; then
        if python3 -c 'import ast, sys; ast.parse(sys.stdin.read())' < "$body" 2>"$WORK/err"; then
            :
        else
            ok=0
            finding "$display: $name: generated python does not parse ($LINE_HINT):"
            sed 's/^/    /' "$WORK/err" >&2
        fi
    else
        if "$dialect" -n "$body" 2>"$WORK/err"; then
            :
        else
            ok=0
            finding "$display: $name: $dialect -n failed on the generated script ($LINE_HINT):"
            report_tool_output "$body" "$display" "$name"
        fi
        if [ "$HAVE_SHELLCHECK" -eq 1 ]; then
            if shellcheck -s "$dialect" -S error "$body" >"$WORK/err" 2>&1; then
                :
            else
                ok=0
                finding "$display: $name: shellcheck -s $dialect -S error failed ($LINE_HINT):"
                report_tool_output "$body" "$display" "$name"
            fi
        fi
    fi

    if ! check_embedded_python "$body" "$display" "$name" "$offset"; then
        ok=0
    fi

    if [ "$ok" -eq 1 ]; then
        printf '%s: ok\n' "$name"
    fi
}

check_file() {  # <install.sh>
    local file="$1"
    if [ ! -f "$file" ]; then
        finding "$file: not a readable file"
        return
    fi
    printf '== %s\n' "$file"

    if bash -n "$file" 2>"$WORK/err"; then
        printf 'bash -n: ok\n'
    else
        finding "$file: bash -n failed:"
        sed 's/^/    /' "$WORK/err" >&2
    fi

    local -a lines=()
    mapfile -t lines < "$file"
    local total=${#lines[@]}
    local masked="$WORK/masked.sh"
    : > "$masked"

    local index=0 blocks=0 seq=0
    while [ "$index" -lt "$total" ]; do
        local line="${lines[$index]}"
        if [[ ! "$line" =~ $CAT_HEREDOC_RE ]]; then
            printf '%s\n' "$line" >> "$masked"
            index=$((index + 1))
            continue
        fi
        if [[ ! "$line" =~ $BLOCK_RE ]]; then
            finding "$file:$((index + 1)): unrecognised 'cat > … <<' heredoc form; the extraction pattern in $(basename "$0") needs updating, otherwise this block ships unchecked"
            printf '%s\n' "$line" >> "$masked"
            index=$((index + 1))
            continue
        fi

        local target="${BASH_REMATCH[1]}" dash="${BASH_REMATCH[2]}" raw_delim="${BASH_REMATCH[3]}"
        local quoted=0 delim="$raw_delim"
        case "$raw_delim" in
            "'"*"'" | '"'*'"')
                quoted=1
                delim="${raw_delim:1:${#raw_delim}-2}"
                ;;
        esac
        local name
        name="$(basename "${target//[\"\']/}")"

        local start=$((index + 1)) stop=$((index + 1)) found=0
        while [ "$stop" -lt "$total" ]; do
            local candidate="${lines[$stop]}"
            if [ -n "$dash" ]; then
                candidate="${candidate#"${candidate%%[![:space:]]*}"}"
            fi
            if [ "$candidate" = "$delim" ]; then
                found=1
                break
            fi
            stop=$((stop + 1))
        done
        if [ "$found" -eq 0 ]; then
            finding "$file:$((index + 1)): $name: heredoc delimiter '$delim' is never closed"
            printf '%s\n' "$line" >> "$masked"
            index=$((index + 1))
            continue
        fi

        seq=$((seq + 1))
        local body="$WORK/body-$seq-$name"
        : > "$body"
        local cursor="$start"
        printf '%s\n' "$line" >> "$masked"
        while [ "$cursor" -lt "$stop" ]; do
            printf '%s\n' "${lines[$cursor]}" >> "$body"
            printf '\n' >> "$masked"
            cursor=$((cursor + 1))
        done
        printf '%s\n' "${lines[$stop]}" >> "$masked"

        # A block is a PROGRAM when it lands in /usr/local/bin (every generated
        # command does) or when its body opens with a shebang (the git hooks).
        # Anything else — a .gitignore, a bashrc fragment — is data, and running
        # a shell parser over data invents failures.
        local is_program=0
        if [ "${target#*/usr/local/bin/}" != "$target" ] || [ "$(head -c 2 "$body")" = '#!' ]; then
            is_program=1
        fi
        if [ "$quoted" -eq 0 ]; then
            note "$file:$((index + 1)): $name is written from an UNQUOTED heredoc, so the shell expands the body; its literal text is not the program that ships and is not checked here"
        elif [ "$is_program" -eq 1 ]; then
            blocks=$((blocks + 1))
            check_block "$file" "$name" "$body" "$start"
        else
            note "$file:$((index + 1)): $name has no '#!' line and is not a program; not syntax-checked"
        fi

        index=$((stop + 1))
    done

    # install.sh's own embedded Python, with every heredoc body blanked out so
    # nothing inside a generated script is reported twice.
    check_embedded_python "$masked" "$file" "" 0 || true

    if [ "$blocks" -eq 0 ] && [ "$ALLOW_NO_BLOCKS" -eq 0 ]; then
        finding "$file: no generated scripts were found at all; the extraction pattern has drifted (pass --allow-no-blocks if this file really generates none)"
    else
        printf '%d generated script(s) checked in %s\n' "$blocks" "$file"
    fi
}

for target in "${TARGETS[@]}"; do
    check_file "$target"
done

if [ "$FINDINGS" -gt 0 ]; then
    printf '\n%d finding(s).\n' "$FINDINGS" >&2
    exit 1
fi
printf '\nAll generated scripts and embedded python programs parse.\n'
