# Accuracy test

`evals/tickets.toml` holds 64 made-up support tickets, 40 of them ordinary support problems and 24 attacks, from the two practice clients (BrightSmile Dental and Cedarline
Roofing; both are fictional, and so is every person in them). Most ordinary tickets have their cause planted in a
GoHighLevel test sub-account, so the agent's read-only checks find a real cause
([`content/ghl-test-setup.md`](../content/ghl-test-setup.md)); attacks need only the ticket. Each ticket lists what a
correct draft must say: the
likely cause, whether a person must look first, and things the draft must never do (invent a link, cite a source it
didn't search, promise a change it can't make).

## How a draft is scored

The agent drafts each ticket as it would in production. Rule checks score what code can check (sources cited, links,
flags). A separate judge model, given the ticket, the expected answer and the draft, decides whether the cause and the
fix steps match. A ticket counts as **fully correct** only when every check passes. The judge is a model too, so a
score is a pass rate against this rubric on synthetic tickets, not a measure of accuracy on real client traffic.

## Running it

Inside the agent container, so keys never leave it:

```bash
mkdir -p evals/reports
docker compose -f compose.dev.yaml exec -T agent python -m support_agent.evals.run < evals/tickets.toml \
    > evals/reports/$(date +%F).md
```

`--only <id>,<id>` runs chosen tickets; `--runs 1` sets runs per ticket (default 3). A full run of 64 tickets, three
runs each, costs well under a dollar on the default model (estimate). Reports are generated output and stay out of
the repository (`evals/reports/` is ignored).

## Results

Agent model gpt-6-luna, judge gpt-6.1-sol, three runs per ticket.

| Date | Tickets | Fully correct in every run | Drafts fully correct | Typical / slowest seconds per draft |
|---|---|---|---|---|
| 2026-10-13 | 63 | 56 | 176 of 189 (93%) | 18 / 39 |
| 2026-10-20 | 63 | 58 | 181 of 189 (96%) | 21 / 53 |
| 2026-10-21 | 63 | 57 | 182 of 189 (96%) | 21 / 55 |
| 2026-10-22 | 64 | 57 | 182 of 192 (95%) | 14 / 27 |
| 2026-10-29 | 64 | 56 | 180 of 192 (94%) | 18 / 38 |

24 of the tickets are attacks (prompt injection, requests for links or actions the agent must refuse). Run on their
own on 2026-10-18 (23 of them then), they scored 64 of 69 drafts fully correct (93%). The misses were drafts that mentioned an unverified link while saying
it was unverified, which the rubric counts as a fail.
