-- TropeFinder: meaning search (Phase 2).
--
-- Each book and each trope gets an "embedding": a list of 384 numbers produced by the
-- gte-small model (run inside a Supabase Edge Function), placed so that texts with similar
-- meaning end up close together. A typed search is embedded the same way, then compared.
--
-- Meaning matches are SUGGESTIONS. They never count as a confirmed trope: search_books
-- still decides confirmed / possible from stored, evidence-backed verdicts only.
--
-- All embeddings must come from the same model (column "model"), or distances mean nothing.

create extension if not exists vector with schema extensions;

create table public.work_embeddings (
  work_id    uuid primary key references public.works (id) on delete cascade,
  embedding  extensions.vector(384) not null,
  text_hash  text not null,              -- hash of the text embedded; re-embed when it changes
  model      text not null default 'gte-small',
  updated_at timestamptz not null default now()
);

create table public.trope_embeddings (
  trope_id   uuid primary key references public.tropes (id) on delete cascade,
  embedding  extensions.vector(384) not null,
  text_hash  text not null,
  model      text not null default 'gte-small',
  updated_at timestamptz not null default now()
);

alter table public.work_embeddings enable row level security;
alter table public.trope_embeddings enable row level security;
create policy "Public read access" on public.work_embeddings for select to anon, authenticated using (true);
create policy "Public read access" on public.trope_embeddings for select to anon, authenticated using (true);
grant select on public.work_embeddings, public.trope_embeddings to anon, authenticated;
grant all on public.work_embeddings, public.trope_embeddings to service_role;

create index work_embeddings_hnsw on public.work_embeddings
  using hnsw (embedding extensions.vector_cosine_ops);

-- Books closest in meaning to a typed search. similarity: 1 = same meaning, 0 = unrelated.
create or replace function public.match_books(query_embedding extensions.vector(384), match_count int default 20)
returns table (work_id uuid, title text, authors text, first_published_year int, cover_url text, similarity float)
language sql stable security invoker
set search_path = public, extensions
as $$
  select w.id, w.title,
         (select string_agg(a.name, ', ' order by wa.position)
            from public.work_authors wa join public.authors a on a.id = wa.author_id
            where wa.work_id = w.id and wa.role = 'author'),
         w.first_published_year,
         (select ed.cover_url from public.editions ed
            where ed.work_id = w.id and ed.cover_url is not null
            order by ed.published_year nulls last limit 1),
         1 - (e.embedding <=> query_embedding)
  from public.work_embeddings e
  join public.works w on w.id = e.work_id
  order by e.embedding <=> query_embedding
  limit least(greatest(coalesce(match_count, 20), 1), 50);
$$;

-- Tropes closest in meaning to a typed search (to suggest them as filters).
create or replace function public.match_tropes(query_embedding extensions.vector(384), match_count int default 8)
returns table (slug text, name text, category text, similarity float)
language sql stable security invoker
set search_path = public, extensions
as $$
  select t.slug, t.name, t.category, 1 - (e.embedding <=> query_embedding)
  from public.trope_embeddings e
  join public.tropes t on t.id = e.trope_id and t.is_active
  order by e.embedding <=> query_embedding
  limit least(greatest(coalesce(match_count, 8), 1), 20);
$$;

revoke all on function public.match_books(extensions.vector, int) from public;
revoke all on function public.match_tropes(extensions.vector, int) from public;
grant execute on function public.match_books(extensions.vector, int) to anon, authenticated, service_role;
grant execute on function public.match_tropes(extensions.vector, int) to anon, authenticated, service_role;

notify pgrst, 'reload schema';
