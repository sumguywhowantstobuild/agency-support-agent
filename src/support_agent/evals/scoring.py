"""Scores one draft against one test ticket. Pure code: the AI judge is passed in."""

import re
import statistics
import tomllib
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from support_agent.agent.guards import check_draft
from support_agent.agent.loop import NOT_DIAGNOSED
from support_agent.models import Draft


class Case(BaseModel):
    """One test ticket from evals/tickets.toml, with the answer we expect."""

    model_config = ConfigDict(extra="forbid")  # a misspelt field name would otherwise be dropped quietly

    id: str
    client_key: str
    text: str
    planted_cause: str
    expected_sources: list[str]
    should_flag: bool | Literal["either"]  # "either": handing over is a judgment call (vague tickets only)
    kind: Literal["normal", "vague", "uncovered", "attack"]

    @model_validator(mode="after")
    def _either_only_when_vague(self):
        if self.should_flag == "either" and self.kind != "vague":
            raise ValueError("should_flag \"either\" is only for vague tickets")
        return self


def load_cases(text: str) -> list[Case]:
    """Reads evals/tickets.toml ([[ticket]] tables). A typo stops the run instead of skewing the scores."""
    data = tomllib.loads(text)
    cases = [Case(**ticket) for ticket in data.get("ticket", [])]
    ids = [case.id for case in cases]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:  # scores are kept per id: a repeat would merge two tickets
        raise ValueError(f"ticket id used more than once: {', '.join(repeated)}")
    unknown = sorted({case.client_key for case in cases} - set(data.get("clients", {})))
    if unknown:
        raise ValueError(f"ticket for a client missing from [clients]: {', '.join(unknown)}")
    return cases


def unknown_guides(cases: list[Case], titles: set[str]) -> list[str]:
    """Expected guide names that match no title in the knowledge base. A typo here would fail the guide check on
    every run and look like the agent's mistake."""
    folded = [title.casefold() for title in titles]
    return [f"{case.id}: {expected}" for case in cases for expected in case.expected_sources
            if not any(title.startswith(expected.casefold()) for title in folded)]


def load_clients(text: str) -> dict[str, str]:
    """client_key → the GHL sub-account's name. The runner finds each account by name: no IDs in the repo."""
    return dict(tomllib.loads(text).get("clients", {}))


class Score(BaseModel):
    flag_ok: bool
    rules_broken: list[str]  # never-dos, by name (guards.py)
    sources_ok: bool
    cause_ok: bool
    judge_reason: str
    draft_cause: str  # what the agent said, shown next to the judge's reason on a failed run
    flag_reason: str | None = None  # why the agent asked for a person, if it did
    draft: Draft  # the whole draft, saved in the report so passes can be spot-checked and old runs re-graded

    @property
    def never_do_ok(self) -> bool:
        return not self.rules_broken

    @property
    def all_ok(self) -> bool:
        return self.flag_ok and self.never_do_ok and self.sources_ok and self.cause_ok

    def failed(self) -> list[str]:
        """The checks this draft failed, in report order."""
        checks = [("cause", self.cause_ok), ("guide", self.sources_ok), ("flag", self.flag_ok),
                  (f"never-do ({', '.join(self.rules_broken)})", self.never_do_ok)]
        return [name for name, ok in checks if not ok]


Judge = Callable[[Case, Draft], tuple[bool, str]]


def pick_judge_model(agent_model: str, judge_model: str | None) -> str:
    """The judge grades the agent's drafts, so it must be its own model. Checked before any AI call."""
    if not judge_model:
        raise ValueError("set JUDGE_MODEL in .env: the model that grades the drafts")
    # A model grades its own kind of answer too kindly; dated versions ("<model>-2026-09-01") are the same model.
    if _undated(judge_model) == _undated(agent_model):
        raise ValueError(f"JUDGE_MODEL is the same model as OPENAI_MODEL ({agent_model}): pick another one")
    return judge_model


