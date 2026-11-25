PYTHON ?= python3
BIN := .venv/bin
EXTRACTOR ?= rules
export PYTHONPATH := src

.PHONY: help install install-gemini test samples scans eval ingest serve demo clean

help:               ## list these targets
	@awk -F':.*## ' '/^[a-z-]+:.*## / {printf "  make %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:            ## create .venv and install the project with test tools
	$(PYTHON) -m venv .venv
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -e ".[dev]"

install-gemini:     ## add the Gemini SDK
	$(BIN)/pip install --quiet -e ".[gemini]"

test:               ## run the test suite (no network, no API key)
	$(BIN)/python -m pytest -q

samples:            ## regenerate the synthetic invoices and ground truth
	$(BIN)/python -m doc2data samples --out samples

scans:              ## rebuild the image-only scans in samples/scans (macOS only)
	$(BIN)/python tools/make_scans.py

eval:               ## score an extractor: make eval EXTRACTOR=gemini
	$(BIN)/python -m doc2data eval --extractor $(EXTRACTOR) --out eval/$(EXTRACTOR).md

ingest:             ## read the sample invoices into the database
	$(BIN)/python -m doc2data ingest samples --extractor $(EXTRACTOR)

serve:              ## run the dashboard on http://127.0.0.1:8000
	DOC2DATA_EXTRACTOR=$(EXTRACTOR) $(BIN)/python -m doc2data serve

demo: ingest serve  ## read the samples, then serve the dashboard

clean:              ## delete the local database and any exported CSV
	rm -f doc2data.db invoices.csv
