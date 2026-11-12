"""The agent loop: the model searches, then writes a draft; code checks the draft before a person sees it."""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from support_agent.agent.guards import check_draft, names_a_customer
from support_agent.agent.prompts import SYSTEM_PROMPT, ticket_message
from support_agent.agent.style import tidy_answer
from support_agent.agent.tools import ToolBox
from support_agent.config import get_settings
from support_agent.models import Draft, DraftAnswer, Ticket, ToolResult

logger = logging.getLogger(__name__)

LIMIT_NOTE = "Tool-call limit reached. Write the draft now with what you have."
TIME_NOTE = "Time is up; this check didn't run."
RETRY_NOTE = "That answer wasn't a valid draft. Reply again with only the draft, in the required format."


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, as the model wrote it


@dataclass
class LlmTurn:
    tool_calls: list[ToolCall]
    text: str | None  # the final answer, when the model didn't call a tool
    items: list[dict]  # this turn's output, sent back to the model on the next call


class Llm(Protocol):
    def respond(self, *, instructions: str, messages: list[dict], tools: list[dict], allow_tools: bool) -> LlmTurn: ...


class OpenAiLlm:
    """The real model, through OpenAI's Responses API."""

    def __init__(self, client: OpenAI | None = None, model: str | None = None):
        settings = get_settings()
        self._client = client or OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=30, max_retries=2)
        self._model = model or settings.openai_model
        self._format = {
            "type": "json_schema",
            "name": "draft",
            "schema": DraftAnswer.model_json_schema(),
            "strict": True,  # OpenAI makes the final answer follow the draft format exactly
        }

    def respond(self, *, instructions, messages, tools, allow_tools):
        response = self._client.responses.create(
            model=self._model,
            instructions=instructions,
            input=messages,
            tools=tools,
            tool_choice="auto" if allow_tools else "none",
            text={"format": self._format},
            store=False,  # OpenAI doesn't store this response for later retrieval (no stored conversation state)
            include=["reasoning.encrypted_content"],  # lets a reasoning model continue its thinking without store
        )
        calls = [
            ToolCall(id=item.call_id, name=item.name, arguments=item.arguments)
            for item in response.output
            if item.type == "function_call"
        ]
        items = [item.model_dump(exclude_none=True) for item in response.output]
        return LlmTurn(tool_calls=calls, text=response.output_text or None, items=items)


NOT_DIAGNOSED = "Not diagnosed by the agent."  # marks a fallback; the test set scores it as "no draft"


def fallback_draft(reason: str, toolbox: ToolBox | None = None) -> Draft:
    """A draft that hands the ticket to a person, used whenever the agent can't produce a usable one. Any GHL checks
    that failed on the way are kept: the person taking over needs to know what wasn't looked at. Each record is read
    once: at the time limit, late work may still be replacing them."""
    failed, ran = (toolbox.ghl_failed, toolbox.ran) if toolbox else ({}, ())
    return Draft(
        likely_cause=NOT_DIAGNOSED,
        confidence="low",
        evidence=_ghl_failure_lines(failed),
        ghl_checks_failed=list(failed),
        checks_ran=list(ran),
        fix_steps=[],
        client_reply="",
        sources=[],
        needs_person=True,
        needs_person_reason=reason,
    )


def draft_within(ticket: Ticket, toolbox: ToolBox, work: Callable[[], Draft], *, seconds: float) -> Draft:
    """A hard time limit around one ticket's work. run_agent's own budget is only checked before each step, so a model
    request already under way can run past it (one took 87 s, while n8n waits 90 s). At the limit the caller
    gets the hand-over draft at once; the late work finishes on its own thread, closes its connections and is
    dropped; an error it ends with is logged, since nobody is left to receive it."""
    done = threading.Event()
    claim = threading.Lock()  # who owns the outcome: the waiting caller, or (once it has gone) the late work
    outcome: dict = {}
    abandoned = False

    def target() -> None:
        try:
            outcome["draft"] = work()
        except BaseException as error:  # noqa: BLE001  handed to the caller below, or logged if it has gone
            outcome["error"] = error
        with claim:
            if abandoned and "error" in outcome:
                error = outcome["error"]
                logger.error("ticket %s: late work failed after the time limit", ticket.ticket_id,
                             exc_info=(type(error), error, error.__traceback__))
            done.set()

    threading.Thread(target=target, name=f"draft-{ticket.ticket_id}", daemon=True).start()
    if not done.wait(seconds):
        with claim:
            abandoned = not done.is_set()  # it may have finished just now: then its outcome is used as usual
    if abandoned:
        logger.warning("ticket %s: hard time limit (%.0f s) reached, handed to a person", ticket.ticket_id, seconds)
        return fallback_draft(f"The agent ran out of time ({seconds:.0f} s) before finishing the draft.", toolbox)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["draft"]


