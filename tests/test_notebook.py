"""What the deploy/apply.sh menu reads from the notebook deploy/.tested."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from notebook import copy_data_from, docker_changed, edited_in_copy, main, parse, waiting  # noqa: E402

IMG_A = "sha256:aaaa|gpt-6-luna"


def line(time, kind, key, fingerprint, note=""):
    return f"{time}\t{kind}\t{key}\t{fingerprint}\t{note}\n"


def test_an_agent_that_passed_on_the_copy_and_is_not_live_is_waiting():
    entries = parse(line("2026-10-15T01:00:00Z", "agent", "agent", IMG_A, "tests passed, health ok"))
    assert [(w.kind, w.key, w.fingerprint) for w in waiting(entries)] == [("agent", "agent", IMG_A)]


def test_an_agent_that_went_live_is_no_longer_waiting():
    entries = parse(line("2026-10-15T01:00:00Z", "agent", "agent", IMG_A, "tests passed, health ok")
                    + line("2026-10-15T01:05:00Z", "live-agent", "agent", IMG_A, "was 6529fc359b41, gpt-6-luna"))
    assert waiting(entries) == []


IMG_B = "sha256:bbbb|gpt-6-luna"


def test_only_the_newest_agent_pass_is_offered_because_live_agent_takes_the_newest():
    entries = parse(line("2026-10-15T01:00:00Z", "agent", "agent", IMG_A)
                    + line("2026-10-15T02:00:00Z", "agent", "agent", IMG_B))
    assert [w.fingerprint for w in waiting(entries)] == [IMG_B]


def test_going_back_to_an_older_agent_is_offered_when_live_now_runs_a_newer_one():
    entries = parse(line("2026-10-15T01:00:00Z", "agent", "agent", IMG_A)
                    + line("2026-10-15T01:05:00Z", "live-agent", "agent", IMG_A)
                    + line("2026-10-15T02:00:00Z", "agent", "agent", IMG_B)
                    + line("2026-10-15T02:05:00Z", "live-agent", "agent", IMG_B)
                    + line("2026-10-15T03:00:00Z", "agent", "agent", IMG_A))
    assert [w.fingerprint for w in waiting(entries)] == [IMG_A]


def test_a_migration_is_waiting_until_that_exact_file_is_live():
    passed = line("2026-10-15T01:00:00Z", "migration", "012_x.sql", "f1")
    assert [w.key for w in waiting(parse(passed), files={"012_x.sql": "f1"})] == ["012_x.sql"]
    went_live = passed + line("2026-10-15T01:05:00Z", "live-migration", "012_x.sql", "f1")
    assert waiting(parse(went_live), files={"012_x.sql": "f1"}) == []


def test_a_migration_file_edited_after_it_passed_is_not_offered_because_live_would_refuse_it():
    passed = line("2026-10-15T01:00:00Z", "migration", "012_x.sql", "f1")
    assert waiting(parse(passed), files={"012_x.sql": "f2"}) == []
    assert waiting(parse(passed), files={}) == []  # file deleted


IMAGES = "postgres=pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c n8n=n8nio/n8n:2.43.5@sha256:4fc4"


def test_new_software_versions_are_waiting_until_live_runs_them():
    passed = line("2026-10-21T01:00:00Z", "images", "images", IMAGES, "started healthy on the copy")
    assert [(w.kind, w.fingerprint) for w in waiting(parse(passed), images=IMAGES)] == [("images", IMAGES)]
    went_live = passed + line("2026-10-21T01:30:00Z", "live-images", "images", IMAGES)
    assert waiting(parse(went_live), images=IMAGES) == []


def test_software_versions_changed_in_the_compose_files_after_the_pass_are_not_offered():
    passed = line("2026-10-21T01:00:00Z", "images", "images", IMAGES)
    assert waiting(parse(passed), images=IMAGES.replace("2.43.5", "2.43.8")) == []


def test_the_menu_passes_the_compose_files_software_versions():
    passed = line("2026-10-21T01:00:00Z", "images", "images", IMAGES)
    assert main(["waiting", "--images", IMAGES], passed).split("\t")[:3] == ["images", "images", IMAGES]
    assert main(["waiting", "--images", "other"], passed) == ""


def test_a_workflow_is_waiting_until_live_has_the_version_that_passed_and_not_if_edited_since():
    passed = (line("2026-10-15T01:00:00Z", "live-workflow", "wf1", "v1")
              + line("2026-10-15T02:00:00Z", "workflow", "wf1", "v2"))
    assert [w.key for w in waiting(parse(passed), copy_workflows={"wf1": "v2"})] == ["wf1"]
    assert waiting(parse(passed), copy_workflows={"wf1": "v3"}) == []  # edited in the copy after it passed
    went_live = passed + line("2026-10-15T02:05:00Z", "live-workflow", "wf1", "v2")
    assert waiting(parse(went_live), copy_workflows={"wf1": "v2"}) == []


REFRESH = (line("2026-10-14T03:00:00Z", "refresh", "copy", "2026-10-14T02:08Z")
           + line("2026-10-14T03:00:01Z", "baseline", "wf1", "v0")
           + line("2026-10-15T03:00:00Z", "refresh", "copy", "2026-10-15T02:59Z")
           + line("2026-10-15T03:00:01Z", "baseline", "wf1", "v1")
           + line("2026-10-15T03:00:01Z", "baseline", "wf2", "v1"))


def test_a_workflow_is_marked_edited_when_its_version_differs_from_the_newest_refresh_or_it_is_new():
    # A refresh takes a fresh backup of live first, so the versions it writes down are live's at that moment.
    copy = {"wf1": "v2", "wf2": "v1", "wf3": "v1"}
    assert edited_in_copy(copy, parse(REFRESH)) == {"wf1", "wf3"}
    assert edited_in_copy({"wf1": "v0"}, parse(REFRESH)) == {"wf1"}  # an older refresh's version doesn't count


def test_a_workflow_that_passed_is_not_marked_edited_until_it_changes_again():
    entries = parse(REFRESH + line("2026-10-15T04:00:00Z", "workflow", "wf1", "v2"))
    assert edited_in_copy({"wf1": "v2"}, entries) == set()
    assert edited_in_copy({"wf1": "v3"}, entries) == {"wf1"}


def test_nothing_is_marked_before_the_first_refresh_writes_versions_down():
    assert edited_in_copy({"wf1": "v2"}, parse(line("2026-10-15T01:00:00Z", "agent", "agent", IMG_A))) == set()


def test_the_copys_data_is_from_the_backup_its_newest_refresh_restored_or_unknown():
    assert copy_data_from(parse(line("2026-10-15T01:00:00Z", "agent", "agent", IMG_A))) is None
    entries = parse(line("2026-10-14T03:00:00Z", "refresh", "copy", "2026-10-14T02:08Z")
                    + line("2026-10-15T03:00:00Z", "refresh", "copy", "2026-10-15T02:31Z"))
    assert copy_data_from(entries) == "2026-10-15T02:31Z"


def test_a_damaged_line_stops_with_its_line_number_instead_of_being_skipped():
    good = line("2026-10-15T01:00:00Z", "agent", "agent", IMG_A)
    with pytest.raises(ValueError, match="line 2"):
        parse(good + "2026-10-15T02:00:00Z\tagent\n" + good)


def test_the_menu_gets_one_tab_separated_line_per_waiting_change_and_one_id_per_edited_workflow():
    text = (line("2026-10-15T01:00:00Z", "agent", "agent", IMG_A)
            + line("2026-10-15T02:00:00Z", "migration", "012_x.sql", "f1")
            + line("2026-10-15T03:00:00Z", "workflow", "wf1", "v2"))
    out = main(["waiting", "--file", "012_x.sql=f1", "--workflow", "wf1=v2"], text)
    assert out.splitlines() == ["agent\tagent\t" + IMG_A + "\t2026-10-15T01:00:00Z",
                                "migration\t012_x.sql\tf1\t2026-10-15T02:00:00Z",
                                "workflow\twf1\tv2\t2026-10-15T03:00:00Z"]
    assert main(["edited", "--copy", "wf1=v3", "--copy", "wf2=v1"], text + REFRESH) == "wf1"
    assert main(["data-from"], text) == ""


def test_with_the_copy_stopped_a_passed_workflow_is_still_listed_because_its_version_is_unknown_not_live():
    passed = line("2026-10-15T02:00:00Z", "workflow", "wf1", "v2")
    assert [w.key for w in waiting(parse(passed), copy_workflows=None)] == ["wf1"]
    assert main(["waiting", "--copy-stopped"], passed).splitlines() == ["workflow\twf1\tv2\t2026-10-15T02:00:00Z"]


DOCKER_OLD = "Docker Desktop 4.94.0 (241994) | engine 29.8.2 | store overlay2"
DOCKER_NEW = "Docker Desktop 4.95.0 (242831) | engine 29.8.2 | store overlayfs"


def test_the_menu_warns_when_docker_changed_since_the_newest_pass_on_the_copy():
    # Docker Desktop once updated itself and switched its image store; the next go-live was refused (safely).
    entries = parse(line("2026-10-14T12:20:21Z", "agent", "agent", IMG_A)
                    + line("2026-10-14T12:20:21Z", "platform", "docker", DOCKER_OLD))
    assert docker_changed(entries, DOCKER_NEW) == DOCKER_OLD
    assert docker_changed(entries, DOCKER_OLD) is None
    assert docker_changed(parse(""), DOCKER_NEW) is None  # nothing recorded yet: nothing to compare with
    newer = entries + parse(line("2026-10-15T03:00:00Z", "platform", "docker", DOCKER_NEW))
    assert docker_changed(newer, DOCKER_NEW) is None
    assert main(["docker-changed", "--now", DOCKER_NEW], "".join(
        line("2026-10-14T12:20:21Z", "platform", "docker", DOCKER_OLD))) == DOCKER_OLD
