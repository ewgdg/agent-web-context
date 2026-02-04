# MCP Web Context

A Model Context Protocol (MCP) server for web browsing, content extraction, and search built with FastAPI.

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
cd mcp-web-context
cp .env.example .env  # Edit with your API keys
docker compose up --build
```


- API: <http://localhost:8000>
- VNC Debug: <https://localhost:6901> (kasm_user/kasm_user)

### Local Development

```bash
uv sync
cp .env.example .env  # Edit with your API keys
uv run -- uvicorn 'src.mcp_web_context.main:app' --host=0.0.0.0 --port=8000
```

```bash
uv run pre-commit install  # install pre-commit hook
```

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
