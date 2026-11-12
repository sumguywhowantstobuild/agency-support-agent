"""The rules the app's style files keep (moved from the design system; a new look is an edit, so these guard
it): each colour has one home, contrast in both themes, named values only, and accessibility rules a screen scan can't see."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from look_contrast import (CLIENTS, DS, THEMES, contrast, over, pair_failures, role, series_failures, values,
                           wash_failures)

ROOT = Path(__file__).resolve().parents[2]
DS_CSS = DS / "ds.css"
BUNDLE = DS / "ds-components.css"
BLUEJAY = DS / "clients" / "bluejay.css"
MOTION = DS / "ds-motion.js"

# The CSS Color 4 named colours (case-insensitive, whole words). `transparent` and `currentColor` carry no colour of
# their own and stay allowed.
NAMED_COLOURS = set(
    """aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown
    burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan
    darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
    darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet
    deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
    ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki
    lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow
    lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
    lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine
    mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise
    mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
    orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru
    pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown
    seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan
    teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen""".split()
)
# The CSS system colours: allowed, and only inside a forced-colors block (a person's own contrast theme).
SYSTEM_COLOURS = set(
    """accentcolor accentcolortext activetext buttonborder buttonface buttontext canvas canvastext field fieldtext
    graytext highlight highlighttext linktext mark marktext selecteditem selecteditemtext visitedtext
    activeborder activecaption appworkspace background buttonhighlight buttonshadow captiontext inactiveborder
    inactivecaption inactivecaptiontext infobackground infotext menu menutext scrollbar threeddarkshadow threedface
    threedhighlight threedlightshadow threedshadow window windowframe windowtext""".split()
)
FORCED_COLORS = "@media (forced-colors: active)"
HOVER_MEDIA = "@media (hover: hover) and (pointer: fine)"
LAYER = "@layer ds.components"

COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
VAR_USE = re.compile(r"var\(\s*(--[\w-]+)")
HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\(", re.IGNORECASE)
WORD = re.compile(r"(?<![\w-])([A-Za-z]+)(?![\w-]|\s*\()")
LENGTH_UNITS = (
    "px|rem|em|ex|ch|lh|rlh|cap|ic|rcap|rex|rch|ric|cm|mm|q|in|pt|pc|"
    "vw|vh|vi|vb|vmin|vmax|svw|svh|svi|svb|svmin|svmax|lvw|lvh|lvi|lvb|lvmin|lvmax|"
    "dvw|dvh|dvi|dvb|dvmin|dvmax|cqw|cqh|cqi|cqb|cqmin|cqmax"
)
LENGTH = re.compile(rf"(?<![\w.])([+-]?(?:\d+\.?\d*|\.\d+))({LENGTH_UNITS})\b", re.IGNORECASE)
DURATION = re.compile(r"(?<![\w.])([+-]?(?:\d+\.?\d*|\.\d+))(ms|s)\b", re.IGNORECASE)
BEZIER = re.compile(r"\b(?:cubic-bezier|steps)\s*\(", re.IGNORECASE)
CURVE_WORD = re.compile(r"(?<![\w-])(?:ease(?:-in|-out|-in-out)?|linear|step-start|step-end)(?![\w-])", re.IGNORECASE)
# scaleX is the one named exemption: a bar's fill is scaleX(0 to 1), a level the app writes (data, not motion).
SCALE_FUNCTION = re.compile(r"(?<![\w-])(scale|scaley|scalez|scale3d)\(", re.IGNORECASE)
# A distance a transform moves by is a token, a percentage or 0: not even the 1px or 2px that a border offset may be.
TRANSLATE_FUNCTION = re.compile(r"(?<![\w-])(translate|translatex|translatey|translatez|translate3d)\(", re.IGNORECASE)
BARE_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")
CONDITION_RULE = re.compile(r"@(?:media|container)\b", re.IGNORECASE)
COLOUR_MIX = re.compile(r"(?<![\w-])(color-mix)\(", re.IGNORECASE)
# The CSS math functions. A token times a number, or added to another token, is a size that no token holds.
MATH_FUNCTION = re.compile(
    r"(?<![\w-])(calc|min|max|clamp|round|mod|rem|abs|sign|pow|sqrt|hypot|log|exp|sin|cos|tan|asin|acos|atan|atan2)\(",
    re.IGNORECASE,
)
ARITHMETIC = re.compile(r"\s[+-]\s|\*|/")  # CSS needs spaces around + and -; a sign sits against its number
LAYOUT_OPERAND = re.compile(
    r"(?<![\w.])[+-]?(?:\d+\.?\d*|\.\d+)(?:%|(?:vw|vh|vi|vb|vmin|vmax|[sld]v(?:w|h|i|b|min|max)|ch)\b)",
    re.IGNORECASE,
)
# A colour mix is allowed in one form: a theme token, a private helper or currentColor, at an opacity token's share.
ALLOWED_MIX = re.compile(
    r"color-mix\(in srgb, (?:var\(--ds-color-[\w-]+\)|var\(--_[\w-]+\)|currentColor) "
    r"var\(--ds-opacity-[\w-]+\), transparent\)",
    re.IGNORECASE,
)
# Unitless values that no token holds, named with their reason. Anything else fails until it is added on purpose.
UNITLESS_ALLOWED = {
    # 0 and 1 are the ends of a fade; 0.45 and 0.5 dim a disabled control; 0.7 is the skeleton's rest; 0.8 is the
    # toast's close button at rest.
    "opacity": {"0", "1", "0.45", "0.5", "0.7", "0.8"},
    # The card's whole-card link and a row's stretched link, the sticky bar, a sheet opened without script, the toast.
    "z-index": {"1", "20", "30", "60", "auto"},
}
COMPOUND_BREAK = re.compile(r"\s*[>+~]\s*|\s+(?![^\[]*\])(?![^(]*\))")


def read(path):
    return path.read_text(encoding="utf-8")


def clean(text):
    """The CSS without comments, and with every string and url() emptied (their text is not CSS)."""
    text = STRING.sub('""', COMMENT.sub("", text))
    return re.sub(r"url\([^)]*\)", "url()", text)


def statements_in_context(text):
    """(kind, text, enclosing preludes) per piece of CSS: "prelude" up to a `{`, "declaration" up to a `;` or `}`."""
    pieces, stack, start, depth = [], [], 0, 0
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and character in "{};":
            chunk = text[start:index].strip()
            if character == "{":
                if chunk:
                    pieces.append(("prelude", chunk, tuple(stack)))
                stack.append(chunk)
            else:
                if chunk and not chunk.startswith("@"):
                    pieces.append(("declaration", chunk, tuple(stack)))
                if character == "}" and stack:
                    stack.pop()
            start = index + 1
    return pieces


def top_level_blocks(text):
    """The text of each top-level `@layer` block: one component each, the scope of its private --_ helpers."""
    blocks, depth, start = [], 0, 0
    for index, character in enumerate(text):
        if character == "{":
            if depth == 0:
                start = index + 1
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                blocks.append(text[start:index])
    return blocks


def calls(value, opener):
    """(function name, argument text, whole call) for each outermost call that `opener` (ending at the bracket) finds."""
    found, position = [], 0
    while match := opener.search(value, position):
        depth, end = 1, match.end()
        while end < len(value) and depth:
            depth += {"(": 1, ")": -1}.get(value[end], 0)
            end += 1
        found.append((match.group(1).lower(), value[match.end() : end - 1], value[match.start() : end]))
        position = end
    return found


def arithmetic_kinds(value, in_switch=False):
    """(call, kind) for each outermost math function; the kind names why its arithmetic is kept, None for any other:
    that makes a size no token holds."""
    kinds = []
    for _, body, whole in calls(value, MATH_FUNCTION):
        flat = " ".join(body.split())
        tokens = re.findall(r"var\(--ds-[\w-]+\)", flat)
        rest = re.sub(r"var\([^)]*\)", "T", flat)
        if not ARITHMETIC.search(rest):
            kind = "no arithmetic"
        elif LAYOUT_OPERAND.search(flat):
            kind = "layout math"
        elif len(tokens) == 1 and re.fullmatch(r"T \* -1|-1 \* T", rest):
            kind = "negation"
        elif in_switch and re.search(r"var\(--_(?:thumb|inset|travel)\)", flat):
            kind = "switch geometry"
        elif len(tokens) == 1 and re.fullmatch(r"T [+-] [12]px|-?[12]px [+-] T", rest):
            kind = "border offset"
        else:
            kind = None
        kinds.append((whole, kind))
    return kinds


def value_problems(value, forced=False, in_switch=False, prop=""):
    """What is wrong with one declaration value; empty when it only uses named values. The system colours are the one
    exception, inside a forced-colors block: there the person's own contrast theme decides the colours."""
    problems = []
    for whole, kind in arithmetic_kinds(value, in_switch):
        if kind is None:
            problems.append(f"arithmetic that makes a size no token holds: {whole}")
    for _, _, whole in calls(value, COLOUR_MIX):
        if not ALLOWED_MIX.fullmatch(" ".join(whole.split())):
            problems.append("a colour mix other than a token, helper or currentColor at an opacity token's share")
    plain = VAR_USE.sub("var(", value)  # the names inside var() are not values
    if HEX_COLOUR.search(plain):
        problems.append("a hex colour")
    if COLOUR_FUNCTION.search(plain):
        problems.append("a colour function")
    words = {w.lower() for w in WORD.findall(plain)}
    named = sorted(words & NAMED_COLOURS)
    if named:
        problems.append(f"the named colour {', '.join(named)}")
    system = sorted(words & SYSTEM_COLOURS)
    if system and not forced:
        problems.append(f"the system colour {', '.join(system)} outside {FORCED_COLORS}")
    for number, unit in LENGTH.findall(plain):
        if not (float(number) == 0 or (unit.lower() == "px" and abs(float(number)) in (1.0, 2.0))):
            problems.append(f"the raw length {number}{unit}")
    for number, unit in DURATION.findall(plain):
        if float(number) != 0:
            problems.append(f"the raw duration {number}{unit}")
    if BEZIER.search(plain) or CURVE_WORD.search(plain):
        problems.append("a raw easing curve")
    for _, arguments, _ in calls(plain, SCALE_FUNCTION):
        if any(BARE_NUMBER.fullmatch(argument.strip()) for argument in arguments.split(",")):
            problems.append("a raw scale() number")
    moved = [arguments for _, arguments, _ in calls(plain, TRANSLATE_FUNCTION)]
    moved += [plain] if prop.lower() == "translate" else []
    if any(float(number) != 0 for arguments in moved for number, _ in LENGTH.findall(arguments)):
        problems.append("a raw distance in a transform")
    return problems


