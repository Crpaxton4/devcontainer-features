# Sphinx documentation

SPHINX_CMD_OPTS   ?=
SPHINX_CMD_BUILD  ?= uv run sphinx-build
SPHINX_SOURCE_DIR = docs/source
SPHINX_BUILD_DIR  = docs/build
PYTHON_CMD        = uv run python

.PHONY: test coverage static mutation report quality all clean help

# Default help target
help:
	@echo "Primary Commands:"
	@echo "  make quality"
	@echo "  make docs"
	@echo "  make clean"
	@echo "-------------------------------------------------------------------"
	@echo "Docs:"
	@echo "  make html        - build documentation"
	@echo "  make linkcheck   - check external links"
	@echo "  make docs        - full documentation pipeline (linkcheck + html)"
	@echo ""
	@echo "Testing:"
	@echo "  make coverage    - run coverage"
	@echo "  make static      - run static analysis (radon + complexipy)"
	@echo "  make mutation    - run mutation testing (cosmic-ray)"
	@echo "  make report      - generate unified quality-report.md"
	@echo "  make quality     - full pipeline (coverage + static + mutation + report)"
	@echo ""
	@echo "Housekeeping:"
	@echo "  make clean       - remove reports and caches"


# ─────────────────────────────────────────────
# Sphinx
# ─────────────────────────────────────────────

html:
	$(SPHINX_CMD_BUILD) -b html $(SPHINX_SOURCE_DIR) $(SPHINX_BUILD_DIR)

linkcheck:
	$(SPHINX_CMD_BUILD) -b linkcheck $(SPHINX_SOURCE_DIR) $(SPHINX_BUILD_DIR)

docs: linkcheck html


# ─────────────────────────────────────────────
# Core test execution
# ─────────────────────────────────────────────
coverage:
	$(PYTHON_CMD) tools/odoo_sdk/coverage.py

# ─────────────────────────────────────────────
# Static analysis
# ─────────────────────────────────────────────

static:
	$(PYTHON_CMD) tools/odoo_sdk/static_analysis.py

# ─────────────────────────────────────────────
# Mutation testing
# ─────────────────────────────────────────────

mutation:
	$(PYTHON_CMD) tools/odoo_sdk/mutation.py

# ─────────────────────────────────────────────
# Report generation
# ─────────────────────────────────────────────

report:
	$(PYTHON_CMD) tools/odoo_sdk/report.py

# ─────────────────────────────────────────────
# Full quality pipeline
# ─────────────────────────────────────────────

quality: coverage static mutation report

# ─────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────

clean:
	rm -rf reports
	rm -rf .cosmic-ray
	rm -rf $(SPHINX_BUILD_DIR)
	rm -rf $(SPHINX_SOURCE_DIR)/api
	rm -rf .complexipy_cache
	rm -rf .hypothesis
	rm -rf *.egg-info
	rm .coverage
