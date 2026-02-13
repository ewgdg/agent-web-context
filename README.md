# Agent Web Context

A web context service for agents: supports MCP plus an HTTP/OpenAPI API for web browsing, content extraction, and search (FastAPI).

## Features

- **Web Content Extraction**: Headless browser automation using Patchright
- **Brave Search**: Web search via Brave Search API
- **AI-Powered Analysis**: Multi-provider AI content analysis (OpenAI, OpenAI-compatible, Anthropic, etc.)
- **Intelligent Caching**: SQLite-based caching with automatic cleanup
- **Docker Ready**: Full containerized setup with VNC debugging

## Quick Start

### Using Docker (Recommended)

```bash
git clone <repository-url>
cd agent-web-context
cp .env.example .env  # Edit with your API keys
docker compose up --build
```


- API: <http://localhost:8000>
- VNC Debug: <https://localhost:6901> (kasm_user/kasm_user)

### Local Development

```bash
uv sync
cp .env.example .env  # Edit with your API keys
uv run -- uvicorn 'src.agent_web_context.main:app' --host=0.0.0.0 --port=8000
```

```bash
uv run pre-commit install  # install pre-commit hook
```

## Using as an MCP server

Start the service (Docker or local) and connect your MCP-capable client to the MCP endpoint:

- Streamable HTTP transport: `/mcp` (recommended)
- Legacy SSE transport: `/mcp/sse` + `/mcp/messages` (compat)

## Using from agent skills

This repo ships a general-purpose agent skill at `skills/agent-web-context/` that calls this service over HTTP and discovers operations from the service OpenAPI schema.

For Codex specifically, we recommend the skill-based integration due to a known Codex [limitation](https://github.com/openai/codex/issues/3152).

### Install the skill (for Codex)

Global install:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/agent-web-context "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Project-local install:

```bash
mkdir -p /path/to/your/project/.codex/skills
cp -R skills/agent-web-context /path/to/your/project/.codex/skills/
```

The skill persists the service `base_url` in `<install_dir>/agent-web-context/references/service.json` (created on first use). If `base_url` is missing/empty, the agent should ask you and then save it for next time.

## Configuration

Copy `.env.example` to `.env` and configure your API keys. See `config.yaml` for AI provider configuration (supports OpenAI, OpenAI-compatible, Anthropic, Ollama, etc.).

## API Endpoints

- `GET /health`: Health check
- `GET /docs`: OpenAPI documentation
- `GET /logs`: Web-based log file browser with delete functionality
- `/mcp`: MCP endpoint (Streamable HTTP transport)
- `/mcp/sse` and `/mcp/messages`: Legacy SSE transport (compatibility)

## MCP Tools

- `fetch_web_content`: Extract content from web pages
- `search_web_pages`: Search using Brave Search API
- `agent_websearch`: Intelligent iterative search with multi-step reasoning
- `agent_extract_content`: AI-powered content extraction and analysis

## Requirements

- Python 3.13+
- Docker (recommended)
- Brave Search API key
- AI provider API key (OpenAI, Anthropic, etc.)
- Firefox ESR (handled automatically in Docker)

## MCP Transports

- Streamable HTTP: available at `/mcp` (recommended)
- SSE: available at `/mcp/sse` and `/mcp/messages` (legacy/compat)