def forced_stack(stack):
    return FORCED_COLORS in [" ".join(s.lower().split()) for s in stack]


def rule_declarations(selector_part, outside=FORCED_COLORS):
    """{property: value} of the rules whose selector contains `selector_part`, not inside the `outside` media. Quoted
    values stay as written, so the wrong state in a selector doesn't read as a match."""
    found, current = {}, None
    for kind, chunk, stack in statements_in_context(COMMENT.sub("", read(BUNDLE))):
        if kind == "prelude":
            current = selector_part in chunk and outside not in [" ".join(s.lower().split()) for s in stack]
        elif current:
            prop, _, value = chunk.partition(":")
            found[prop.strip()] = " ".join(value.split())
    return found


def test_each_colour_has_one_home():
    # A second copy that outranks the first means an edit changes nothing for some people, and contrast is checked on
    # the wrong one. The design has no "follow the computer's setting", so those blocks have no job.
    ds, layer = read(DS_CSS), read(BLUEJAY)
    assert "prefers-color-scheme" not in ds and "prefers-color-scheme" not in layer
    names = re.compile(r"(--[\w-]+)\s*:")
    def block(selector):
        match = re.search(rf"(?:^|\n){re.escape(selector)} \{{(.*?)\n\}}", ds, re.DOTALL)
        assert match, f"ds.css has no {selector} block"
        return set(names.findall(match.group(1)))

    assert block(":root") & block('[data-mode="productive"]') == set()


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("theme", THEMES)
def test_every_colour_pair_reaches_its_ratio(theme, client):
    assert pair_failures(theme, client) == []


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("theme", THEMES)
def test_every_pair_of_series_colours_stays_apart_for_colour_blindness(theme, client):
    """The four chart lines, and each line against the limit's amber (look_contrast.py has
    the method, thresholds and sources)."""
    assert series_failures(theme, client) == []


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("theme", THEMES)
def test_every_colour_on_the_hover_wash_reaches_its_ratio(theme, client):
    assert wash_failures(theme, client) == []