def run_agent(
    ticket: Ticket,
    toolbox: ToolBox,
    llm: Llm,
    *,
    cap: int | None = None,
    time_budget: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Draft:
    if not ticket.text.strip():
        return fallback_draft("The ticket has no text (maybe only a screenshot): a person needs to read it.")
    if cap is None or time_budget is None:  # tests pass both; the running agent takes them from settings
        settings = get_settings()
        cap = settings.tool_call_cap if cap is None else cap
        time_budget = settings.agent_time_budget if time_budget is None else time_budget
    try:
        return _draft(ticket, toolbox, llm, cap, time_budget, clock)
    except Exception:  # e.g. OpenAI down: the caller answers 500 or 429; the lookups would be lost with the toolbox
        logger.error("ticket %s: agent stopped by an error, lookups so far: %s", ticket.ticket_id,
                     ", ".join(f"{r.name} {r.result}" for r in toolbox.ran) or "none")
        raise


def _draft(ticket: Ticket, toolbox: ToolBox, llm: Llm, cap: int, time_budget: float,
           clock: Callable[[], float]) -> Draft:
    started = clock()
    messages: list[dict] = [{"role": "user", "content": ticket_message(ticket)}]
    bad_answers = 0
    for _ in range(cap + 3):  # every turn calls tools, answers or fails, so this always ends
        if clock() - started > time_budget:
            reason = f"The agent ran out of time ({time_budget:.0f} s) before finishing the draft."
            return fallback_draft(reason, toolbox)
        turn = llm.respond(
            instructions=SYSTEM_PROMPT, messages=messages, tools=toolbox.definitions(), allow_tools=toolbox.calls < cap
        )
        messages += turn.items

        if turn.tool_calls:
            for call in turn.tool_calls:  # every call gets an answer, or OpenAI rejects the next request
                if clock() - started > time_budget:  # checked per call: a few slow GHL checks can't outrun n8n
                    result = ToolResult(status="error", note=TIME_NOTE)
                elif toolbox.calls < cap:
                    result = toolbox.call(call.name, _arguments(call.arguments))
                else:
                    result = ToolResult(status="error", note=LIMIT_NOTE)
                messages.append({"type": "function_call_output", "call_id": call.id, "output": result.model_dump_json()})
            continue

        try:
            answer = DraftAnswer.model_validate_json(turn.text or "")
        except ValidationError:
            bad_answers += 1
            if bad_answers == 2:
                return fallback_draft("The model's draft couldn't be read, twice.", toolbox)
            messages.append({"role": "user", "content": RETRY_NOTE})
            continue
        logger.info("ticket %s drafted after %d tool calls", ticket.ticket_id, toolbox.calls)
        return _checked(answer, ticket, toolbox)

    return fallback_draft("The agent didn't finish a draft within its step limit.", toolbox)


def _ghl_failure_lines(failed: dict[str, str]) -> list[str]:
    return [f"Couldn't check in GHL: {name} ({note})" for name, note in failed.items()]


def _arguments(text: str) -> dict:
    try:
        args = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return args if isinstance(args, dict) else {}


def _checked(answer: DraftAnswer, ticket: Ticket, toolbox: ToolBox) -> Draft:
    """Turn the model's answer into a Draft. Any broken rule or missing source sends it to a person."""
    answer = tidy_answer(answer)  # house style first, so the checks and the person see the final text
    problems = check_draft(answer, ticket.other_client_names, known_urls=toolbox.seen_urls,
                           personal_details=toolbox.seen_personal)
    reasons = []
    if answer.needs_person:
        reasons.append(answer.needs_person_reason or "The agent asked for a person.")
    if answer.access_change_requested:
        reasons.append("Access change needs a verified approver (Bluejay policy).")
    if not answer.sources:
        reasons.append("Sources: no guide or help article matched. A person should answer; log the gap.")
    if problems:
        reasons.append("The draft broke a rule: " + ", ".join(problems) + ".")
    fields = answer.model_dump(exclude={"needs_person", "needs_person_reason", "access_change_requested"})
    # GHL checks that failed are listed in the evidence too, which the Slack card already shows.
    fields["evidence"] += _ghl_failure_lines(toolbox.ghl_failed)
    return Draft(
        **fields,
        needs_person=bool(reasons),
        needs_person_reason=" ".join(reasons) or None,
        checks_failed=problems,
        ghl_checks_failed=list(toolbox.ghl_failed),
        keep_out_of_past_tickets=names_a_customer(answer, toolbox.seen_personal, toolbox.seen_name_parts),
        checks_ran=list(toolbox.ran),
    )