RUN ?= uv run

.PHONY: coverage format static

# Measured, not claimed: `coverage report` fails below fail_under (pyproject.toml).
coverage:
	$(RUN) coverage run -m pytest tests
	$(RUN) coverage report
	mkdir -p reports/coverage
	$(RUN) coverage json -o reports/coverage/coverage.json

# The gate CI runs: formatting and complexity (max-complexity-allowed in pyproject.toml).
static:
	$(RUN) black --check src tests
	$(RUN) complexipy src

format:
	$(RUN) black src tests
