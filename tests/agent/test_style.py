from support_agent.agent.style import no_dashes, tidy_answer
from support_agent.models import DraftAnswer, Source


def test_em_dash_becomes_a_comma():
    assert no_dashes("one example call—please don't post it") == "one example call, please don't post it"


def test_spaced_dashes_become_a_comma():
    assert no_dashes("We checked it — it looks fine – mostly") == "We checked it, it looks fine, mostly"


def test_ranges_keep_a_hyphen():
    assert no_dashes("Open Mon–Fri, 9–5") == "Open Mon-Fri, 9-5"


def test_no_double_or_stray_commas():
    assert no_dashes("— Hello, — there —.") == "Hello, there."


def test_text_without_dashes_is_unchanged():
    text = "Settings → Phone System → Voice. Don't change the A2P 10DLC brand (re-check it)."
    assert no_dashes(text) == text


def test_every_draft_field_is_tidied_but_source_titles_are_kept():
    answer = DraftAnswer(
        likely_cause="a—b", confidence="high", evidence=["c—d"], fix_steps=["e—f"], client_reply="g—h",
        sources=[Source(title="T—U", url="https://example.com")], needs_person=True, needs_person_reason="i—j",
        access_change_requested=False, suspicious_sources=[],
    )
    tidy = tidy_answer(answer)
    assert (tidy.likely_cause, tidy.evidence, tidy.fix_steps, tidy.client_reply, tidy.needs_person_reason) == (
        "a, b", ["c, d"], ["e, f"], "g, h", "i, j")
    assert tidy.sources[0].title == "T—U"  # titles must match the knowledge base exactly


def test_no_reason_stays_none():
    answer = DraftAnswer(
        likely_cause="x", confidence="low", evidence=[], fix_steps=[], client_reply="y", sources=[],
        needs_person=False, needs_person_reason=None, access_change_requested=False, suspicious_sources=[],
    )
    assert tidy_answer(answer).needs_person_reason is None