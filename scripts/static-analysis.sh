#!/usr/bin/env bash
# static-analysis.sh — Run all Radon static analysis metrics against src/odoo_sdk
# Produces JSON reports in reports/radon/ and streams results to terminal.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$REPO_ROOT/src/odoo_sdk"
REPORTS_DIR="$REPO_ROOT/reports/radon"

cd "$REPO_ROOT"

mkdir -p "$REPORTS_DIR"

# ── Cyclomatic Complexity ────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  Cyclomatic Complexity (cc)"
echo "════════════════════════════════════════════════════════"
radon cc "$SRC" --show-complexity --average --json | tee "$REPORTS_DIR/cc.json"

# ── Raw Metrics ──────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  Raw Metrics (raw)"
echo "════════════════════════════════════════════════════════"
radon raw "$SRC" --summary --json | tee "$REPORTS_DIR/raw.json"

# ── Maintainability Index ────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  Maintainability Index (mi)"
echo "════════════════════════════════════════════════════════"
radon mi "$SRC" --show --json | tee "$REPORTS_DIR/mi.json"

# ── Halstead Metrics ─────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  Halstead Metrics (hal)"
echo "════════════════════════════════════════════════════════"
radon hal "$SRC" --json | tee "$REPORTS_DIR/hal.json"

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Reports written to: $REPORTS_DIR"
echo "════════════════════════════════════════════════════════"
echo ""
