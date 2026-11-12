"""The Drafts today chart: which hours, which lines and what it says when there's little to draw."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from support_agent.admin.chart import build_chart
from support_agent.admin.db import ClientRow, DraftTime
from support_agent.admin.templating import templates

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 3, 15, 40, tzinfo=NEW_YORK)  # 15 h 40 after midnight, so 16 points


def client(key, name=None, used=0, status="active"):
    return ClientRow(client_key=key, display_name=name or key.title(), channel_id=f"C{key}", location_id=f"loc-{key}",
                     drafts_per_hour=20, used_this_hour=used, status=status, pause_reason=None, pause_note=None)


def at(key, minutes_ago, now=NOW):
    # Through UTC: subtracting inside one zone goes by the clock, not real time, across a clock change.
    return DraftTime(client_key=key, created_at=now.astimezone(timezone.utc) - timedelta(minutes=minutes_ago))


def counts(chart, name):
    return next(line.counts for line in chart.lines if line.name == name)


def test_hours_count_back_from_now():
    chart = build_chart([at("a", 10), at("a", 59), at("a", 61), at("a", 121)], [client("a")], NOW, NEW_YORK)

    assert counts(chart, "A")[-3:] == [1, 1, 2]


def test_the_last_point_is_the_last_sixty_minutes():
    chart = build_chart([at("a", 0), at("a", 59), at("a", 60)], [client("a")], NOW, NEW_YORK)

    # A draft exactly 60 minutes ago belongs to the hour before: the limit's own window is "the last 60 minutes".
    assert counts(chart, "A")[-2:] == [1, 2]


def test_first_point_can_be_a_part_hour():
    chart = build_chart([at("a", 15 * 60 + 39)], [client("a")], NOW, NEW_YORK)

    assert len(chart.hours) == 16
    assert counts(chart, "A")[0] == 1


def test_midnight_in_the_zone_not_utc():
    utc_night = datetime(2026, 11, 3, 3, 30, tzinfo=timezone.utc)  # 22:30 the day before in New York
    yesterday_evening = DraftTime(client_key="a", created_at=utc_night - timedelta(hours=1))
    this_morning_utc = DraftTime(client_key="a", created_at=datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc))
    now = datetime(2026, 11, 3, 9, 0, tzinfo=NEW_YORK)

    chart = build_chart([yesterday_evening, this_morning_utc, at("a", 5, now)], [client("a")], now, NEW_YORK)

    assert sum(counts(chart, "A")) == 1  # 02:00 UTC is 21:00 the day before: it isn't today in New York


def test_a_clock_change_day():
    now = datetime(2026, 11, 1, 23, 30, tzinfo=NEW_YORK)  # the 25-hour day: clocks went back at 2am

    chart = build_chart([at("a", 24 * 60 + 15, now), at("a", 5, now)], [client("a")], now, NEW_YORK)

    assert len(chart.hours) == 25  # 24 h 30 of real time since midnight
    assert sum(counts(chart, "A")) == 2


def test_top_four_and_others():
    clients = [client(key, used=1) for key in "abcdef"]
    drafts = [at(key, 5) for key, times in zip("abcdef", (6, 5, 4, 3, 2, 1)) for _ in range(times)]

    chart = build_chart(drafts, clients, NOW, NEW_YORK)

    assert [line.name for line in chart.lines] == ["A", "B", "C", "D", "Others"]
    assert [line.series for line in chart.lines] == ["1", "2", "3", "4", "other"]
    assert counts(chart, "Others")[-1] == 3
    assert chart.lines[-1].used == 2


def test_each_client_on_a_line_is_told_which_one_for_its_card():
    clients = [client(key, used=1) for key in "abcdefg"]  # g has no draft today
    drafts = [at(key, 5) for key, times in zip("abcdef", (6, 5, 4, 3, 2, 1)) for _ in range(times)]

    chart = build_chart(drafts, clients, NOW, NEW_YORK)

    assert chart.series_of == {"a": "1", "b": "2", "c": "3", "d": "4", "e": "other", "f": "other"}
    assert build_chart([], clients, NOW, NEW_YORK).series_of == {}  # nothing drawn, nothing to match


def test_a_client_keeps_its_place_when_ties_are_broken_by_name():
    drafts = [at("b", 5), at("a", 5)]

    chart = build_chart(drafts, [client("b"), client("a")], NOW, NEW_YORK)

    assert [line.name for line in chart.lines] == ["A", "B"]


def test_clients_without_a_draft_today_get_no_line():
    chart = build_chart([at("a", 5)], [client("a"), client("b")], NOW, NEW_YORK)

    assert [line.name for line in chart.lines] == ["A"]


def test_a_draft_of_a_client_no_longer_listed_is_left_out():
    chart = build_chart([at("gone", 5), at("a", 5)], [client("a")], NOW, NEW_YORK)

    assert [line.name for line in chart.lines] == ["A"]


def test_empty_says_so():
    chart = build_chart([], [client("a")], NOW, NEW_YORK)

    assert chart.words == "No drafts yet today"
    assert not chart.lines


def test_under_two_points_says_the_count():
    now = datetime(2026, 11, 3, 0, 40, tzinfo=NEW_YORK)

    assert build_chart([at("a", 5, now)], [client("a")], now, NEW_YORK).words == "1 draft so far today"
    assert build_chart([at("a", 5, now), at("a", 6, now)], [client("a")], now, NEW_YORK).words == "2 drafts so far today"


def test_labels_that_would_overlap_become_a_legend():
    apart = build_chart([at("a", 5)] * 10 + [at("b", 5)], [client("a"), client("b")], NOW, NEW_YORK)
    close = build_chart([at("a", 5)] * 10 + [at("b", 5)] * 9, [client("a"), client("b")], NOW, NEW_YORK)

    assert not apart.legend
    assert close.legend


def test_each_line_is_a_path_in_the_plot_with_straight_segments():
    chart = build_chart([at("a", 5), at("a", 70)], [client("a")], NOW, NEW_YORK)

    path = chart.lines[0].path
    assert path.startswith("M0 ")
    assert "C" not in path and "S" not in path and path.count("L") == 15


def test_the_end_label_carries_the_cards_count():
    chart = build_chart([at("a", 5)], [client("a", used=7)], NOW, NEW_YORK)

    assert chart.lines[0].used == 7


def test_end_positions_are_fives_so_a_class_can_place_them():
    chart = build_chart([at("a", 5)] * 3 + [at("b", 5)], [client("a"), client("b")], NOW, NEW_YORK)

    assert all(line.end % 5 == 0 and 0 <= line.end <= 100 for line in chart.lines)


def test_the_axis_ends_at_now_in_the_zone():
    chart = build_chart([at("a", 5)], [client("a")], NOW, NEW_YORK)

    assert chart.axis[-1] == "Now"
    assert chart.axis[0] == "00:40"  # 24-hour, like every other time on the app's screens


def render(chart):
    return templates.get_template("chart.html").render(chart=chart)


def test_names_are_escaped():
    name = '<script>alert("x")</script>'

    page = render(build_chart([at("a", 5)], [client("a", name)], NOW, NEW_YORK))

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_the_legend_is_always_there_and_the_chart_asks_for_it_when_labels_collide():
    apart = render(build_chart([at("a", 5)] * 10 + [at("b", 5)], [client("a"), client("b")], NOW, NEW_YORK))
    close = render(build_chart([at("a", 5)] * 10 + [at("b", 5)] * 9, [client("a"), client("b")], NOW, NEW_YORK))

    assert 'class="ds-chart__legend"' in apart and 'class="ds-chart__legend"' in close  # a phone shows it in both
    assert "ds-chart--legend" not in apart
    assert "ds-chart--legend" in close


def test_a_chart_swapped_in_later_does_not_draw_again():
    chart = build_chart([at("a", 5)], [client("a")], NOW, NEW_YORK)

    assert "ds-chart--still" not in render(chart)
    assert "ds-chart--still" in templates.get_template("chart.html").render(chart=chart, still=True)


def test_the_page_uses_no_inline_styles():
    page = render(build_chart([at("a", 5)] * 3 + [at("b", 5)], [client("a"), client("b")], NOW, NEW_YORK))

    assert "style=" not in page
    assert "ds-chart__line--1" in page and "ds-chart__line--2" in page


def test_the_numbers_are_there_for_a_screen_reader():
    page = render(build_chart([at("a", 5), at("a", 6), at("a", 70)], [client("a")], NOW, NEW_YORK))

    assert "ds-chart__table" in page
    assert '<th scope="row">15:40</th><td>2</td>' in page  # the last 60 minutes
    assert '<th scope="row">14:40</th><td>1</td>' in page  # the hour before
    assert '<th scope="row">13:40</th><td>0</td>' in page
