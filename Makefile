.PHONY: install dev test lint fmt migrate seed demo types ui build-ui e2e serve

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

demo: seed build-ui
	@echo "Now run 'make serve' and open http://localhost:8000"

types:
	uv run tf openapi > openapi.json
	cd frontend && npm run gen:types

ui:
	cd frontend && npm run dev

build-ui:
	cd frontend && npm ci && npm run build

e2e:
	cd frontend && npm run test:e2e

serve: build-ui migrate
	TESTFORGE_FRONTEND_DIST=frontend/dist uv run uvicorn testforge.main:create_app --factory --port 8000
