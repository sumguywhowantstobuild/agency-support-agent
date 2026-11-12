"""The outside software versions a compose file names, for deploy/apply.sh's upgrade path (SETUP section 12).

`deploy/apply.sh copy images` records them when the copy runs well on them, and `live images` refuses any others.
Each must be pinned by digest (`name:tag@sha256:...`): a tag can be pushed again with other contents, so only the
digest says exactly what passed. The agent is left out (its own path: `copy agent`, `live agent`), and so is the
backup service (rebuilt by `live images` itself; the copy never runs it).
"""

import re
import sys
from pathlib import Path

import yaml

SERVICES = ("postgres", "n8n", "tunnel")
PINNED = re.compile(r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")


def versions(compose: dict) -> str:
    """`postgres=<image> n8n=<image> tunnel=<image>`, always in that order, so equal versions give equal text."""
    services = compose.get("services") or {}
    found = []
    for name in SERVICES:
        image = (services.get(name) or {}).get("image")
        if not image:
            raise ValueError(f"the compose file has no image for {name}")
        if not PINNED.match(image):
            raise ValueError(f"{name}'s image {image!r} is not pinned by digest (name:tag@sha256:...)")
        found.append(f"{name}={image}")
    return " ".join(found)


if __name__ == "__main__":  # deploy/images.py <compose file>
    try:
        print(versions(yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))))
    except ValueError as exc:
        sys.exit(f"{sys.argv[1]}: {exc}")
