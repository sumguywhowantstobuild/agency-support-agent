"""deploy/images.py: the software versions a compose file names, as deploy/apply.sh records and compares them."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from images import versions  # noqa: E402

D = "@sha256:" + "a" * 64


def compose(**images):
    return {"services": {name: {"image": image} for name, image in images.items()} | {"agent": {"build": "."}}}


def test_the_versions_of_the_database_n8n_and_tunnel_in_a_fixed_order():
    found = versions(compose(tunnel=f"cf:1{D}", n8n=f"n8n:2{D}", postgres=f"pg:3{D}"))
    assert found == f"postgres=pg:3{D} n8n=n8n:2{D} tunnel=cf:1{D}"


def test_a_version_without_a_digest_is_refused():
    with pytest.raises(ValueError, match="n8n"):
        versions(compose(postgres=f"pg:3{D}", n8n="n8n:2", tunnel=f"cf:1{D}"))


def test_a_missing_service_is_refused():
    with pytest.raises(ValueError, match="tunnel"):
        versions(compose(postgres=f"pg:3{D}", n8n=f"n8n:2{D}"))
