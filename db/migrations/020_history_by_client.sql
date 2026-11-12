-- 020: admin_history names the client by its key (two clients can share a name, so a client sheet filters by key)
-- and keeps a pause's reason and note apart (Undo after Resume pauses again as it was). Three columns at the end;
-- the six before are unchanged, so everything reading them keeps working, and the view keeps its grants.
-- Run once, as one transaction:  psql -v ON_ERROR_STOP=1 -1 -f 020_history_by_client.sql

insert into schema_migrations (version) values ('020');  -- a second run fails here instead of half-applying

create or replace view admin_history as
  select c.changed_at, s.name as staff_name, 'client'::text as kind,
         coalesce(m.display_name, c.client_key) as subject, c.action, concat_ws(': ', c.reason, c.note) as detail,
         c.client_key, c.reason, c.note
  from client_changes c
  join staff s on s.id = c.changed_by
  left join client_map m on m.client_key = c.client_key
  union all
  select l.changed_at, coalesce(s.name, l.changed_by), 'limit', coalesce(m.display_name, l.client_key),
         'limit_changed', l.old_limit || ' to ' || l.new_limit, l.client_key, null, null
  from limit_changes l
  left join staff s on s.slack_user_id = l.changed_by
  left join client_map m on m.client_key = l.client_key
  union all
  select sc.changed_at, s.name, 'staff', t.name, sc.action, null, null, null, null
  from staff_changes sc
  join staff s on s.id = sc.changed_by
  join staff t on t.id = sc.staff_id
  order by 1 desc;
comment on view admin_history is 'Every change made in the admin app or with the Slack limit button, newest first. '
  'client_key is null for a staff change; reason and note are a pause''s.';
