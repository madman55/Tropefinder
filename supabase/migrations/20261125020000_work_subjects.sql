-- TropeFinder: keep a work's subject headings from its source.
-- Open Library subjects ("Time travel", "Superheroes", "LitRPG"...) are real
-- evidence the trope engine can quote later, so we store them as given.

alter table public.works
  add column if not exists subjects text[] not null default '{}',
  add column if not exists subjects_source_id uuid references public.data_sources (id);
