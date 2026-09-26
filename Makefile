.PHONY: up down test lint fmt eval eval-retrieval load load-smoke

up:
	docker compose up -d --wait

down:
	docker compose down

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .

eval:
	uv run python -m whatfrom.cli eval

eval-retrieval:
	uv run python -m whatfrom.cli eval --retrieval-only

# 부하 기준선(F10). 앱(:8000)과 모의 LLM 서버(:8081)를 먼저 띄운다. README "부하 측정" 참고.
LOAD_LABEL ?= run
K6 = docker run --rm -u "$$(id -u):$$(id -g)" --add-host=host.docker.internal:host-gateway \
	-v "$(CURDIR)/load:/load" grafana/k6:2.3.0

load:
	mkdir -p load/results
	$(K6) run -e LABEL=$(LOAD_LABEL) /load/spike.js

load-smoke:
	mkdir -p load/results
	$(K6) run -e SMOKE=1 /load/spike.js
