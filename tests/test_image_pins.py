"""Every outside image is pinned by digest (`name:tag@sha256:...`), not only by tag: a tag can be pushed again with
other contents (pgvector's `0.8.7-pg18` was, on 2026-10-01), so only the digest makes the test copy and live run
exactly the same bytes. The monthly update routine (SETUP section 12) moves tag and digest together."""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy"))
from images import PINNED  # noqa: E402


def compose_images(name):
    services = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["services"]
    return {f"{name} {svc}": spec["image"] for svc, spec in services.items() if "image" in spec}


def dockerfile_images(name):
    """FROM lines and COPY --from=<image> lines; a FROM naming an earlier stage (`AS build`) is not an image."""
    text = (ROOT / name).read_text(encoding="utf-8")
    stages = set(re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.MULTILINE | re.IGNORECASE))
    found = re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE) + re.findall(r"COPY\s+--from=(\S+)", text)
    return {f"{name} {image}": image for image in found if image not in stages}


def unpinned(images):
    return {where for where, image in images.items() if not PINNED.match(image)}


def test_a_tag_without_a_digest_is_caught():
    assert unpinned({"x": "n8nio/n8n:2.43.5"}) == {"x"}
    assert unpinned({"x": "n8nio/n8n:2.43.5@sha256:" + "a" * 64}) == set()


def test_every_image_is_pinned_by_digest():
    images = {**compose_images("compose.dev.yaml"), **compose_images("deploy/compose.yaml"),
              **dockerfile_images("Dockerfile"), **dockerfile_images("Dockerfile.backup")}
    assert len(images) >= 8  # 3 + 3 compose images, 2 python bases + uv, 1 backup base: the scan found them all
    assert unpinned(images) == set()
