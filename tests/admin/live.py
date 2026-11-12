"""A real server for the browser tests: the admin app on a free port of 127.0.0.1, with the test's fakes in place."""

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI


def free_url() -> str:
    with socket.socket() as probe:  # a free port for this test
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{probe.getsockname()[1]}"


@contextmanager
def serving(make_app: Callable[[str], FastAPI]) -> Iterator[str]:
    """Runs make_app(url) until the block ends; yields the url. make_app gets the url so ADMIN_ORIGIN can match it."""
    url = free_url()
    port = int(url.rsplit(":", 1)[1])
    # lifespan off: the start-up check reads the real settings, which the tests keep away (test_settings covers it)
    server = uvicorn.Server(uvicorn.Config(make_app(url), host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "the test server didn't start"
        time.sleep(0.02)
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)
