# app/db_logging.py

import time

from loguru import logger
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.config import settings


def register_db_logging() -> None:

    @event.listens_for(Engine, "before_cursor_execute")
    def before_cursor_execute(
        conn,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        conn.info.setdefault("query_start_time", []).append(
            time.perf_counter()
        )

    @event.listens_for(Engine, "after_cursor_execute")
    def after_cursor_execute(
        conn,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        start_time = conn.info["query_start_time"].pop()

        duration_ms = (
            time.perf_counter() - start_time
        ) * 1000

        # if duration_ms >= settings.slow_query_threshold_ms:
        #     logger.warning(
        #                 "Slow DB query | {:.2f}ms | {}",
        #                 duration_ms,
        #                 statement,
        #             )
        logger.warning(
            "DB query | {:.2f}ms | {}",
            duration_ms,
            statement,
        )