@pytest.mark.parametrize("theme", THEMES)
def test_the_room_beside_an_open_panel_is_the_dimmed_page(theme):
    # The dimmed area can't reach the room kept for the scroll bar, and a browser paints that room with one solid colour.
    found = values(theme)
    assert role(found, "color.overlay.scrim-page") == over(role(found, "color.overlay.scrim"), role(found, "color.bg.canvas"))


@pytest.mark.parametrize("theme", THEMES)
def test_the_toast_close_buttons_hover_wash_keeps_its_icon_at_3_to_1(theme):
    # Hovering the toast's close button paints text.on-inverse at opacity.close-hover over the toast's own ground; the
    # icon, in text.on-inverse, then sits on that composite and needs 3:1 (WCAG 1.4.11).
    hover = rule_declarations(".ds-toast .ds-button.ds-toast__close:hover")
    assert re.fullmatch(
        r"color-mix\(in srgb, var\(--ds-color-text-on-inverse\) var\(--ds-opacity-close-hover\), transparent\)",
        hover.get("background", ""),
    ), "the close button no longer washes with text.on-inverse at opacity.close-hover"
    assert hover.get("color") == "var(--ds-color-text-on-inverse)"
    found = values(theme)
    share = float(role(found, "opacity.close-hover").rstrip("%")) / 100
    text, ground = role(found, "color.text.on-inverse"), role(found, "color.bg.inverse")
    red, green, blue = (int(text[i : i + 2], 16) for i in (1, 3, 5))

    assert contrast(text, over(f"rgba({red}, {green}, {blue}, {share})", ground)) >= 3.0


