"""Tests for `app/logging_config.py` and `app/db_logging.py`."""

from __future__ import annotations

import logging
import sys

import pytest
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.logging_config import InterceptHandler


@pytest.fixture
def captured_records():
    """Collect loguru records emitted during the test."""
    records: list[dict] = []

    sink_id = logger.add(
        lambda message: records.append(message.record),
        level="DEBUG",
    )

    yield records

    logger.remove(sink_id)


# ======================================================
# STDLIB -> LOGURU INTERCEPTION
# ======================================================


def _log_record(level: int, msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.error",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_intercept_handler_forwards_stdlib_records(captured_records) -> None:
    InterceptHandler().emit(_log_record(logging.WARNING, "hello %s", "world"))

    messages = [record["message"] for record in captured_records]

    assert "hello world" in messages


def test_intercept_handler_preserves_the_level(captured_records) -> None:
    InterceptHandler().emit(_log_record(logging.ERROR, "kaboom"))

    levels = [record["level"].name for record in captured_records]

    assert "ERROR" in levels


def test_intercept_handler_carries_exception_info(captured_records) -> None:
    try:
        raise ValueError("original failure")
    except ValueError:
        record = _log_record(logging.ERROR, "failed")
        record.exc_info = sys.exc_info()

        InterceptHandler().emit(record)

    assert any(entry["exception"] is not None for entry in captured_records)


@pytest.mark.xfail(
    raises=ValueError,
    strict=True,
    reason=(
        "Known bug: the ValueError fallback in InterceptHandler.emit sets "
        "level = str(record.levelno) and passes it to logger.log(), but loguru "
        "only accepts registered level *names*, so it raises again. Delete "
        "this xfail once the fallback uses the numeric level."
    ),
)
def test_intercept_handler_handles_a_custom_stdlib_level(captured_records) -> None:
    logging.addLevelName(25, "NOTICE")

    InterceptHandler().emit(_log_record(25, "custom level"))

    assert "custom level" in [record["message"] for record in captured_records]


def test_request_id_extra_has_a_default(captured_records) -> None:
    """Both log formats interpolate `extra[request_id]`, so it must always
    exist — otherwise every log line outside a request would raise."""
    logger.info("no request context here")

    assert captured_records[-1]["extra"]["request_id"] == "-"


def test_request_id_extra_is_bound_inside_a_request_scope(
    captured_records,
) -> None:
    with logger.contextualize(request_id="abc-123"):
        logger.info("inside")

    assert captured_records[-1]["extra"]["request_id"] == "abc-123"


# ======================================================
# SQLALCHEMY QUERY TIMING
# ======================================================


async def test_db_queries_are_logged_with_a_duration(
    db_engine: AsyncEngine,
    captured_records,
) -> None:
    async with db_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

    db_messages = [
        record["message"]
        for record in captured_records
        if record["message"].startswith("DB query")
    ]

    assert db_messages, "expected the after_cursor_execute listener to log"
    assert "SELECT 1" in db_messages[-1]
    assert "ms |" in db_messages[-1]


async def test_db_query_logging_pops_its_timing_stack(
    db_engine: AsyncEngine,
) -> None:
    """`query_start_time` is a list used as a stack; it must not grow."""
    async with db_engine.connect() as connection:
        for _ in range(3):
            await connection.execute(text("SELECT 1"))

        raw_connection = await connection.get_raw_connection()

        assert raw_connection.info["query_start_time"] == []
