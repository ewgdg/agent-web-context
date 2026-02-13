# Repository Guidelines

## Project Structure & Module Organization
- Source: `src/agent_web_context/` (FastAPI app + MCP server).
- Key modules: `main.py` (entry), `routers/` (API routes), `agents/` (agent logic), `scraper.py` (Patchright browser), `search.py` (Google CSE), `cache.py` (async cache), `config.py` (model/provider settings).
- Tests: `tests/` (unit/integration) with `test_*.py` files.
- Logs/Screenshots: `logs/` for runtime traces and capture artifacts.

## Build, Test, and Development Commands
- Install deps: `uv sync`
- Run API: `uv run -- uvicorn 'src.agent_web_context.main:app' --host=0.0.0.0 --port=8000` (docs at `http://localhost:8000/docs`).
- Run tests: `uv run pytest -q`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Docker: `docker compose up --build` (API at `http://localhost:8000`; KasmVNC at `https://localhost:6901`).

## Coding Style & Naming Conventions
- Python 3.13, 4-space indentation, comprehensive type hints.
- Naming: `snake_case` for modules/functions, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- Keep functions small with explicit returns; prefer dataclasses/Pydantic models for DTOs.
- Follow Ruff defaults; fix all warnings prior to PR.

## Testing Guidelines
- Framework: `pytest` + `pytest-asyncio` for async endpoints/agents.
- Location/pattern: tests in `tests/` named `test_*.py`.
- Coverage focus: routers (status codes/payloads), agents (happy/edge paths), scraper/search with mocks.
- Run: `uv run pytest -q`; add fixtures to isolate network/browser.

## Commit & Pull Request Guidelines
- Commits: imperative mood, concise scope, reference issues (e.g., `fix: handle 429 in scraper (#123)`).
- PRs: include summary, rationale, test coverage notes, and relevant outputs/screenshots (e.g., API responses).
- Checks: ensure tests pass and run `ruff check` and `ruff format`; build Docker locally when touching runtime or packaging.

## Security & Configuration Tips
- Never commit secrets. Use `.env` or container env vars.
- Required: `GOOGLE_API_KEY`, `GOOGLE_CX_KEY`; optional: `DATABASE_URL`.
- Model/provider settings live in `config.yaml` (ordered primary → fallback). Validate credentials before enabling agents.
- Rate limits and caching are built-in—avoid duplicate fetches; inspect `logs/` for failures/screenshots.