@pytest.mark.parametrize("theme", THEMES)
def test_every_scrollbar_thumb_is_the_selected_ring_and_reaches_3_to_1_on_each_ground(theme):
    # An author-styled scrollbar needs 3:1 (WCAG 1.4.11). The thumb sits on a ground, or on the 5% wash over one.
    thumbs = set(re.findall(r"scrollbar-color:\s*([^;]+);", clean(read(BUNDLE))))
    assert thumbs == {"var(--ds-color-control-selected-ring) transparent"}
    found = values(theme)
    thumb, wash = role(found, "color.control.selected-ring"), role(found, "color.overlay.hover")
    for under in ("canvas", "raised", "surface"):
        ground = role(found, f"color.bg.{under}")
        assert contrast(thumb, ground) >= 3.0, f"the thumb on bg.{under}"
        assert contrast(thumb, over(wash, ground)) >= 3.0, f"the thumb on the track over bg.{under}"


def painters(colour):
    """The components (by class name) with a rule painting a background, or its --_bg helper, with this custom property."""
    found, selector = set(), ""
    paints = re.compile(rf"(?:background(?:-color)?|--_bg(?:-hover)?):\s*var\({re.escape(colour)}\)")
    for kind, chunk, _ in statements_in_context(clean(read(BUNDLE))):
        if kind == "prelude":
            selector = chunk
        elif paints.fullmatch(" ".join(chunk.split())):
            found.update(re.findall(r"\.ds-([a-z]+(?:-[a-z]+)*)", selector)[:1])
    return found


def test_only_the_components_checked_for_text_on_the_wash_and_on_border_default_paint_them():
    # A new component painting the wash or border.default behind text brings its text roles into the contrast checks
    # (WASH_PAIRS, or PAIRS for border.default): this fails until the lists below, and those, are updated.
    assert painters("--ds-color-overlay-hover") == {"button", "nav-bar", "segmented", "select", "sheet", "side-nav", "table", "text-input"}
    assert painters("--ds-color-border-default") == {"button", "sheet"}


def test_unitless_opacity_and_stacking_values_are_pinned_to_a_named_list():
    # No token holds a plain opacity or z-index, so a new value fails here until it is added on purpose.
    problems = []
    for kind, chunk, _ in statements_in_context(clean(read(BUNDLE))):
        prop, _, value = chunk.partition(":")
        prop, value = prop.strip().lower(), " ".join(value.split())
        if kind == "declaration" and prop in UNITLESS_ALLOWED:
            if not value.startswith("var(") and value not in UNITLESS_ALLOWED[prop]:
                problems.append(f"{prop}: {value}")

    assert not problems, "\n".join(problems)


