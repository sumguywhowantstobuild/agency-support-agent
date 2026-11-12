-- 013: the approval count. approval_report() says what staff did with the agent's drafts in a period, per client
-- and for all clients, from the live drafts and the kept archive. A new status, test_closed, keeps test traffic
-- out of every count: it is a status, not a note in decided_by, because the 90-day clean-up keeps the status and
-- drops who decided.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 013_approval_report.sql

insert into schema_migrations (version) values ('013');  -- a second run fails here instead of half-applying

alter table drafts drop constraint drafts_status_check;
alter table drafts add constraint drafts_status_check
  check (status in ('pending', 'approved', 'edited', 'taken_over', 'agent_unavailable', 'replaced', 'limit_reached',
                    'test_closed'));
-- Closing a draft as test traffic is a decision too: who and when are required.
alter table drafts drop constraint drafts_check;
alter table drafts add constraint drafts_check
  check (status not in ('approved', 'edited', 'taken_over', 'test_closed')
         or (decided_by is not null and decided_at is not null));

-- A flood test's leftover drafts (many test messages at once) were closed as taken over with this note.
update drafts set status = 'test_closed' where status = 'taken_over' and decided_by = 'flood-test-cleanup';

comment on table drafts is
  'The agent''s drafts for each ticket and what the support team did with them (approved, edited, taken over, or '
  'closed as test traffic), by whom and when. Deleted with its ticket.';

-- A staff decision counts only where there was a draft to judge (confidence is set): a busy or limit card someone
-- took over is counted under no_draft. Shares and typical times leave out test traffic and cards with no draft.
create function approval_report(p_from timestamptz, p_to timestamptz default now())
returns table (
  client text,
  decided integer,                   -- approved + edited + taken over
  approved integer,                  -- sent as written
  edited integer,
  taken_over integer,
  approved_pct integer,              -- approved as a share of decided; blank when nothing was decided
  waiting integer,                   -- no decision yet
  replaced integer,                  -- a newer draft for the same ticket took its place
  no_draft integer,                  -- agent busy or the client's limit reached, whatever was clicked after
  test_closed integer,               -- closed as test traffic: never counted above
  typical_draft_seconds integer,     -- median, client's message to draft
  typical_decision_minutes numeric   -- median, draft to staff decision
)
language sql
stable
set search_path = public, pg_temp
as $$
  with period as (
    select client_key, status, confidence is not null as has_draft, seconds_to_draft, seconds_to_decision
    from draft_stats
    where drafted_at >= p_from and drafted_at < p_to
    union all  -- the clean-up moves a draft's row to the archive and deletes the ticket together: never in both
    select client_key, status, confidence is not null, seconds_to_draft, seconds_to_decision
    from ticket_stats
    where drafted_at >= p_from and drafted_at < p_to
  ),
  sorted as (
    select client_key,
           case when status = 'test_closed' then 'test_closed'
                when not has_draft then 'no_draft'
                else status end as outcome,
           seconds_to_draft, seconds_to_decision
    from period
  )
  select coalesce(client_key, 'all clients'),
         count(*) filter (where outcome in ('approved', 'edited', 'taken_over'))::integer,
         count(*) filter (where outcome = 'approved')::integer,
         count(*) filter (where outcome = 'edited')::integer,
         count(*) filter (where outcome = 'taken_over')::integer,
         round(100.0 * count(*) filter (where outcome = 'approved')
               / nullif(count(*) filter (where outcome in ('approved', 'edited', 'taken_over')), 0))::integer,
         count(*) filter (where outcome = 'pending')::integer,
         count(*) filter (where outcome = 'replaced')::integer,
         count(*) filter (where outcome = 'no_draft')::integer,
         count(*) filter (where outcome = 'test_closed')::integer,
         (percentile_cont(0.5) within group (order by seconds_to_draft)
            filter (where outcome in ('approved', 'edited', 'taken_over', 'pending', 'replaced')))::integer,
         round((percentile_cont(0.5) within group (order by seconds_to_decision)
            filter (where outcome in ('approved', 'edited', 'taken_over')) / 60)::numeric, 1)
  from sorted
  group by grouping sets ((client_key), ())
  order by client_key is null, client_key
$$;

-- Postgres lets every login run new functions by default; close that. Only the admin login reads these numbers.
revoke execute on function approval_report(timestamptz, timestamptz) from public;
comment on function approval_report(timestamptz, timestamptz) is
  'What staff did with the agent''s drafts from p_from to p_to, per client and for all clients. Numbers only.';
