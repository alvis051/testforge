.PHONY: install dev test lint fmt migrate seed demo types ui build-ui e2e

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

types:
	uv run tf openapi > openapi.json
	cd frontend && npm run gen:types

ui:
	cd frontend && npm run dev

build-ui:
	cd frontend && npm ci && npm run build

e2e:
	cd frontend && npm run test:e2e