def test_motion_distances_come_from_the_motion_tokens():
    # New cards and rows rise from, and the chevron nudges by, a --ds-motion- token, not the space scale or a raw 2px.
    css = read(BUNDLE)
    for name in ("card", "table"):
        rise = re.search(rf"@keyframes ds-{name}-rise\s*\{{\s*from\s*\{{([^}}]*)\}}", css)
        assert rise and "transform: translateY(var(--ds-motion-rise-offset));" in rise.group(1), f"the {name} rise"
    assert "transform: translateX(var(--ds-motion-nudge-offset));" in css
    assert "translateX(2px)" not in css and "translateY(var(--ds-space-2))" not in css


def test_components_use_tokens_only():
    # So a colour, size or curve is changed in one place, and Bluejay's layer reaches everything.
    assert len(NAMED_COLOURS) == 148  # the CSS Color 4 list, typed in full
    declared_tokens = read(DS_CSS)
    known = set(re.findall(r"(--ds-[\w-]+)\s*:", declared_tokens))
    breakpoints = set(re.findall(r"--ds-size-breakpoint-[\w-]+:\s*([^;]+);", declared_tokens))
    assert breakpoints, "ds.css has no size.breakpoint token"
    blocks = top_level_blocks(clean(read(BUNDLE)))
    assert blocks, "the bundle holds no block"

    problems = []
    for text in blocks:
        helpers = set(re.findall(r"(--_[\w-]+)\s*:", text))
        for kind, chunk, stack in statements_in_context(text):
            if kind == "prelude":
                if CONDITION_RULE.match(chunk):  # lengths in a condition must equal a breakpoint token
                    for number, unit in LENGTH.findall(chunk):
                        if f"{number}{unit}" not in breakpoints:
                            problems.append(f"{chunk}: {number}{unit} is not a breakpoint value")
                continue
            prop, _, value = chunk.partition(":")
            prop, value = prop.strip(), value.strip()
            if prop.startswith("--") and not prop.startswith("--_"):
                problems.append(f"{prop}: components declare only --_ helpers, never a token")
            in_switch = any("ds-checkbox-toggle" in selector for selector in stack)
            problems += [f"{prop}: {issue}" for issue in value_problems(value, forced_stack(stack), in_switch, prop)]
            for name in VAR_USE.findall(value):
                if name.startswith("--ds-"):
                    if name not in known:
                        problems.append(f"{prop}: {name} does not exist in ds.css")
                elif name not in helpers:
                    problems.append(f"{prop}: {name} is not a --ds- token or a --_ helper of this component")

    assert not problems, "\n".join(problems)


def test_components_sit_in_the_components_layer():
    # A project's own unlayered styles win over a layer without !important; keep it that way.
    text = clean(read(BUNDLE))
    found, depth, start = [], 0, 0
    for index, character in enumerate(text):
        if character == "{":
            if depth == 0 and " ".join(text[start:index].split()) != LAYER:
                found.append(" ".join(text[start:index].split()))
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                start = index + 1
        elif character == ";" and depth == 0:
            found.append(" ".join(text[start:index].split()))
            start = index + 1
    found.append(" ".join(text[start:].split()))

    assert LAYER + " {" in text
    assert [statement for statement in found if statement] == []
    assert "!important" not in read(BUNDLE)


def test_hover_only_where_hover_exists():
    # A tap on a phone must not leave a stuck wash, lift or colour: :hover lives only where a pointer hovers.
    text = clean(read(BUNDLE))
    stack, found, start = [], [], 0
    for index, character in enumerate(text):
        if character == "{":
            prelude = " ".join(text[start:index].split())
            if not prelude.startswith("@") and ":hover" in prelude and HOVER_MEDIA not in [s.lower() for s in stack]:
                found.append(prelude)
            stack.append(prelude)
            start = index + 1
        elif character in "};":
            if character == "}" and stack:
                stack.pop()
            start = index + 1

    assert found == []


