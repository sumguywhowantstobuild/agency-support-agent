"""deploy/apply.sh `ai_print`: the fingerprint of the code that decides what the AI writes (agent/, knowledge/, ghl/ in
an image). A change to it needs the accuracy test; the one-time key move tool (ghl/move_tokens.py) is the only file
left out, because nothing in drafting imports it. The python code is taken out of the script and run here against a
stand-in package, so Docker isn't needed."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = ("agent", "knowledge", "ghl")
EXCLUDED = "ghl/move_tokens.py"


def fingerprint_code():
    script = (ROOT / "deploy" / "apply.sh").read_text(encoding="utf-8")
    found = re.search(r"^ai_print\(\) \{\n.*? -c '(?P<code>[^']*)'\n\}$", script, re.S | re.M)
    assert found, "ai_print in deploy/apply.sh no longer has the shape this test reads"
    return found["code"]


def make_package(root):
    package = root / "support_agent"
    for folder in FOLDERS:
        (package / folder).mkdir(parents=True)
        (package / folder / "__init__.py").write_text("", encoding="utf-8")
        (package / folder / "one.py").write_text(f"# {folder} one\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "ghl" / "move_tokens.py").write_text("# one-time move\n", encoding="utf-8")
    return package


def fingerprint(root):
    env = {**os.environ, "PYTHONPATH": str(root)}
    result = subprocess.run([sys.executable, "-c", fingerprint_code()], cwd=root, env=env, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"[0-9a-f]{16}\n", result.stdout), result.stdout
    return result.stdout.strip()


def test_the_ai_fingerprint_ignores_only_the_one_time_move_tool(tmp_path):
    package = make_package(tmp_path)
    before = fingerprint(tmp_path)

    (package / EXCLUDED).write_text("# one-time move, changed\n", encoding="utf-8")
    assert fingerprint(tmp_path) == before  # the one-time tool is left out

    for folder in FOLDERS:  # any other file moves it
        other = package / folder / "one.py"
        other.write_text(f"# {folder} one, changed\n", encoding="utf-8")
        changed = fingerprint(tmp_path)
        assert changed != before, folder
        other.write_text(f"# {folder} one\n", encoding="utf-8")
        assert fingerprint(tmp_path) == before, folder  # put back: the same again

    (package / "ghl" / "two.py").write_text("# a new file\n", encoding="utf-8")  # a new file counts too
    assert fingerprint(tmp_path) != before

    (package / "ghl" / "two.py").unlink()
    (package / "agent" / "move_tokens.py").write_text("# same name, other folder\n", encoding="utf-8")
    assert fingerprint(tmp_path) != before  # only that one path is left out, not the name
