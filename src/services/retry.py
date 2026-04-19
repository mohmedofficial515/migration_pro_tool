"""
Retry Utility
=============
Provides a decorator and a context function for retrying database operations
that fail due to transient network/connection errors.

Strategy: Exponential backoff with jitter.
  Attempt 1: immediate
  Attempt 2: wait ~2s
  Attempt 3: wait ~4s
  After 3 failures: raise the last exception
"""

import logging
import random
import time
from functools import wraps
from typing import Callable, Type

import psycopg2

logger = logging.getLogger(__name__)

# Errors that are safe to retry (network blips, timeouts, dropped connections)
RETRYABLE_ERRORS: tuple[Type[Exception], ...] = (
    psycopg2.OperationalError,       # Connection lost, timeout
    psycopg2.InterfaceError,         # Connection already closed
    ConnectionResetError,
    TimeoutError,
)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 2.0   # seconds
DEFAULT_MAX_DELAY = 30.0   # seconds cap


def with_retry(
    func: Callable = None,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    on_retry: Callable[[int, Exception], None] | None = None,
):
    """
    Decorator that retries a function on transient DB errors.

    Usage:
        @with_retry
        def my_db_op():
            ...

        @with_retry(max_attempts=5, base_delay=1.0)
        def my_other_op():
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return _run_with_retry(
                fn, args, kwargs,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                on_retry=on_retry,
            )
        return wrapper

    # Support both @with_retry and @with_retry(...)
    if func is not None:
        return decorator(func)
    return decorator


def retry_call(
    func: Callable,
    *args,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs,
):
    """
    Functional form — call retry_call(fn, arg1, kwarg=val).
    Useful when you can't decorate the function directly.
    """
    return _run_with_retry(
        func, args, kwargs,
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=DEFAULT_MAX_DELAY,
        on_retry=on_retry,
    )


def _run_with_retry(
    func: Callable,
    args: tuple,
    kwargs: dict,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    on_retry: Callable | None,
) -> object:
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)

        except RETRYABLE_ERRORS as e:
            last_error = e
            if attempt == max_attempts:
                logger.error(
                    "[Retry] '%s' failed after %d attempts. Last error: %s",
                    func.__name__, max_attempts, e,
                )
                raise

            # Exponential backoff with ±20% jitter to avoid thundering herd
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            jitter = delay * random.uniform(-0.2, 0.2)
            wait = max(0.5, delay + jitter)

            logger.warning(
                "[Retry] '%s' attempt %d/%d failed (%s). Retrying in %.1fs...",
                func.__name__, attempt, max_attempts, type(e).__name__, wait,
            )

            if on_retry:
                on_retry(attempt, e)

            time.sleep(wait)

        except Exception as e:
            # Non-retryable error — re-raise immediately
            logger.error("[Retry] '%s' non-retryable error: %s", func.__name__, e)
            raise

    raise last_error  # Should never reach here, but satisfies type checker
