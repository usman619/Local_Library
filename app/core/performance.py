import os
import time
from contextlib import contextmanager


PROFILE_ENABLED = os.environ.get("LOCAL_LIBRARY_PROFILE", "").lower() in {
    "1", "true", "yes", "on"
}


@contextmanager
def measure(label: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        if PROFILE_ENABLED:
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"[performance] {label}: {elapsed_ms:.1f} ms", flush=True)
