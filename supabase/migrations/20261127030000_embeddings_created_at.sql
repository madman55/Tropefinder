-- Follow the project convention: every table has created_at (the pipeline pages through
-- rows in created_at order, so paging stays stable).
alter table public.work_embeddings  add column if not exists created_at timestamptz not null default now();
alter table public.trope_embeddings add column if not exists created_at timestamptz not null default now();
notify pgrst, 'reload schema';
