"""The live system (compose.dev.yaml) and the server version (deploy/compose.yaml, which the test copy also runs) must
stay the same apart from the lines in ALLOWED, so a change that passes on the test copy behaves the same live."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MISSING = object()  # a line only one file has

# Lines the two files may differ on (a line and everything under it), each with its reason.
ALLOWED = {
    "name",  # own project names: support-agent-dev on the PC, support-agent on a server
    "services.postgres.ports",  # the server publishes no ports; the PC has 127.0.0.1 ones for checks
    "services.n8n.ports",
    "services.agent.ports",
    "services.admin.ports",
    "services.n8n.environment.N8N_EDITOR_BASE_URL",  # each machine's own editor address
    "services.agent.build",  # deploy/ sits one folder down, so the build folder is written differently
    "services.admin.build",
    "services.backup.build.context",
}


def flatten(value, path=""):
    if not isinstance(value, dict):
        return {path: value}
    return {line: item for key, child in value.items()
            for line, item in flatten(child, f"{path}.{key}" if path else str(key)).items()}


def allowed(line):
    return any(line == a or line.startswith(a + ".") for a in ALLOWED)


def differences(dev, deploy):
    a, b = flatten(dev), flatten(deploy)
    return {line for line in a.keys() | b.keys() if a.get(line, MISSING) != b.get(line, MISSING) and not allowed(line)}


def test_a_planted_difference_is_caught():
    dev = {"services": {"agent": {"image": "a:1", "environment": {"OPENAI_MODEL": "x"}}}}
    deploy = {"services": {"agent": {"image": "a:1", "environment": {"OPENAI_MODEL": "y"}}}}
    assert differences(dev, deploy) == {"services.agent.environment.OPENAI_MODEL"}


def test_a_line_only_one_file_has_is_caught():
    dev = {"volumes": {"pgdata": None, "n8ndata": None}}
    deploy = {"volumes": {"pgdata": None}}
    assert differences(dev, deploy) == {"volumes.n8ndata"}


def load(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_the_live_and_server_files_match():
    assert differences(load("compose.dev.yaml"), load("deploy/compose.yaml")) == set()


def test_n8n_features_we_dont_use_stay_off():
    # The remote-control interface (public API), outside packages, usage reports and code steps reading
    # the container's settings. The drift test above holds the server file to the same lines.
    env = load("compose.dev.yaml")["services"]["n8n"]["environment"]
    assert {name: env.get(name) for name in OFF} == OFF


OFF = {
    "N8N_PUBLIC_API_DISABLED": "true",
    "N8N_COMMUNITY_PACKAGES_ENABLED": "false",
    "N8N_DIAGNOSTICS_ENABLED": "false",
    "N8N_BLOCK_ENV_ACCESS_IN_NODE": "true",
}


def test_the_agent_no_longer_needs_the_slack_secret():
    """Clicks are checked in the database (migration 016); a required secret here would stop a new server's agent."""
    for name in ("compose.dev.yaml", "deploy/compose.yaml"):
        environment = yaml.safe_load((ROOT / name).read_text())["services"]["agent"]["environment"]
        assert "SLACK_SIGNING_SECRET" not in environment, name


COMPOSE_FILES = ("compose.dev.yaml", "deploy/compose.yaml", "deploy/compose.copy.yaml")


def test_no_service_has_a_place_for_the_old_plain_keys():
    """Client keys live in the database, locked. A GHL_TOKENS line would invite plain keys back
    into .env; the one-time move passes them in for its run only."""
    for name in COMPOSE_FILES:
        for service, settings in load(name)["services"].items():
            assert "GHL_TOKENS" not in settings.get("environment", {}), f"{name}: {service}"


def test_admin_container_never_gets_the_private_key():
    """Only the agent opens client keys: the admin app gets its own settings and nothing else from .env,
    so a hole in the admin app can't reach the private half, the OpenAI key or the database admin password."""
    from support_agent.admin.settings import AdminSettings

    admin_names = {name.upper() for name in AdminSettings.model_fields}
    for name in COMPOSE_FILES:
        services = load(name)["services"]
        admin = services["admin"]
        assert "env_file" not in admin, name  # the whole .env would come with it
        assert set(admin.get("environment", {})) <= admin_names, name
        for setting, value in admin.get("environment", {}).items():  # a right name could still carry another's value
            for secret in ("GHL_KEY_PRIVATE", "OPENAI_API_KEY", "POSTGRES_PASSWORD", "GHL_TOKENS"):
                assert secret not in str(value), f"{name}: admin {setting} reads {secret}"
        if name != "deploy/compose.copy.yaml":  # the copy's file only adds to the server's
            assert "GHL_KEY_PRIVATE" in services["agent"]["environment"], name
            assert set(admin["environment"]) == admin_names - {"ADMIN_TEST_KEYS"}, name  # test keys: copy only


def test_the_time_zone_is_optional_in_compose_and_defaults_to_bluejays():
    """An .env from before the setting existed must keep starting the admin app (it has no TIME_ZONE line)."""
    for name in ("compose.dev.yaml", "deploy/compose.yaml"):
        assert load(name)["services"]["admin"]["environment"]["TIME_ZONE"] == "${TIME_ZONE:-America/New_York}", name


def test_dockerfiles_fail_on_build_check_warnings():
    """Docker's build check (lint) only warns by default; `check=error=true` makes a warning stop the build, so a
    risky line (an undefined variable, a mistyped keyword) can't slip into an image. Docker reads the directive only
    when it is the very first line, before any other comment or instruction."""
    for name in ("Dockerfile", "Dockerfile.backup"):
        first_line = (ROOT / name).read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "# check=error=true", name


def test_the_tunnel_always_connects_over_ipv4():
    # Docker's network here has no IPv6. On "auto" (cloudflared's default) the tunnel picked IPv6 after a host restart,
    # never tried IPv4 and stayed down. The drift test above holds the server file to the same line.
    env = load("compose.dev.yaml")["services"]["tunnel"]["environment"]
    assert env.get("TUNNEL_EDGE_IP_VERSION") == "4"
