-- Run this once in Supabase SQL Editor.
-- Creates a lightweight telemetry table with no chat content.

create extension if not exists pgcrypto;

create table if not exists public.usage_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  event_name text not null,
  run_id text null,
  meta jsonb null
);

create index if not exists usage_events_created_at_idx
  on public.usage_events (created_at desc);

create index if not exists usage_events_event_name_idx
  on public.usage_events (event_name);

create index if not exists usage_events_run_id_idx
  on public.usage_events (run_id);

-- Optional: keep this telemetry table private by default.
alter table public.usage_events enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'usage_events'
      and policyname = 'deny_all_usage_events'
  ) then
    create policy deny_all_usage_events
      on public.usage_events
      as restrictive
      for all
      to public
      using (false)
      with check (false);
  end if;
end $$;

-- Handy checks:
-- select * from public.usage_events order by created_at desc limit 50;
-- select event_name, count(*) from public.usage_events group by 1 order by 2 desc;
