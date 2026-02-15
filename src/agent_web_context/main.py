from contextlib import asynccontextmanager, AsyncExitStack
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import logging.config
from logging.handlers import RotatingFileHandler
import os
import yaml

from .cache import initialize_cache, shutdown_cache
from .routers import scraping, search, logs, agent
from .scraper import scraper_context_manager, Scraper
from .services import service_locator
from .mcp_server import create_mcp, create_mcp_asgi_app


def setup_logging(config_path: str = "logging.yaml") -> None:
    os.makedirs("./logs", exist_ok=True)
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        if isinstance(config, dict) and config.get("version"):
            logging.config.dictConfig(config)
            return
    except Exception:
        # If loading config fails, fall back to minimal in-code config
        pass

    # Fallback minimal setup (errors to file + console info)
    error_handler = RotatingFileHandler(
        "./logs/errors.log",
        maxBytes=1 * 1024 * 1024,
        backupCount=3,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    logging.basicConfig(
        level=logging.INFO, handlers=[console_handler, error_handler], force=True
    )


# Initialize logging on import
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = logging.getLogger(__name__)
    logger.info("🚀 Starting lifespan initialization...")

    async with AsyncExitStack() as stack:
        # Initialize cache
        logger.info("Initializing cache...")
        await initialize_cache()
        stack.push_async_callback(shutdown_cache)

        # Initialize scraper
        logger.info("Initializing scraper...")
        scraper = await stack.enter_async_context(scraper_context_manager())
        service_locator.container.register_singleton(Scraper, scraper)
        logger.info("Services registered successfully")

        # Initialize MCP server lifespans
        logger.info("Initializing MCP server...")
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
        logger.info("✅ Lifespan initialization complete")
        yield
        logger.info("🔄 Lifespan shutdown starting...")


instructions = (
    "Use this server for researching and analyzing web content. "
    "Prefer high-level agentic tools (agent_*) for comprehensive tasks over low-level tools for fine-grain control."
)

app = FastAPI(
    title="Web Browsing API",
    version="0.1.0",
    description=instructions,
    lifespan=lifespan,
    disable_existing_loggers=False,
)

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger(__name__)

# Create MCP server instance (shared builder for all transports)
mcp = create_mcp(instructions)

mcp_app = create_mcp_asgi_app(mcp)


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Include routers
app.include_router(scraping.router)
app.include_router(search.router)
app.include_router(logs.router)
app.include_router(agent.router)

logger.info("📋 MCP server initialized")


# Mount both transports for compatibility:
# - SSE: `/mcp/sse` and `/mcp/messages`
# - Streamable HTTP: `/mcp`
app.mount("/", mcp_app)
