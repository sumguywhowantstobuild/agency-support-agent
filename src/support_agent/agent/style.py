"""House style the code enforces, so it holds even when the model slips: no em or en dashes in a draft."""

import re

from support_agent.models import DraftAnswer

EM, EN = "\u2014", "\u2013"
_RANGE = re.compile(rf"(?<=\w){EN}(?=\w)")  # "9–5", "Mon–Fri": a range, so a plain hyphen
_DASH = re.compile(rf"\s*[{EM}{EN}]+\s*")  # any other dash is a pause in the sentence: a comma


def no_dashes(text: str) -> str:
    """The text with em and en dashes replaced: hyphens in ranges, commas everywhere else."""
    text = _DASH.sub(", ", _RANGE.sub("-", text))
    text = re.sub(r",\s*,", ",", text)  # "word, — next" would leave two commas
    text = re.sub(r",\s*([.;:!?)])", r"\1", text)  # no comma right before other punctuation
    return text.strip().strip(",").strip()


def tidy_answer(answer: DraftAnswer) -> DraftAnswer:
    """Every text field without dashes. Source titles stay exactly as the knowledge base has them."""
    return answer.model_copy(
        update={
            "likely_cause": no_dashes(answer.likely_cause),
            "evidence": [no_dashes(x) for x in answer.evidence],
            "fix_steps": [no_dashes(x) for x in answer.fix_steps],
            "client_reply": no_dashes(answer.client_reply),
            "needs_person_reason": no_dashes(answer.needs_person_reason) if answer.needs_person_reason else None,
        }
    )