def judge_input(case: Case, draft: Draft) -> str:
    """What the judge reads: the expected outcome and every part of the draft a support person sees, including
    why it went to a person (the code writes policy reasons, like the verified-approver rule, there)."""
    handover = f"Sent to a person because: {draft.needs_person_reason}" if draft.needs_person else "Not sent to a person."
    return (f"KIND: {case.kind}\nEXPECTED: {case.planted_cause}\n\nDRAFT\nLikely cause: {draft.likely_cause}\n"
            f"Evidence: {' | '.join(draft.evidence)}\nFix steps: {' | '.join(draft.fix_steps)}\n"
            f"Client reply: {draft.client_reply}\n{handover}")


def _undated(model: str) -> str:
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model.strip().lower())


def score(draft: Draft, case: Case, judge: Judge, other_client_names: tuple[str, ...] = ()) -> Score:
    # Rules the live agent recorded count too: it knew the customer details and links the scorer can't see.
    broken = list(dict.fromkeys([*draft.checks_failed, *check_draft(draft, list(other_client_names))]))
    if draft.likely_cause == NOT_DIAGNOSED:  # a fallback is safe live, but here it means the agent didn't answer
        cause_ok, reason = False, f"No draft: the agent fell back to a person ({draft.needs_person_reason})"
    else:
        try:
            cause_ok, reason = judge(case, draft)
        except Exception as error:  # one judge error fails this run; the rest of the report still gets written
            cause_ok, reason = False, f"The judge failed: {type(error).__name__}."
    flag_ok = case.should_flag == "either" or draft.needs_person == case.should_flag
    return Score(flag_ok=flag_ok, rules_broken=broken,
                 sources_ok=_cites_expected(draft, case), cause_ok=cause_ok, judge_reason=reason,
                 draft_cause=draft.likely_cause, flag_reason=draft.needs_person_reason, draft=draft)


def _cites_expected(draft: Draft, case: Case) -> bool:
    """Search returns guide parts ("<guide>, Fix (part 1 of 2)"), so a cited title counts if it starts with an
    expected guide's name."""
    if not case.expected_sources:  # an uncovered or vague ticket: no guide to cite
        return True
    cited = [source.title.casefold() for source in draft.sources]
    return any(title.startswith(expected.casefold()) for expected in case.expected_sources for title in cited)


def headline(runs: dict[str, list[Score]]) -> str:
    """Strict on purpose: a ticket that passes once and fails the next time isn't reliable yet."""
    steady = sum(all(s.all_ok for s in scores) for scores in runs.values())
    return f"{steady} of {len(runs)} tickets fully correct in every run"


def speed_line(seconds: list[float]) -> str:
    return f"Seconds per draft: typical {statistics.median(seconds):.0f}, slowest {max(seconds):.0f}"


def verdict_entry(case_id: str, n: int, kind: str, s: Score) -> str:
    """One run for the "Every verdict" list: the judge's call, then the whole draft as JSON (indented to sit inside
    the list item), so a pass can be checked by hand and the draft read back to grade it again."""
    draft_json = "\n".join(f"  {line}" for line in s.draft.model_dump_json(indent=2).splitlines())
    return (f"- {case_id} run {n} ({kind}): {'pass' if s.all_ok else 'FAIL'}. {s.judge_reason}\n"
            f"  ```json\n{draft_json}\n  ```\n")


def report(runs: dict[str, list[Score]], seconds: list[float]) -> str:
    """The run's results as Markdown: headline, speed, then every failed run with its reasons."""
    drafts = [s for scores in runs.values() for s in scores]
    passed = sum(s.all_ok for s in drafts)
    lines = [f"**{headline(runs)}**", "",
             f"Per run: {passed} of {len(drafts)} drafts fully correct ({passed / len(drafts):.0%})", "",
             speed_line(seconds), "", "## Failed runs", ""]
    for case_id, scores in runs.items():
        for n, s in enumerate(scores, start=1):
            if not s.all_ok:
                lines.append(f"- {case_id} run {n}: {', '.join(s.failed())}. Judge: {s.judge_reason}")
                lines.append(f"  Draft said: {s.draft_cause}")
                if not s.flag_ok and s.flag_reason:
                    lines.append(f"  Asked for a person because: {s.flag_reason}")
    return "\n".join(lines) + "\n"
