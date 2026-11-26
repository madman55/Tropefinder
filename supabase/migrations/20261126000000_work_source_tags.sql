-- TropeFinder: tags that outside sources attach to a work (Hardcover genres,
-- moods, reader tags, content warnings). These are raw evidence; the trope
-- engine maps them to tropes later through trope_aliases.

create table if not exists public.work_source_tags (
  id         uuid primary key default gen_random_uuid(),
  work_id    uuid not null references public.works (id) on delete cascade,
  source_id  uuid not null references public.data_sources (id),
  tag_type   text not null check (tag_type in ('genre', 'mood', 'tag', 'content_warning')),
  tag        text not null,
  position   integer,   -- order the source listed it in (earlier = more votes)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (work_id, source_id, tag_type, tag)
);

create index if not exists work_source_tags_lookup_idx on public.work_source_tags (tag_type, lower(tag));

drop trigger if exists set_updated_at on public.work_source_tags;
create trigger set_updated_at before update on public.work_source_tags
  for each row execute function public.set_updated_at();

alter table public.work_source_tags enable row level security;
drop policy if exists "Public read access" on public.work_source_tags;
create policy "Public read access" on public.work_source_tags for select to anon, authenticated using (true);
revoke all on public.work_source_tags from anon, authenticated;
grant select on public.work_source_tags to anon, authenticated;
grant all on public.work_source_tags to service_role;

-- Hardcover's terms, checked on its own API docs page 2026-11-26.
update public.data_sources set
  licence = 'Hardcover API terms: aggregate book data allowed if identified as Hardcover data; no user-owned data in commercial products; no use to train public/commercial LLMs; displayed images need a DMCA policy',
  terms_url = 'https://docs.hardcover.app/api/getting-started/',
  rate_limit_note = 'Free: 60 requests/minute, 5,000/day, burst 10. Search queries time out at 2 s.',
  terms_checked_on = '2026-11-26',
  notes = 'Used for book-level genres, moods, tags, content warnings, series, format flags and missing descriptions. Cover images not used.'
where slug = 'hardcover';
