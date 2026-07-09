PYTHON ?= .venv/bin/python

.PHONY: verify
verify:
	$(PYTHON) verify_project.py
