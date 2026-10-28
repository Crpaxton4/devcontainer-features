PYTHON_CMD        = uv run python

.PHONY: test coverage format static mutation report quality all clean help

# Default help target
help:
	@echo "Primary Commands:"
	@echo "  make quality"
	@echo "  make clean"
	@echo "-------------------------------------------------------------------"
	@echo "Testing:"
	@echo "  make coverage    - run coverage"
	@echo "  make format      - reformat src/tests/tools with black"
	@echo "  make static      - run static analysis (black + radon + complexipy)"
	@echo "  make mutation    - run mutation testing (cosmic-ray)"
	@echo "  make report      - generate unified quality-report.md"
	@echo "  make quality     - full pipeline (coverage + static + mutation + report)"
	@echo ""
	@echo "Housekeeping:"
	@echo "  make clean       - remove reports and caches"


# ─────────────────────────────────────────────
# Core test execution
# ─────────────────────────────────────────────
coverage:
	$(PYTHON_CMD) tools/coverage.py

# ─────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────

# The fix half of the `black --check` gate inside `make static`.
format:
	uv run --group static-analysis black src tests tools

# ─────────────────────────────────────────────
# Static analysis
# ─────────────────────────────────────────────

static:
	$(PYTHON_CMD) tools/static_analysis.py

# ─────────────────────────────────────────────
# Mutation testing
# ─────────────────────────────────────────────

mutation:
	$(PYTHON_CMD) tools/mutation.py

# ─────────────────────────────────────────────
# Report generation
# ─────────────────────────────────────────────

report:
	$(PYTHON_CMD) tools/report.py

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
	rm -rf .complexipy_cache
	rm -rf .hypothesis
	rm -rf *.egg-info
	rm .coverage
