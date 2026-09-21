.PHONY: install uninstall deps test lint typecheck migrate up verify

install:
	bash scripts/install.sh

uninstall:
	bash scripts/uninstall.sh

deps:
	uv sync --all-extras

test:
	BOT_TOKEN=test-token ADMIN_TELEGRAM_ID=1 uv run pytest tests/ -q

lint:
	uv run ruff check . --fix
	uv run ruff check .

typecheck:
	uv run mypy src

migrate:
	uv run alembic upgrade head

up:
	docker compose up --build

verify: test lint typecheck
	docker compose config -q
