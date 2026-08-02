.PHONY: install dev test lint fmt

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
