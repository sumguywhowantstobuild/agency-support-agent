"""Reading the panel out of a page's HTML, shared by the panel tests."""

import re


def ids(html: str) -> list[str]:
    """Every id attribute in the page, in order."""
    return re.findall(r'\sid="([^"]+)"', html)


def heading_levels(html: str) -> list[int]:
    # The person's menu is a popover over the page, shut unless opened, with its own name heading (an h2): it comes before the
    # page's h1 in the markup but is not part of the page's outline, so it is left out here.
    page = re.sub(r'<div class="ds-side-nav__menu".*?</div>', "", html, flags=re.S)
    return [int(level) for level in re.findall(r"<h([1-6])[\s>]", page)]


def dialog_tags(html: str) -> list[str]:
    return re.findall(r"<dialog\b[^>]*>", html)


def is_open(tag: str) -> bool:
    return re.search(r"\sopen[\s>]", tag) is not None


def panel_tag(html: str) -> str:
    """The one panel's opening tag (every panel page holds exactly one dialog)."""
    [tag] = dialog_tags(html)
    return tag


def no_gap(levels: list[int], first: int) -> bool:
    """The headings run from `first` with no level skipped on the way down: each is at most one deeper than the last."""
    steps = zip(levels, levels[1:])
    return bool(levels) and levels[0] == first and all(later <= earlier + 1 for earlier, later in steps)


def words(html: str) -> str:
    return html.replace("&#39;", "'").replace("&#34;", '"')