def test_controls_drawn_by_background_have_forced_colors_rules():
    # In a Windows contrast theme, solid backgrounds and shadows are repainted or dropped. A checkbox tick, a switch, the
    # pressed segmented item, the status dot and the current menu link are drawn that way. (Strings read as empty.)
    inside = {
        part.strip()  # each selector of each rule in the block, whole: a longer selector does not count
        for kind, chunk, stack in statements_in_context(clean(read(BUNDLE)))
        if kind == "prelude" and forced_stack(stack)
        for part in re.split(r",(?![^()]*\))", " ".join(chunk.split()))
    }
    wanted = {
        ".ds-checkbox-toggle",
        ".ds-checkbox-toggle--switch",
        '.ds-segmented__item[aria-pressed=""]',
        ".ds-status__dot",
        '.ds-side-nav__link[aria-current=""]',
    }

    assert wanted - inside == set()


def test_everything_that_sets_display_has_a_hidden_rule():
    # An author `display` (or `all:`) beats the browser's own [hidden] { display: none } in any layer, so each thing
    # that sets one carries its own hidden rule; a variant (.x--y) is covered by its base class's rule.
    sets, classes, elements, current = {}, set(), set(), None
    for kind, chunk, _ in statements_in_context(clean(read(BUNDLE))):
        if kind == "prelude":
            current = None if chunk.startswith("@") else [s.strip() for s in re.split(r",(?![^()]*\))", chunk) if s.strip()]
            continue
        prop = chunk.split(":", 1)[0].strip()
        if current is None or prop not in ("display", "all"):
            continue
        value = chunk.split(":", 1)[1].strip()
        for selector in current:
            by_class = re.fullmatch(r"\.([\w-]+)\[hidden\]", selector)
            by_tag = re.fullmatch(r"\.([\w-]+)\s+([a-z][\w-]*)\[hidden\]", selector)
            if prop == "display" and value == "none" and by_class:
                classes.add(by_class.group(1))
            elif prop == "display" and value == "none" and by_tag:
                elements.add((by_tag.group(1), by_tag.group(2)))
            elif not (prop == "display" and value == "none"):
                sets[selector] = f"{prop}: {value}"
    problems = []
    for selector, declaration in sets.items():
        last = [part for part in COMPOUND_BREAK.split(selector.strip()) if part][-1]
        if "::" in last:
            continue
        names = re.findall(r"\.([\w-]+)", last)
        if names:
            if not any(c in classes or c.split("--")[0] in classes for c in names):
                problems.append(f"{selector!r} sets {declaration} and has no [hidden] rule")
            continue
        tag = re.match(r"[a-z][\w-]*", last)
        root = re.search(r"\.([\w-]+)", selector)
        if tag and root and (root.group(1), tag.group(0)) not in elements:
            problems.append(f"{selector!r} sets {declaration} on an element and has no .{root.group(1)} {tag.group(0)}[hidden] rule")

    assert not problems, "\n".join(problems)


def test_the_switch_uses_its_own_checked_colours():
    # The switch colours are checked for contrast above; the styles must use exactly those.
    text = clean(read(BUNDLE))
    start = text.index(".ds-checkbox-toggle--switch {")
    switch = text[start : text.find(LAYER, start)]

    for name in ("switch-off", "switch-on", "switch-thumb"):
        assert f"var(--ds-color-control-{name})" in switch, f"the switch does not use control.{name}"
    assert "var(--ds-color-status-ok)" not in switch
    assert "var(--ds-color-bg-surface)" not in switch


def test_the_selected_item_and_the_current_tab_carry_the_selected_ring():
    # A 1px ring in control.selected-ring, so the current choice isn't marked by a fill alone (WCAG 1.4.1).
    ring = "0 0 0 1px var(--ds-color-control-selected-ring)"

    assert rule_declarations('.ds-segmented__item[aria-pressed="true"]').get("box-shadow") == f"{ring}, var(--ds-shadow-card)"
    # The side menu's: on a phone the bar marks it another way (test_clients_browser), and the bar would cut a ring off.
    assert rule_declarations('.ds-side-nav__link[aria-current="page"]', outside="@media (max-width: 720px)").get("box-shadow") == ring


