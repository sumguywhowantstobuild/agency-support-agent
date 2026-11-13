"""The colours people actually get, read from the app's style files, and WCAG contrast (moved from the design system's
checks). A colour that can't be read is a failure, never a skip."""

import re
from pathlib import Path

DS = Path(__file__).resolve().parents[2] / "src" / "support_agent" / "admin" / "static" / "ds"
THEMES = ["light", "dark"]
CLIENTS = ["base", "bluejay"]

# (foreground role, background role, minimum ratio): 4.5 is WCAG AA for text, 3.0 for non-text parts of a control.
PAIRS = [
    ("color.text.primary", "color.bg.canvas", 4.5),
    ("color.text.primary", "color.bg.surface", 4.5),
    ("color.text.primary", "color.bg.raised", 4.5),
    ("color.text.secondary", "color.bg.canvas", 4.5),
    ("color.text.secondary", "color.bg.surface", 4.5),
    ("color.text.on-accent", "color.accent.default", 4.5),
    ("color.text.on-accent", "color.accent.hover", 4.5),
    ("color.accent.default", "color.bg.canvas", 3.0),
    ("color.border.input", "color.bg.surface", 3.0),
    ("color.focus.ring", "color.bg.canvas", 3.0),
    ("color.focus.ring", "color.bg.surface", 3.0),
    # The status colours are also used as normal-size text (plain status, card labels, error lines): 4.5.
    ("color.status.ok", "color.bg.surface", 4.5),
    ("color.status.warn", "color.bg.surface", 4.5),
    ("color.status.fail", "color.bg.surface", 4.5),
    ("color.status.idle", "color.bg.surface", 4.5),
    ("color.status.ok", "color.bg.raised", 4.5),
    ("color.status.warn", "color.bg.raised", 4.5),
    ("color.status.fail", "color.bg.raised", 4.5),
    ("color.status.idle", "color.bg.raised", 4.5),
    ("color.accent.text", "color.bg.selected", 4.5),
    ("color.text.primary", "color.bg.subtle", 4.5),
    ("color.text.primary", "color.bg.selected", 4.5),
    ("color.text.muted", "color.bg.canvas", 4.5),
    ("color.data.series-1", "color.bg.surface", 3.0),
    ("color.data.series-1", "color.bg.raised", 3.0),
    ("color.data.series-2", "color.bg.surface", 3.0),
    ("color.data.series-2", "color.bg.raised", 3.0),
    ("color.data.series-3", "color.bg.surface", 3.0),
    ("color.data.series-3", "color.bg.raised", 3.0),
    ("color.data.series-4", "color.bg.surface", 3.0),
    ("color.data.series-4", "color.bg.raised", 3.0),
    ("color.text.primary", "color.bg.inset", 4.5),
    ("color.text.muted", "color.bg.inset", 4.5),
    # The Needs you items and the initial of a Staff row sit on the inset ground; so do their secondary words and the
    # at-the-limit title.
    ("color.text.secondary", "color.bg.inset", 4.5),
    ("color.status.warn", "color.bg.inset", 4.5),
    ("color.text.muted", "color.bg.surface", 4.5),
    ("color.text.muted", "color.bg.raised", 4.5),
    ("color.text.secondary", "color.bg.raised", 4.5),
    ("color.accent.text", "color.bg.canvas", 4.5),
    ("color.accent.text", "color.bg.surface", 4.5),
    ("color.accent.text", "color.bg.raised", 4.5),
    ("color.text.on-inverse", "color.bg.inverse", 4.5),
    ("color.status.ok", "color.status.ok-soft", 4.5),
    ("color.status.warn", "color.status.warn-soft", 4.5),
    ("color.status.fail", "color.status.fail-soft", 4.5),
    ("color.status.idle", "color.status.idle-soft", 4.5),
    ("color.border.input", "color.bg.canvas", 3.0),
    # The danger button sets status.fail text on bg.subtle, and on border.default when hovered.
    ("color.status.fail", "color.bg.subtle", 4.5),
    ("color.status.fail", "color.border.default", 4.5),
    # The switch is a control: its track must show against the card and the panel, and its knob against the track.
    ("color.control.switch-thumb", "color.control.switch-off", 3.0),
    ("color.control.switch-thumb", "color.control.switch-on", 3.0),
    ("color.control.switch-off", "color.bg.surface", 3.0),
    ("color.control.switch-off", "color.bg.raised", 3.0),
    ("color.control.switch-on", "color.bg.surface", 3.0),
    ("color.control.switch-on", "color.bg.raised", 3.0),
    # The ring on the pressed item and the current link must show (3:1) against the 5% wash it sits on. That wash
    # darkens the ground by about 10%, so 3.5 against the ground keeps 3:1 on the track.
    ("color.control.selected-ring", "color.bg.surface", 3.5),
    ("color.control.selected-ring", "color.bg.canvas", 3.5),
    ("color.control.selected-ring", "color.bg.raised", 3.5),
    # The secondary button's hover and the sheet's step dot set text on border.default.
    ("color.text.primary", "color.border.default", 4.5),
    ("color.text.secondary", "color.border.default", 4.5),
]

# The hover wash is translucent: what the reader sees is the wash over the ground the control sits on.
WASH = "color.overlay.hover"
WASH_GROUNDS = ("color.bg.surface", "color.bg.canvas", "color.bg.raised")
WASH_PAIRS = [
    ("color.control.selected-ring", 3.0),
    ("color.focus.ring", 3.0),
    ("color.text.primary", 4.5),
    ("color.text.secondary", 4.5),
    ("color.text.muted", 4.5),
    ("color.accent.text", 4.5),
    ("color.status.warn", 4.5),
]

