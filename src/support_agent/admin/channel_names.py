"""The hourly timer that keeps clients' Slack channel names fresh.

A minute after the admin app starts, then every hour, one daemon thread reads the bot's channels from Slack and notes
their names through the name noter (db.name_noter: a writable connection that offers only note_channel_names). No page
starts a run and none waits on one: pages read the stored name. A run that fails (Slack down, slow or refusing, or the
database) waits the hour like any other, keeps the stored names, and logs Slack's error code or the exception's type
name only. A run that works logs how many names changed, never a name."""

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from support_agent.admin.slack import ChannelLister, SlackUnavailable

logger = logging.getLogger(__name__)

FIRST_RUN_SECONDS = 60
EVERY_SECONDS = 3600
STOP_WAIT_SECONDS = 5  # how long stopping the app waits for a run in progress before leaving the daemon thread behind


class NameNoter(Protocol):
    def note_channel_names(self, names: dict[str, str]) -> int: ...


Noter = Callable[[], AbstractContextManager[NameNoter]]
Wait = Callable[[float], bool]  # sleeps up to this many seconds; True when the timer was told to stop


def refresh_channel_names(list_channels: ChannelLister, noter: Noter, log: logging.Logger) -> None:
    """One run. Never raises: whatever goes wrong is logged by its code or type, and the names stay as they were."""
    try:
        names = {channel.id: channel.name for channel in list_channels()}
        with noter() as writer:
            changed = writer.note_channel_names(names)
        log.info("channel names refreshed: %d changed", changed)
    except SlackUnavailable as exc:  # Slack's error code, or an exception's type name: never the token
        log.warning("channel names not refreshed, Slack: %s", exc)
    except Exception as exc:  # the database or anything else: its text can name the login and address
        log.warning("channel names not refreshed: %s", type(exc).__name__)


def run_timer(list_channels: ChannelLister, noter: Noter, log: logging.Logger, wait: Wait) -> None:
    """A minute, a run, an hour, a run, and so on until `wait` says stop. A run that fails also waits the hour; runs
    follow one another in this one loop, so two never overlap."""
    seconds = FIRST_RUN_SECONDS
    while not wait(seconds):
        refresh_channel_names(list_channels, noter, log)
        seconds = EVERY_SECONDS


_timer: threading.Thread | None = None
_stopping = threading.Event()


def start_timer(list_channels: ChannelLister, noter: Noter, log: logging.Logger = logger, *,
                wait: Wait | None = None) -> None:
    """Start the timer's thread; a second start while one is running does nothing. `wait` is for tests."""
    global _timer, _stopping
    if _timer is not None and _timer.is_alive():
        return
    _stopping = threading.Event()
    _timer = threading.Thread(target=run_timer, args=(list_channels, noter, log, wait or _stopping.wait),
                              name="channel-names", daemon=True)
    _timer.start()


def stop_timer() -> None:
    """Tell the timer to stop and wait briefly for it. A run still talking to Slack finishes in the background (the
    thread is a daemon, so it never keeps the app from exiting)."""
    global _timer
    _stopping.set()
    if _timer is not None:
        _timer.join(STOP_WAIT_SECONDS)
        _timer = None