def test_the_tap_flash_is_off_and_what_can_be_tapped_answers_a_press():
    # On a real phone the browser's blue flash lay over a card's name. Without the flash, each thing that can be
    # tapped answers for itself; a button and a card already did. A long press on a touch screen doesn't select the name.
    # All of it only for touch: a mouse still selects the name, and sees no new pressed look.
    css = COMMENT.sub("", read(BUNDLE))
    assert re.search(r"(?m)^\s*html\s*\{[^}]*-webkit-tap-highlight-color:\s*transparent", css)

    def on_touch(selector_part):
        found, current = {}, None
        for kind, chunk, stack in statements_in_context(css):
            if kind == "prelude":
                current = selector_part in chunk and "@media (hover: none)" in [" ".join(s.lower().split()) for s in stack]
            elif current:
                prop, _, value = chunk.partition(":")
                found[prop.strip()] = " ".join(value.split())
        return found

    for tappable in (".ds-side-nav__link:active", ".ds-side-nav__toggle:active",
                     ".ds-side-nav__person:active", ".ds-side-nav__menu-item:active",
                     ".ds-change-line__link:active", ".ds-link:active", ".ds-segmented__item:active",
                     ".ds-table tbody tr:active", ".ds-table__sort:active"):
        assert on_touch(tappable), f"{tappable} has no pressed look on a touch screen"
        assert not rule_declarations(tappable, outside="@media (hover: none)"), f"{tappable} also presses for a mouse"
    name = on_touch(".ds-card__name-button")
    assert name.get("user-select") == "none" and name.get("-webkit-user-select") == "none"
    assert "user-select" not in rule_declarations(".ds-card__name-button", outside="@media (hover: none)")


def test_user_invalid_is_marked_by_more_than_colour_and_like_aria_invalid():
    # :user-invalid comes with no error words unless the app writes them, so colour alone (WCAG 1.4.1) is not enough.
    # The same rule marks [aria-invalid="true"], and it draws an inset ring in the fail colour (a shape, not only colour).
    rules = {}
    for kind, chunk, stack in statements_in_context(clean(read(BUNDLE))):
        if kind == "declaration" and stack and ":user-invalid" in stack[-1]:
            prop, _, value = chunk.partition(":")
            rules.setdefault(" ".join(stack[-1].split()), {})[prop.strip()] = " ".join(value.split())
    assert len(rules) >= 4, "the text field and the select should mark :user-invalid in at least four rules"
    problems = []
    for selectors, declarations in rules.items():
        parts = [part.strip() for part in selectors.split(",")]
        for part in parts:
            if ":user-invalid" in part and part.replace(":user-invalid", '[aria-invalid=""]') not in parts:
                problems.append(f'{part} has no matching [aria-invalid="true"] selector in the same rule')
        ring = declarations.get("box-shadow", "")
        if "inset" not in ring or "var(--ds-color-status-fail)" not in ring:
            problems.append(f"{selectors} marks the field by colour only")

    assert not problems, "\n".join(problems)


def test_no_component_hides_its_scrollbar():
    # A box that scrolls sideways shows that it does: with the bar hidden, a mouse user can't tell more tabs exist.
    problems = []
    for kind, chunk, _ in statements_in_context(clean(read(BUNDLE))):
        squashed = re.sub(r"\s+", "", chunk.lower())
        if kind == "declaration" and squashed == "scrollbar-width:none":
            problems.append(chunk)
        elif kind == "prelude" and "::-webkit-scrollbar" in squashed:
            problems.append(chunk)

    assert not problems, "\n".join(problems)


def test_every_font_file_is_there_with_its_licence_beside_it():
    # The fonts are self-hosted under the SIL Open Font License, which has to travel with them.
    named = re.findall(r'url\("(fonts/[^"]+)"\)', read(DS_CSS))
    assert named, "ds.css names no font file"
    for file in named:
        assert (DS / file).is_file(), f"{file} is missing"
        assert (DS / file).parent.joinpath("OFL.txt").is_file(), f"{file} has no licence beside it"


def test_the_add_on_is_a_plain_script_with_no_network_address():
    text = read(MOTION)

    assert not re.search(r"^\s*(import|export)\b", text, re.MULTILINE)  # no module syntax
    assert not re.search(r"\bimport\s*\(", text)  # nor dynamic imports
    assert "http" not in text  # no URL to fetch from
    assert b"\r" not in MOTION.read_bytes()  # LF everywhere (.gitattributes)


def test_the_motion_scripts_own_tests_pass():
    # In `pytest` so the whole run can't forget them; Node's built-in runner, no packages.
    node = shutil.which("node")
    assert node, "the motion script's tests need Node on the PATH (docs/SETUP.md)"

    result = subprocess.run([node, "--test", "tests/js/motion.test.mjs"], cwd=ROOT, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stdout + result.stderr
