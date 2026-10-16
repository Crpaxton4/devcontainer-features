RUN ?= uv run

.PHONY: coverage format

# Measured, not claimed: `coverage report` fails below fail_under (pyproject.toml).
coverage:
	$(RUN) coverage run -m pytest tests
	$(RUN) coverage report
	mkdir -p reports/coverage
	$(RUN) coverage json -o reports/coverage/coverage.json

format:
	$(RUN) black src tests