HEX = re.compile(r"#[0-9a-fA-F]{6}")
RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")


def _block(css: str, selector: str) -> dict[str, str]:
    """The custom properties of the rule whose selector list holds exactly `selector`, at the top level of the file."""
    found: dict[str, str] = {}
    for prelude, body in re.findall(r"(?:^|\n)([^\s{}@][^{}]*)\{([^{}]*)\}", css):
        if selector in [part.strip() for part in prelude.split(",")]:
            found.update({name: " ".join(value.split()) for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)})
    return found


def values(theme: str, client: str = "bluejay") -> dict[str, str]:
    """Every custom property a page in this theme gets, with the same order of winners as the browser."""
    css = (DS / "ds.css").read_text(encoding="utf-8")
    found = _block(css, ":root")
    if theme == "dark":
        found |= _block(css, '[data-theme="dark"]')
    found |= _block(css, '[data-mode="productive"]')
    if client == "bluejay":
        layer = (DS / "clients" / "bluejay.css").read_text(encoding="utf-8")
        found |= _block(layer, '[data-mode="productive"][data-client="bluejay"]')
        if theme == "dark":
            found |= _block(layer, '[data-mode="productive"][data-client="bluejay"][data-theme="dark"]')
    return found


def role(found: dict[str, str], name: str) -> str:
    """The value of a role such as color.text.primary; a missing one is a failure, so a check can't pass on nothing."""
    prop = "--ds-" + name.replace(".", "-")
    assert prop in found, f"{prop} isn't in the style files: this colour can't be checked"
    return found[prop]


def _channels(colour: str) -> tuple[int, int, int]:
    assert HEX.fullmatch(colour), f"{colour!r} isn't #rrggbb: this colour can't be checked"
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _luminance(colour: str) -> float:
    linear = [(c / 255 / 12.92) if c / 255 <= 0.03928 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in _channels(colour)]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def over(top: str, ground: str) -> str:
    """The #rrggbb a translucent rgba() or #rrggbb makes on the opaque `ground`."""
    if HEX.fullmatch(top):
        return top
    match = RGBA.fullmatch(top)
    assert match, f"{top!r} isn't rgba(): this colour can't be checked"
    alpha = float(match.group(4)) if match.group(4) else 1.0
    above = [int(match.group(i)) for i in (1, 2, 3)]
    mixed = [round(alpha * a + (1 - alpha) * b) for a, b in zip(above, _channels(ground))]
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def pair_failures(theme: str, client: str) -> list[str]:
    found = values(theme, client)
    failures = []
    for fore, back, minimum in PAIRS:
        ratio = contrast(role(found, fore), role(found, back))
        if ratio < minimum:
            failures.append(f"{theme} {client}: {fore} on {back} is {ratio:.2f}:1, needs {minimum}")
    return failures


def wash_failures(theme: str, client: str) -> list[str]:
    found = values(theme, client)
    failures = []
    for fore, minimum in WASH_PAIRS:
        for ground in WASH_GROUNDS:
            ratio = contrast(role(found, fore), over(role(found, WASH), role(found, ground)))
            if ratio < minimum:
                failures.append(f"{theme} {client}: {fore} on the wash over {ground} is {ratio:.2f}:1, needs {minimum}")
    return failures


# The chart's four lines cross, so every pair of them must stay apart, for people with red-green colour blindness too,
# and none may pass for the limit's amber. Simulation: Machado, Oliveira and Fernandes
# (2009), severity 1, applied to linear RGB; distance: OKLab, times 100. Thresholds from the dataviz method's validator:
# 8 is its target and 6 its floor when the series also differ by something besides colour, which ours do (each line has
# its own dash and the client's name at its end or in the legend); 15 is its floor for normal vision.
SERIES = ["color.data.series-1", "color.data.series-2", "color.data.series-3", "color.data.series-4"]
CVD_FLOOR, NORMAL_FLOOR = 6.0, 15.0
MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
}


def _linear(colour: str) -> list[float]:
    return [c / 255 / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in _channels(colour)]


def _oklab(rgb: list[float]) -> tuple[float, float, float]:
    r, g, b = rgb
    # l, m and s are OKLab's own names for the three cone responses
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)  # noqa: E741
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _seen(colour: str, kind: str | None) -> list[float]:
    rgb = _linear(colour)
    if kind is None:
        return rgb
    return [min(1.0, max(0.0, sum(weight * c for weight, c in zip(row, rgb)))) for row in MACHADO[kind]]


def distance(first: str, second: str, kind: str | None = None) -> float:
    """How far apart two colours look, in OKLab times 100: with normal vision, or as a protanope or deuteranope sees."""
    a, b = _oklab(_seen(first, kind)), _oklab(_seen(second, kind))
    return 100 * sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def series_failures(theme: str, client: str) -> list[str]:
    found = values(theme, client)
    named = {name: role(found, name) for name in SERIES}
    pairs = [(SERIES[i], SERIES[j]) for i in range(4) for j in range(i + 1, 4)]
    pairs += [(name, "color.status.warn") for name in SERIES]
    named["color.status.warn"] = role(found, "color.status.warn")
    failures = []
    for one, other in pairs:
        normal = distance(named[one], named[other])
        blind = min(distance(named[one], named[other], kind) for kind in MACHADO)
        if normal < NORMAL_FLOOR or blind < CVD_FLOOR:
            failures.append(f"{theme} {client}: {one} and {other} are {normal:.1f} apart, {blind:.1f} with red-green colour "
                            f"blindness; need {NORMAL_FLOOR} and {CVD_FLOOR}")
    return failures
