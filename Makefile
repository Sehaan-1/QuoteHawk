SHELL := /usr/bin/env bash

UV ?= uv
NPM ?= npm

.PHONY: ci ci-agents ci-python ci-web ci-mobile format-check lint test typecheck spellcheck lock-check \
	package-check web-check mobile-check

## Run every deterministic quality gate used by GitHub Actions.
ci: ci-agents ci-python ci-web ci-mobile

## Validate the committed agents-cli scaffold and its built-in Python quality lifecycle.
ci-agents:
	AGENTS_CLI_TELEMETRY=0 $(UV) tool run --from google-agents-cli==1.14.0 agents-cli info
	AGENTS_CLI_TELEMETRY=0 $(UV) tool run --from google-agents-cli==1.14.0 agents-cli lint
	AGENTS_CLI_TELEMETRY=0 $(UV) tool run --from google-agents-cli==1.14.0 agents-cli build

## Install locked Python dependencies and enforce policy, test, type, spelling, lock, and package checks.
ci-python:
	$(UV) sync --all-groups --all-extras --locked
	$(MAKE) format-check lint test typecheck spellcheck lock-check package-check

format-check:
	$(UV) run ruff format --check .

lint:
	$(UV) run ruff check .

test:
	$(UV) run pytest -q

typecheck:
	$(UV) run ty check .

spellcheck:
	$(UV) run codespell .

lock-check:
	$(UV) lock --check

package-check:
	rm -rf /tmp/quotehawk-ci-dist
	$(UV) build --out-dir /tmp/quotehawk-ci-dist
	unzip -t /tmp/quotehawk-ci-dist/*.whl >/dev/null

## Reproducibly build and audit the Next.js buyer workspace.
ci-web: web-check

web-check:
	cd web && $(NPM) ci
	cd web && $(NPM) run typecheck
	cd web && $(NPM) run build
	cd web && rm -rf .next out && $(NPM) run build:static
	test -f web/out/index.html
	cd web && $(NPM) audit --omit=dev --audit-level=low

## Reproducibly export and audit the Expo approval companion without Expo network calls.
ci-mobile: mobile-check

mobile-check:
	cd mobile && $(NPM) ci
	cd mobile && $(NPM) run typecheck
	cd mobile && $(NPM) run export:web
	test -f mobile/dist/index.html
	rm -rf mobile/dist
	cd mobile && $(NPM) run export:android
	test -f mobile/dist/metadata.json
	rm -rf mobile/dist
	cd mobile && $(NPM) audit --omit=dev --audit-level=low
