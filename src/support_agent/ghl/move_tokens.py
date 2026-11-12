"""One-time move of the GHL keys from .env into the database, locked. Prints SQL holding only locked
values, for the database admin to run in one transaction; progress goes to stderr as location ids only.

Runs in an agent container on 1.1.0 or later (on an upgrade from 1.0.0, the test copy's: SETUP section 14). No
service gets GHL_TOKENS any more, so the old keys and the public half are given for this run only, by name and read
from .env, so the keys never appear in a command line or the shell's history (GHL_TOKENS must be the bare JSON there):
  ( set -o pipefail; export GHL_TOKENS="$(grep '^GHL_TOKENS=' .env | cut -d= -f2-)" \\
      GHL_KEY_PUBLIC="$(grep '^GHL_KEY_PUBLIC=' .env | cut -d= -f2-)" && \\
    docker compose -f compose.dev.yaml exec -T -e GHL_TOKENS -e GHL_KEY_PUBLIC agent \\
      python -m support_agent.ghl.move_tokens \\
    | docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d knowledge -v ON_ERROR_STOP=1 -1 )
"""

import json
import os
import sys

from support_agent.keys import seal


def move_sql(tokens: dict[str, str], public_key_b64: str) -> str:
    """SQL that stores each location's locked key for its client. Stops (raises) if a location has no client."""
    rows = ",\n  ".join(f"('{location}', '\\x{seal(public_key_b64, token).hex()}'::bytea)"
                        for location, token in sorted(tokens.items()))
    return f"""create temp table moving (location_id text primary key, sealed bytea not null) on commit drop;
insert into moving values
  {rows};
do $$
begin
  if exists (select 1 from moving m left join client_map c on c.location_id = m.location_id
             where c.client_key is null) then
    raise exception 'move_tokens: a location in GHL_TOKENS has no client in client_map';
  end if;
end $$;
insert into client_keys (client_key, sealed)
select c.client_key, m.sealed from moving m join client_map c on c.location_id = m.location_id
on conflict (client_key) do update set sealed = excluded.sealed, set_at = now();
"""


def main() -> None:
    tokens = json.loads(os.environ["GHL_TOKENS"])
    for location in sorted(tokens):
        if not location.replace("_", "").replace("-", "").isalnum():
            sys.exit(f"not a location id: {location!r}")
    print(move_sql(tokens, os.environ["GHL_KEY_PUBLIC"]))
    for location in sorted(tokens):
        print(f"{location}: sealed", file=sys.stderr)


if __name__ == "__main__":
    main()
