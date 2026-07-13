PYTHON ?= .venv/bin/python

.PHONY: verify
verify:
	$(PYTHON) -m reality.verify_project
