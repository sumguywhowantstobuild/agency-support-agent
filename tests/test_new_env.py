"""deploy/new-env.sh, run with typed-in answers: the settings file for a new server (a restore rehearsal,
step 2 of a hand-over). It makes up every internal password, asks each outside key at a hidden prompt, and in staged
mode fills what reaches outside (tunnel, Slack) with placeholders instead of asking."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

# The answers in the order the helper asks for them (staged mode).
# Well-formed fakes in the order the helper asks for them (staged mode).
STAGED_ANSWERS = {
    "OPENAI_API_KEY": "sk-" + "svcacct-" + "F" * 40,
    "OPENAI_MODEL": "gpt-test-mini",
    "JUDGE_MODEL": "gpt-test-judge",
    "R2_ACCESS_KEY_ID": "a" * 32,
    "R2_SECRET_ACCESS_KEY": "b" * 64,
    "R2_ENDPOINT": "https://" + "0123456789abcdef" * 2 + ".r2.cloudflarestorage.com",
    "R2_BUCKET": "test-bucket",
    "BACKUP_AGE_RECIPIENT": "age1" + "qpzry9x8gf2tvdw0s3jn54khce6mua7l"[:29] * 2,
    "N8N_ENCRYPTION_KEY": "c" * 64,
}
GENERATED = ("POSTGRES_PASSWORD", "AGENT_DB_PASSWORD", "N8N_DB_PASSWORD", "KB_SYNC_DB_PASSWORD",
             "N8N_SUPPORT_DB_PASSWORD", "BACKUP_DB_PASSWORD", "AGENT_API_KEY", "ADMIN_DB_PASSWORD")


def staged_input(answers=STAGED_ANSWERS):
    lines = [answers["OPENAI_API_KEY"], answers["OPENAI_MODEL"], answers["JUDGE_MODEL"]]
    lines += [answers[name] for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET",
                                         "BACKUP_AGE_RECIPIENT", "N8N_ENCRYPTION_KEY")]
    return "".join(f"{line}\n" for line in lines)


def run_new_env(folder, mode, typed, lang=None):
    """Runs the real helper in `folder` with `typed` on its standard input (as bytes: text mode on Windows would turn
    each \\n into \\r\\n, which a server's terminal never sends). `lang` sets the window's language (LANG)."""
    script = (ROOT / "deploy" / "new-env.sh").as_posix()
    env = None
    if lang is not None:
        env = {name: value for name, value in os.environ.items() if not name.startswith("LC_")} | {"LANG": lang}
    result = subprocess.run([BASH, script, mode], cwd=folder, input=typed.encode(), capture_output=True, timeout=60,
                            env=env)
    return subprocess.CompletedProcess(result.args, result.returncode, result.stdout.decode(), result.stderr.decode())


def read_env(path):
    """NAME=value lines as a dict; a value in single quotes loses them (how Docker Compose reads them)."""
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            name, _, value = line.partition("=")
            if len(value) >= 2 and value[0] == value[-1] == "'":
                value = value[1:-1]
            values[name] = value
    return values


def compose_names():
    """Every setting deploy/compose.yaml takes from .env without a default."""
    text = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    return {name for name, rest in re.findall(r"\$\{([A-Z0-9_]+)(:?-?)", text) if rest != ":-"}


def test_staged_writes_every_setting_the_server_needs(tmp_path):
    result = run_new_env(tmp_path, "staged", staged_input())
    assert result.returncode == 0, result.stderr
    env = read_env(tmp_path / ".env")
    assert {name for name in compose_names() if not env.get(name)} == set()
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "JUDGE_MODEL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                 "R2_ENDPOINT", "R2_BUCKET", "BACKUP_AGE_RECIPIENT"):
        assert env[name] == STAGED_ANSWERS[name], name


def test_the_agencys_time_zone_is_written_for_the_admin_app(tmp_path):
    """The admin app's times and chart start from this zone (admin settings, TIME_ZONE); written, not asked, so the
    agency can change the one line and a new server never depends on a default it can't see."""
    assert run_new_env(tmp_path, "staged", staged_input()).returncode == 0
    assert read_env(tmp_path / ".env")["TIME_ZONE"] == "America/New_York"


def test_highlevel_keys_are_never_asked(tmp_path):
    """Client keys are entered in the admin app, locked; .env no longer holds them."""
    result = run_new_env(tmp_path, "staged", staged_input())
    assert result.returncode == 0, result.stderr
    assert "HighLevel" not in result.stderr  # where the questions go
    assert "GHL_TOKENS" not in read_env(tmp_path / ".env")


def test_internal_passwords_are_made_up_fresh_each_time(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    for folder in (first, second):
        folder.mkdir()
        assert run_new_env(folder, "staged", staged_input()).returncode == 0
    one, two = read_env(first / ".env"), read_env(second / ".env")
    made_up = [one[name] for name in GENERATED]
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in made_up)
    assert len(set(made_up)) == len(GENERATED)  # no two logins share a password
    assert not set(made_up) & {two[name] for name in GENERATED}  # a second server gets its own
    assert one["DATABASE_URL"] == f"postgresql://agent_reader:{one['AGENT_DB_PASSWORD']}@postgres:5432/knowledge"


def test_a_fresh_key_pair_locks_and_opens_client_keys(tmp_path):
    """The admin app locks client keys with the public half and only the agent opens them with the private half;
    a new server gets its own pair, so the agency enters its client keys again."""
    from support_agent.keys import open_sealed, seal

    first, second = tmp_path / "first", tmp_path / "second"
    for folder in (first, second):
        folder.mkdir()
        assert run_new_env(folder, "staged", staged_input()).returncode == 0
    one, two = read_env(first / ".env"), read_env(second / ".env")
    assert open_sealed(one["GHL_KEY_PRIVATE"], seal(one["GHL_KEY_PUBLIC"], "pit-sample")) == "pit-sample"
    assert one["GHL_KEY_PRIVATE"] != two["GHL_KEY_PRIVATE"]


def test_the_old_systems_n8n_encryption_key_is_carried_over(tmp_path):
    """n8n 2.x keeps its own keys in its database, locked with N8N_ENCRYPTION_KEY: a restored n8n with a new key
    refuses to start (found in a restore rehearsal), so the old system's key is asked for, never made up."""
    result = run_new_env(tmp_path, "staged", staged_input())
    assert result.returncode == 0, result.stderr
    assert read_env(tmp_path / ".env")["N8N_ENCRYPTION_KEY"] == STAGED_ANSWERS["N8N_ENCRYPTION_KEY"]


def test_a_cut_off_n8n_encryption_key_is_asked_again(tmp_path):
    """The key is 64 hex characters; a wrong one would only show as n8n failing to start."""
    key = STAGED_ANSWERS["N8N_ENCRYPTION_KEY"]
    typed = type_wrong_first(staged_input(), key, key[:-1], key.upper(), "")
    result = run_new_env(tmp_path, "staged", typed)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("doesn't look right") == 3
    assert read_env(tmp_path / ".env")["N8N_ENCRYPTION_KEY"] == key


def test_capitals_are_refused_whatever_the_windows_language(tmp_path):
    """With LANG=en_US.UTF-8 (as Git Bash often sets it), a-f in a check follows dictionary order (a A b B ... f),
    so capitals A-E passed; a key in the wrong case would only show as n8n failing to start."""
    key, bucket = STAGED_ANSWERS["N8N_ENCRYPTION_KEY"], STAGED_ANSWERS["R2_BUCKET"]
    typed = type_wrong_first(type_wrong_first(staged_input(), key, "C" * 64), bucket, "Test-Bucket")
    result = run_new_env(tmp_path, "staged", typed, lang="en_US.UTF-8")
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("doesn't look right") == 2
    env = read_env(tmp_path / ".env")
    assert env["N8N_ENCRYPTION_KEY"] == key and env["R2_BUCKET"] == bucket


def test_an_existing_env_is_never_replaced(tmp_path):
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=the-working-one\n")
    result = run_new_env(tmp_path, "staged", staged_input())
    assert result.returncode != 0
    assert ".env already exists" in result.stderr
    assert (tmp_path / ".env").read_text() == "POSTGRES_PASSWORD=the-working-one\n"


def test_an_env_written_meanwhile_is_never_replaced(tmp_path):
    """Another helper (or a person) writes .env while this one waits for answers: the last step must not replace it."""
    script = (ROOT / "deploy" / "new-env.sh").as_posix()
    helper = subprocess.Popen([BASH, script, "staged"], cwd=tmp_path, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first, rest = staged_input().split("\n", 1)
    helper.stdin.write(f"{first}\n".encode())
    helper.stdin.flush()
    assert helper.stderr.read(1) == b"\n"  # the line break after a hidden answer: past its first check, waiting
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=the-working-one\n")
    _, err = helper.communicate(rest.encode(), timeout=60)
    assert helper.returncode != 0
    assert ".env already exists" in err.decode()
    assert (tmp_path / ".env").read_text() == "POSTGRES_PASSWORD=the-working-one\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]  # no temp file left behind


def type_wrong_first(typed, right, *wrong):
    """The answers with `wrong` ones typed before `right` (each wrong one must be asked again)."""
    return typed.replace(f"{right}\n", "".join(f"{w}\n" for w in wrong) + f"{right}\n", 1)


def test_a_wrong_paste_is_asked_again(tmp_path):
    key, bucket = STAGED_ANSWERS["OPENAI_API_KEY"], STAGED_ANSWERS["R2_BUCKET"]
    typed = type_wrong_first(type_wrong_first(staged_input(), key, "hello"), bucket, bucket + '"')
    result = run_new_env(tmp_path, "staged", typed)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("doesn't look right") == 2
    env = read_env(tmp_path / ".env")
    assert (env["OPENAI_API_KEY"], env["R2_BUCKET"]) == (key, bucket)


def test_a_cut_off_key_is_asked_again(tmp_path):
    """Each outside key has its real shape (R2 32 and 64 hex, OpenAI well over 40), so a paste that lost its end is
    caught here."""
    typed = staged_input()
    for right in (STAGED_ANSWERS["OPENAI_API_KEY"], STAGED_ANSWERS["R2_ACCESS_KEY_ID"],
                  STAGED_ANSWERS["R2_SECRET_ACCESS_KEY"]):
        typed = type_wrong_first(typed, right, right[:-1] if len(right) < 40 else right[:30])
    result = run_new_env(tmp_path, "staged", typed)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("doesn't look right") == 3
    env = read_env(tmp_path / ".env")
    assert env["R2_ACCESS_KEY_ID"] == STAGED_ANSWERS["R2_ACCESS_KEY_ID"]


def test_backup_settings_must_have_their_real_shape(tmp_path):
    """A storage address of another service, or a cut-off backup key, would only fail later, at the first backup."""
    endpoint, recipient = STAGED_ANSWERS["R2_ENDPOINT"], STAGED_ANSWERS["BACKUP_AGE_RECIPIENT"]
    typed = staged_input().replace(f"{endpoint}\n", f"https://example.com\n{endpoint}\n", 1) \
                          .replace(f"{recipient}\n", f"age1a\n{recipient[:-1]}\n{recipient}b\n{recipient}\n", 1)
    result = run_new_env(tmp_path, "staged", typed)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("doesn't look right") == 4  # one address, three keys: too short, cut off, b not used
    env = read_env(tmp_path / ".env")
    assert (env["R2_ENDPOINT"], env["BACKUP_AGE_RECIPIENT"]) == (endpoint, recipient)


def test_stopping_partway_leaves_no_env(tmp_path):
    result = run_new_env(tmp_path, "staged", "".join(staged_input().splitlines(keepends=True)[:2]))
    assert result.returncode != 0
    assert "no .env was written" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows has no owner-only file mode; runs on Linux (the server, CI)")
def test_only_the_owner_can_read_the_env(tmp_path):
    assert run_new_env(tmp_path, "staged", staged_input()).returncode == 0
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_staged_fills_what_reaches_outside_with_placeholders(tmp_path):
    assert run_new_env(tmp_path, "staged", staged_input()).returncode == 0
    env = read_env(tmp_path / ".env")
    assert env["N8N_WEBHOOK_URL"].endswith(".invalid/")  # a name no one can own
    assert "placeholder" in env["CLOUDFLARE_TUNNEL_TOKEN"]
    assert "SLACK_SIGNING_SECRET" not in env  # the database holds it (migration 016); the agent's copy went in v1.0.0
    # The admin app: test.invalid can never be reached, so no pass can be checked and nobody signs in (and without
    # ADMIN_TEST_KEYS no test pass either) until a person enters the real team.
    assert env["CF_TEAM_DOMAIN"] == "test.invalid"
    assert env["ADMIN_ORIGIN"].endswith(".invalid")
    assert "placeholder" in env["CF_AUDIENCE"] and "placeholder" in env["SLACK_BOT_TOKEN"]


# A server's outside answers, in the order asked: n8n's address, tunnel token, Access team, audience tag, admin
# address, Slack bot token.
SERVER_ANSWERS = ("https://n8n.example.com/", "eyJ0dW5uZWwiOiJ0ZXN0In0=", "example-team.cloudflareaccess.com", "d" * 64,
                  "https://admin.example.com", "xoxb-" + "1" * 12 + "-" + "2" * 13 + "-" + "e" * 24)


def server_input(answers=SERVER_ANSWERS):
    return staged_input() + "".join(f"{answer}\n" for answer in answers)


def test_server_asks_for_what_reaches_outside(tmp_path):
    result = run_new_env(tmp_path, "server", server_input())
    assert result.returncode == 0, result.stderr
    env = read_env(tmp_path / ".env")
    assert env["N8N_WEBHOOK_URL"] == "https://n8n.example.com/"
    assert "SLACK_SIGNING_SECRET" not in env and "signing secret" not in result.stdout + result.stderr  # never asked
    assert env["CLOUDFLARE_TUNNEL_TOKEN"] == "eyJ0dW5uZWwiOiJ0ZXN0In0="
    names = ("CF_TEAM_DOMAIN", "CF_AUDIENCE", "ADMIN_ORIGIN", "SLACK_BOT_TOKEN")
    assert tuple(env[name] for name in names) == SERVER_ANSWERS[2:]


def test_admin_settings_must_have_their_real_shape(tmp_path):
    """A wrong team, a cut-off audience tag or an address with a path would only show as nobody able to sign in; a
    user token (xoxp-) in place of the bot's would list the wrong person's channels."""
    team, audience, origin, slack = SERVER_ANSWERS[2:]
    typed = type_wrong_first(server_input(), team, "bluejay.example.com")
    typed = type_wrong_first(typed, audience, audience[:-1])
    typed = type_wrong_first(typed, origin, origin + "/", "http://admin.example.com")
    typed = type_wrong_first(typed, slack, "xoxp" + slack[4:])
    result = run_new_env(tmp_path, "server", typed)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("doesn't look right") == 5
    assert read_env(tmp_path / ".env")["ADMIN_ORIGIN"] == origin


def test_the_summary_names_every_setting_and_shows_no_value(tmp_path):
    result = run_new_env(tmp_path, "staged", staged_input())
    assert result.returncode == 0, result.stderr
    shown = result.stdout + result.stderr
    env = read_env(tmp_path / ".env")
    assert all(name in result.stdout for name in env)
    assert "password manager" in result.stdout
    secrets = [env[name] for name in (*GENERATED, "OPENAI_API_KEY", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                                      "N8N_ENCRYPTION_KEY", "GHL_KEY_PRIVATE")]
    assert not [value for value in secrets if value in shown]


def test_the_summary_names_the_n8n_logins_that_need_the_new_passwords(tmp_path):
    """With the old system's key, n8n's saved logins open after the restore, but the three that hold an internal
    password made up here still hold the old one: the admin must hear which, before publishing."""
    result = run_new_env(tmp_path, "staged", staged_input())
    assert result.returncode == 0, result.stderr
    assert "recreate" not in result.stdout
    assert "old system's" in result.stdout and "open here" in result.stdout
    for login, setting in (("Knowledge DB (kb_sync)", "KB_SYNC_DB_PASSWORD"),
                           ("Support DB (n8n_support)", "N8N_SUPPORT_DB_PASSWORD"), ("Agent API", "AGENT_API_KEY")):
        assert login in result.stdout and setting in result.stdout
    assert "before publishing" in result.stdout
    # A server restore starts the backup's workflows published, with those old passwords.
    assert "deploy/restore.sh staged" in result.stdout
