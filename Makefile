.PHONY: dev lint type test qa artifact-test matrix live eval formal check-install-guidance

dev:
	python -m pip install -e ".[dev]"

lint:
	ruff check drex_policy tests examples eval scripts

type:
	pyright

test:
	python -m pytest

qa: lint type test

artifact-test:
	python scripts/artifact_test.py

matrix:
	python scripts/compat_matrix.py

live:
	python -m pytest -m live

eval:
	python -m eval.run $(ARGS)

check-install-guidance:
	python scripts/check_install_guidance.py

formal:
	python formal/run.py
