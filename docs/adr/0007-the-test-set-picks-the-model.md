# 0007: The accuracy test set picks the model

Status: accepted

## Context

Model prices differ by more than ten times between tiers, and the chat model is nearly all of the running cost. A
cheaper model is only worth it if its drafts are still right and it still obeys the rules under attack.

## Options

- **The strongest model, always.** Simplest; pays for quality the tickets may not need.
- **The cheapest model, always.** Lowest cost; quality unknown.
- **Measure.** A fixed set of tickets with planted causes and attacks, scored the same way on each candidate.

## Decision

The model is one setting. The [test set](../../evals/README.md) (ordinary tickets plus prompt-injection and other
attacks) is scored by rule checks plus a separate judge model, and the cheapest model that meets every pass mark is
used: no rule broken in a reply that reaches a client, at least 90% of drafts fully correct, at least 85% of tickets
right in every run, and time and cost per draft under their limits.

## Consequences

- A model or prompt change is followed by a test-set run before it goes live.
- The rule checks the test set uses are the same code the live agent runs, so they can't drift apart.
- The judge model is separate from the drafting model and never used live.
