"""Identifier and uptime helpers."""
import time
import uuid

APP_START = time.time()


def new_uid() -> str:
    return str(uuid.uuid4())


def uptime_str() -> str:
    seconds = int(time.time() - APP_START)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
