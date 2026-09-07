import inspect
import logging
import sys

from loguru import logger

from app.config import settings


class InterceptHandler(logging.Handler):
    """Route stdlib logging (uvicorn, sqlalchemy, httpx) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level:str  = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)

        # Walk out of the logging module so {name}:{line} points at the real caller
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    logger.remove()

    CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "{extra[request_id]} | "
    "<level>{message}</level>"
    )

    FILE_FORMAT = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <8} | "
        "{name}:{function}:{line} | "
        "{extra[request_id]} | "
        "{message}"
    )

    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=CONSOLE_FORMAT,
        # Never leak local variable values (incl. settings.database_url) into prod logs
        diagnose=settings.log_diagnose,
        backtrace=True,
        enqueue=True,  # async-safe; required if you add a file sink
    )

    logger.add(
        "app.log",
        level=settings.log_level,
        format=FILE_FORMAT,
        colorize=False,
        diagnose=settings.log_diagnose,
        backtrace=True,
        enqueue=True,
    )

    logger.configure(extra={"request_id": "-"})

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",   # ← the one that actually emits SQL
        "httpx",
        "httpcore",
    ):
        lg = logging.getLogger(name)
        lg.handlers = [InterceptHandler()]
        lg.propagate = False
    # SQLAlchemy sets its root logger to WARNING on import; opt in explicitly.
    # This replaces echo=True — see note below.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.db_echo else logging.WARNING
    )