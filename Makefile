.PHONY: up down test lint fmt

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
