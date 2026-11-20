# Operational targets for the CBB prediction engine (Lean V1.0).
# Usage: make setup | test | ingest | simulate | audit | daily
#        make simulate TEAM1="Kansas" TEAM2="Houston" VENUE=home

PYTHON  ?= python3
VENV    := .venv
PY      := $(VENV)/bin/python
STAMP   := $(VENV)/.requirements-installed

TEAM1   ?= Duke
TEAM2   ?= Michigan St.
VENUE   ?= neutral
SEASON  ?= 2026

DAILY_HOUR   ?= 9
DAILY_MINUTE ?= 0

.PHONY: setup test ingest simulate predict audit import-v1 daily \
        install-daily daily-status uninstall-daily

setup: $(STAMP)

$(STAMP): requirements.txt
	test -x $(PY) || $(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	touch $@

test: setup
	$(PY) -m pytest -q

# Today's KenPom snapshot (ratings + subscriber 3PA). Credentials from .env.
ingest: setup
	$(PY) cli.py ingest

simulate: setup
	$(PY) cli.py simulate --team1 "$(TEAM1)" --team2 "$(TEAM2)" --venue "$(VENUE)"

import-v1: setup
	$(PY) cli.py import-v1

predict: setup
	$(PY) cli.py predict --season $(SEASON)

audit: setup
	$(PY) cli.py audit --season $(SEASON)

# Daily pipeline (cron/launchd). Publishing is added in Milestone 6.
daily: ingest

# macOS launchd agent running `make daily` (catches up after sleep).
install-daily: setup
	$(PY) ops/launchd.py install --hour $(DAILY_HOUR) --minute $(DAILY_MINUTE)

daily-status:
	$(PY) ops/launchd.py status

uninstall-daily:
	$(PY) ops/launchd.py uninstall
