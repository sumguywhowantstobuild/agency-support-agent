"""Reads the notebook deploy/.tested for the deploy/apply.sh menu.

Each line: time, kind, key, fingerprint, note (tab-separated). deploy/apply.sh writes a line when a change passes on
the test copy (kind agent, evals, migration, workflow, images), when it goes live (live-agent, live-migration,
live-workflow, live-images)
and when the copy is refreshed (refresh, then a baseline line per workflow with its version).
"""

import argparse
import sys
from pathlib import Path
from typing import NamedTuple


class Entry(NamedTuple):
    time: str
    kind: str
    key: str
    fingerprint: str
    note: str


def parse(text: str) -> list[Entry]:
    """A damaged line stops everything: skipping it could hide a pass or a go-live."""
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        fields = line.split("\t")
        if len(fields) != len(Entry._fields) or not all(fields[:4]):
            raise ValueError(f"deploy/.tested line {number} is damaged (expected time, kind, key, fingerprint, note "
                             f"separated by tabs): {line!r}")
        entries.append(Entry(*fields))
    return entries


PASSED = ("agent", "migration", "workflow", "images")  # each has a "live-" line when it goes live


def waiting(entries: list[Entry], files: dict[str, str] | None = None,
            copy_workflows: dict[str, str] | None = None, images: str | None = None) -> list[Entry]:
    """What passed on the copy and isn't live yet: the newest pass of each change, as `live ...` would take it.

    files: each db/migrations file's fingerprint now. images: the software versions the compose files name now
    (`live images` refuses others). copy_workflows: each workflow's version in the copy now, or None
    while the copy is stopped (unknown: a passed workflow is still listed, and `live workflow` checks it on the way).
    """
    # Later lines replace earlier ones: the newest pass of each change, and what live runs now.
    newest = {(e.kind, e.key): e for e in entries if e.kind in PASSED}
    live_now = {(e.kind[5:], e.key): e.fingerprint for e in entries if e.kind.startswith("live-")}
    # `live migration` and `live workflow` refuse a file or workflow that changed after it passed, so the menu
    # doesn't offer it either.
    still_as_passed = {"migration": files or {}, "images": {"images": images}}
    if copy_workflows is not None:
        still_as_passed["workflow"] = copy_workflows
    return [e for e in newest.values() if live_now.get((e.kind, e.key)) != e.fingerprint
            and (e.kind not in still_as_passed or still_as_passed[e.kind].get(e.key) == e.fingerprint)]


def edited_in_copy(copy_workflows: dict[str, str], entries: list[Entry]) -> set[str]:
    """Workflows changed in the copy since its newest refresh (or new there) and not yet passed in that version.

    A refresh takes a fresh backup of live first and writes down each workflow's version ("baseline" lines), so those
    are live's versions at that moment. Versions, not times: the PC's and Docker's clocks can drift apart.
    """
    refreshes = [i for i, e in enumerate(entries) if e.kind == "refresh"]
    if not refreshes:
        return set()
    baseline = {e.key: e.fingerprint for e in entries[refreshes[-1]:] if e.kind == "baseline"}
    passed = {(e.key, e.fingerprint) for e in entries if e.kind == "workflow"}
    return {wf for wf, version in copy_workflows.items()
            if baseline.get(wf) != version and (wf, version) not in passed}


def copy_data_from(entries: list[Entry]) -> str | None:
    """When the backup behind the copy's data was taken (`copy refresh` records it), or None if never recorded."""
    refreshes = [e.fingerprint for e in entries if e.kind == "refresh"]
    return refreshes[-1] if refreshes else None


def docker_changed(entries: list[Entry], now: str) -> str | None:
    """The Docker version and image store at the newest pass on the copy, if they differ from now; else None.

    Each pass records them ("platform" lines): a Docker update or a default it flips by itself can change how images
    and containers behave, so the menu warns until a change has been through the copy on the new one.
    """
    recorded = [e.fingerprint for e in entries if e.kind == "platform"]
    return recorded[-1] if recorded and recorded[-1] != now else None


def main(argv: list[str], text: str) -> str:
    """For deploy/menu.sh. waiting [--file NAME=FINGERPRINT ...] [--workflow ID=VERSION ... | --copy-stopped]
    [--images VERSIONS] -> kind, key, fingerprint, time (tab-separated, one change a line); edited --copy ID=VERSION ... -> one id a line;
    data-from -> the backup time behind the copy's data, or nothing; docker-changed --now DOCKER -> the Docker recorded
    at the newest pass if it differs, or nothing."""
    parser = argparse.ArgumentParser(prog="deploy/notebook.py")
    parser.add_argument("question", choices=("waiting", "edited", "data-from", "docker-changed"))
    for flag in ("--file", "--workflow", "--copy"):
        parser.add_argument(flag, action="append", default=[])
    parser.add_argument("--copy-stopped", action="store_true")  # the copy's workflow versions are unknown
    parser.add_argument("--now", default="")  # docker-changed: this machine's Docker now
    parser.add_argument("--images", default="")  # waiting: the software versions the compose files name now
    args = parser.parse_args(argv)

    def pairs(values: list[str]) -> dict[str, str]:
        return dict(v.split("=", 1) for v in values)

    entries = parse(text)
    if args.question == "waiting":
        return "\n".join("\t".join((e.kind, e.key, e.fingerprint, e.time))
                         for e in waiting(entries, pairs(args.file),
                                          None if args.copy_stopped else pairs(args.workflow), args.images))
    if args.question == "edited":
        return "\n".join(sorted(edited_in_copy(pairs(args.copy), entries)))
    if args.question == "docker-changed":
        return docker_changed(entries, args.now) or ""
    return copy_data_from(entries) or ""


if __name__ == "__main__":
    notebook = Path(__file__).resolve().parent / ".tested"
    answer = main(sys.argv[1:], notebook.read_text(encoding="utf-8") if notebook.exists() else "")
    if answer:  # nothing at all for "none", so the menu doesn't read an empty line as an item
        print(answer)
