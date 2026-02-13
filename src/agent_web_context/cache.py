from collections import OrderedDict
import functools
import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Type, TypeVar

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import BaseModel
from sqlalchemy import DateTime, select, delete
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import os

DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
EXPIRY_MINUTES = 30

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


# SQLAlchemy base and engine setup
class Base(AsyncAttrs, DeclarativeBase):
    pass


# Cache model using SQLAlchemy ORM
class Cache(Base):
    __tablename__ = "cache"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


engine = create_async_engine(DB_URL, echo=False)
make_async_session = async_sessionmaker(bind=engine, expire_on_commit=False)


# Initialize DB and create tables
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


T = TypeVar("T")


# Decorator factory
def async_cache_result(
    argument_serializers: dict[Type[T], Callable[[T], str]],
    result_serializer: Callable[[Any], str],
    result_deserializer: Callable[[str], Any],
    predicate: Callable[[Any], bool] = lambda x: True,
):
    def decorator(func: Callable):
        def get_key(
            func: Callable,
            arg_serializers: dict[Type[T], Callable[[T], str]],
            *args,
            **kwargs,
        ) -> str:
            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            return str(
                (
                    func.__name__,
                    {
                        arg_name: arg_serializers[type(arg_val)](arg_val)
                        for arg_name, arg_val in bound.arguments.items()
                        if type(arg_val) in arg_serializers
                    },
                )
            )

        @functools.wraps(func)
        async def wrapper(*args, allow_cache=True, **kwargs):
            key = get_key(func, argument_serializers, *args, **kwargs)

            async with make_async_session() as session:
                if allow_cache:
                    # Try to load from cache
                    result = await session.scalar(select(Cache).where(Cache.key == key))
                    if result:
                        timestamp = result.timestamp
                        # SQLite does not support timezone
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                        if datetime.now(timezone.utc) - timestamp <= timedelta(
                            minutes=EXPIRY_MINUTES
                        ):
                            return result_deserializer(result.value)
                        else:
                            # Expired cache
                            await session.delete(result)
                            await session.commit()

                # Miss or expired, compute and cache it
                result = func(*args, **kwargs)
                # Check if the result is an instance of Awaitable
                if inspect.isawaitable(result):
                    data = await result
                else:
                    # If not awaitable, just return the result
                    data = result
                if predicate(data):
                    cache_entry = Cache(
                        key=key,
                        value=result_serializer(data),
                        timestamp=datetime.now(timezone.utc),
                    )
                    await session.merge(cache_entry)
                    await session.commit()
                return data

        return wrapper

    return decorator


# Cleanup task to remove expired entries
async def cleanup_cache():
    expiry_cutoff = datetime.now(timezone.utc) - timedelta(minutes=EXPIRY_MINUTES)

    async with make_async_session() as session:
        result = await session.execute(
            delete(Cache).where(Cache.timestamp < expiry_cutoff)
        )
        await session.commit()
        count = result.rowcount
        logger.info(f"Cache cleanup complete. Removed {count} entries.")


# Schedule cleanup job
def schedule_cache_cleanup():
    scheduler.add_job(cleanup_cache, "interval", days=1)
    scheduler.start()


async def initialize_cache():
    await init_db()
    await cleanup_cache()
    schedule_cache_cleanup()


async def shutdown_cache():
    scheduler.shutdown(wait=False)  # Gracefully shut down scheduler
    await engine.dispose()
