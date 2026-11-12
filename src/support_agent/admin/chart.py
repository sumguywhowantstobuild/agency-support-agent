"""The Drafts today chart: today's real drafts as one line per client, drawn by the server as an SVG, so the
security header holds. Hours count back from now, as the limit does, so the chart's end agrees with "this hour"."""

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo

from support_agent.admin.db import ClientRow, DraftTime

HOUR = timedelta(hours=1)
WIDTH, HEIGHT = 400, 100  # the SVG's own units; the page stretches them to its width
HEADROOM = 0.9  # the highest point stays under the plot's top edge
TOP_LINES = 4  # more can't be told apart; the rest share one line
OVERLAP = 20  # two end labels closer than this share of the plot's height collide
AXIS_LABELS = 5


@dataclass(frozen=True)
class ChartLine:
    name: str
    series: str  # "1" to "4", or "other": each has its own colour and dash in the design system
    counts: list[int]  # oldest first
    used: int  # the cards' "this hour" count, for the label at the line's end
    path: str
    end: int  # the label's height, as a share of the plot, in fives: a class places it (no inline styles)


@dataclass(frozen=True)
class Chart:
    lines: list[ChartLine]
    hours: list[str]  # when each point ends, oldest first
    axis: list[str]
    legend: bool  # the end labels would collide: show them below instead
    words: str  # said instead of a chart when there's too little to draw
    series_of: dict[str, str] = field(default_factory=dict)  # client key -> the series of its line: for its card


def _midnight(now: datetime, zone: tzinfo) -> datetime:
    today: date = now.astimezone(zone).date()
    return datetime(today.year, today.month, today.day, tzinfo=zone)


def today_starts(now: datetime, zone: tzinfo) -> datetime:
    """Midnight in the zone, as a moment: where "today" begins, and what drafts_today is asked from."""
    return _midnight(now, zone)


def _clock(moment: datetime, zone: tzinfo) -> str:
    return moment.astimezone(zone).strftime("%H:%M")


def _plural(count: int) -> str:
    return f"{count} draft{'s' if count != 1 else ''} so far today"


def build_chart(drafts: list[DraftTime], clients: list[ClientRow], now: datetime, zone: tzinfo) -> Chart:
    """`clients` gives the names and this hour's counts; a draft of anyone not in it is left out."""
    # All in UTC: Python subtracts two times in one zone by the clock, so a clock change's 23 or 25 hours would be lost.
    now = now.astimezone(timezone.utc)
    start = _midnight(now, zone).astimezone(timezone.utc)
    points = max(math.ceil((now - start) / HOUR), 1)
    names = {row.client_key: row for row in clients}
    per_client: dict[str, list[int]] = {}
    for draft in drafts:
        made = draft.created_at.astimezone(timezone.utc)
        if draft.client_key not in names or not start <= made <= now:
            continue
        back = (now - made) // HOUR  # exactly 60 minutes ago is the hour before: the limit's own window
        per_client.setdefault(draft.client_key, [0] * points)[points - 1 - min(back, points - 1)] += 1
    total = sum(sum(counts) for counts in per_client.values())
    if not total:
        return Chart(lines=[], hours=[], axis=[], legend=False, words="No drafts yet today")
    if points < 2:
        return Chart(lines=[], hours=[], axis=[], legend=False, words=_plural(total))

    ranked = sorted(per_client, key=lambda key: (-sum(per_client[key]), names[key].display_name.casefold()))
    picked = [(names[key].display_name, str(place), per_client[key], names[key].used_this_hour)
              for place, key in enumerate(ranked[:TOP_LINES], start=1)]
    rest = ranked[TOP_LINES:]
    series_of = {key: str(place) for place, key in enumerate(ranked[:TOP_LINES], start=1)}
    series_of |= dict.fromkeys(rest, "other")
    if rest:
        together = [sum(per_client[key][i] for key in rest) for i in range(points)]
        picked.append(("Others", "other", together, sum(names[key].used_this_hour for key in rest)))

    highest = max(max(counts) for _, _, counts, _ in picked)
    step = WIDTH / (points - 1)

    def height(count: int) -> float:
        return round(HEIGHT - count / highest * HEIGHT * HEADROOM, 2)

    lines = []
    for name, series, counts, used in picked:
        path = "M" + "L".join(f"{round(i * step, 2):g} {height(count):g}" for i, count in enumerate(counts))
        lines.append(ChartLine(name=name, series=series, counts=counts, used=used, path=path,
                               end=5 * round(height(counts[-1]) / 5)))
    ends = sorted(line.end for line in lines)
    every = [now - HOUR * (points - 1 - i) for i in range(points)]
    spaced = [every[round(i * (points - 1) / (AXIS_LABELS - 1))] for i in range(AXIS_LABELS - 1)] if points >= AXIS_LABELS else every[:-1]
    return Chart(lines=lines, hours=[_clock(moment, zone) for moment in every],
                 axis=[_clock(moment, zone) for moment in spaced] + ["Now"],
                 legend=any(b - a < OVERLAP for a, b in zip(ends, ends[1:])), words="", series_of=series_of)
