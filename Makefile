.PHONY: install dev test lint fmt migrate seed demo

install:
	uv sync

dev:
	uv run uvicorn testforge.main:create_app --factory --reload

test:
	uv run pytest -v

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

migrate:
	uv run alembic -c server/alembic.ini upgrade head

seed: migrate
	uv run tf seed

demo: seed
	@echo "Start the API with 'make dev', then: uv run tf case list CHK"
