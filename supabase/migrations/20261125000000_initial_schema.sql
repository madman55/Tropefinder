-- TropeFinder: Phase 1 initial schema
-- Creates the catalogue, provenance and trope-intelligence tables.
-- User tables (profiles, reading history, lists, community votes) arrive in Phase 4.
--
-- Plain-English rules this schema enforces:
--   * Every table has a UUID id and created_at / updated_at timestamps.
--   * A source's own ID for a record can only be stored once (main duplicate guard).
--   * An ISBN-13 can only belong to one edition.
--   * A book has at most one current verdict per trope: present, absent or unknown.
--   * Anyone may READ catalogue and trope data; only the service key (Python jobs) may WRITE.

create schema if not exists extensions;
create extension if not exists pg_trgm with schema extensions;

-- Keeps updated_at current on every update.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

------------------------------------------------------------------------
-- Provenance: where information comes from
------------------------------------------------------------------------

create table public.data_sources (
  id               uuid primary key default gen_random_uuid(),
  slug             text not null unique,
  name             text not null,
  kind             text not null check (kind in ('catalogue', 'community', 'manual', 'classifier')),
  licence          text,
  terms_url        text,
  rate_limit_note  text,
  terms_checked_on date,
  notes            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

------------------------------------------------------------------------
-- Catalogue: what exists
------------------------------------------------------------------------

create table public.works (
  id                    uuid primary key default gen_random_uuid(),
  title                 text not null,
  subtitle              text,
  normalised_title      text not null,
  first_published_year  integer check (first_published_year between 1000 and 2100),
  description           text,
  description_source_id uuid references public.data_sources (id),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create table public.editions (
  id             uuid primary key default gen_random_uuid(),
  work_id        uuid not null references public.works (id) on delete cascade,
  title          text,
  isbn_10        text check (isbn_10 ~ '^[0-9]{9}[0-9X]$'),
  isbn_13        text check (isbn_13 ~ '^97[89][0-9]{10}$'),
  asin           text,
  format         text not null default 'unknown'
                 check (format in ('hardcover', 'paperback', 'ebook', 'audiobook', 'other', 'unknown')),
  publisher      text,
  published_text text,   -- dates arrive in many shapes ("2009", "May 2009"); kept as given
  published_year integer check (published_year between 1000 and 2100),
  language       text,
  page_count     integer check (page_count > 0),
  cover_url      text,
  source_id      uuid references public.data_sources (id),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create table public.authors (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  normalised_name text not null,
  sort_name       text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table public.work_authors (
  work_id    uuid not null references public.works (id) on delete cascade,
  author_id  uuid not null references public.authors (id) on delete cascade,
  role       text not null default 'author'
             check (role in ('author', 'co-author', 'editor', 'narrator', 'translator', 'illustrator')),
  position   integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (work_id, author_id, role)
);

create table public.series (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  normalised_name text not null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table public.work_series (
  work_id        uuid not null references public.works (id) on delete cascade,
  series_id      uuid not null references public.series (id) on delete cascade,
  position       numeric(6, 2),   -- decimal so a novella can sit at 2.5
  position_label text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  primary key (work_id, series_id)
);

create table public.genres (
  id         uuid primary key default gen_random_uuid(),
  slug       text not null unique,
  name       text not null,
  parent_id  uuid references public.genres (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.work_genres (
  work_id    uuid not null references public.works (id) on delete cascade,
  genre_id   uuid not null references public.genres (id) on delete cascade,
  source_id  uuid references public.data_sources (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (work_id, genre_id)
);

------------------------------------------------------------------------
-- Provenance links on catalogue records
------------------------------------------------------------------------

create table public.external_ids (
  id              uuid primary key default gen_random_uuid(),
  entity_type     text not null check (entity_type in ('work', 'edition', 'author', 'series')),
  entity_id       uuid not null,
  source_id       uuid not null references public.data_sources (id),
  external_id     text not null,
  raw_url         text,
  last_fetched_at timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  -- The main duplicate guard: one source record maps to exactly one of ours.
  unique (source_id, entity_type, external_id)
);

create table public.format_availability (
  id         uuid primary key default gen_random_uuid(),
  work_id    uuid references public.works (id) on delete cascade,
  edition_id uuid references public.editions (id) on delete cascade,
  format     text not null check (format in ('hardcover', 'paperback', 'ebook', 'audiobook')),
  source_id  uuid not null references public.data_sources (id),
  confirmed  boolean not null default false,
  checked_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (work_id is not null or edition_id is not null)
);

create table public.external_links (
  id           uuid primary key default gen_random_uuid(),
  work_id      uuid not null references public.works (id) on delete cascade,
  link_type    text not null check (link_type in ('retailer', 'library', 'audiobook', 'info', 'author_site')),
  url          text not null,
  label        text,
  source_id    uuid references public.data_sources (id),
  is_affiliate boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (work_id, url)
);

create table public.import_runs (
  id          uuid primary key default gen_random_uuid(),
  source_id   uuid references public.data_sources (id),
  job_name    text not null,
  status      text not null default 'running'
              check (status in ('running', 'succeeded', 'partial', 'failed')),
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  counts      jsonb not null default '{}'::jsonb,
  cursor      text,   -- where an incremental import should resume
  error_log   text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

------------------------------------------------------------------------
-- Trope intelligence
------------------------------------------------------------------------

create table public.tropes (
  id              uuid primary key default gen_random_uuid(),
  slug            text not null unique check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  name            text not null,
  category        text not null check (category in (
                    'protagonist', 'abilities', 'setting', 'story-mechanics',
                    'technology', 'relationships', 'structure-and-tone', 'format')),
  parent_id       uuid references public.tropes (id),
  definition      text not null,
  classifier_hint text,
  is_active       boolean not null default true,
  sort_order      integer not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  check (parent_id is null or parent_id <> id)
);

create table public.trope_aliases (
  id         uuid primary key default gen_random_uuid(),
  trope_id   uuid not null references public.tropes (id) on delete cascade,
  alias      text not null,
  alias_type text not null default 'synonym'
             check (alias_type in ('synonym', 'search_phrase', 'source_tag')),
  source_id  uuid references public.data_sources (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.trope_relations (
  trope_id         uuid not null references public.tropes (id) on delete cascade,
  related_trope_id uuid not null references public.tropes (id) on delete cascade,
  relation         text not null check (relation in ('related', 'implies', 'often_conflicts')),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  primary key (trope_id, related_trope_id, relation),
  check (trope_id <> related_trope_id)
);

create table public.classification_runs (
  id              uuid primary key default gen_random_uuid(),
  method          text not null check (method in ('source_tag', 'rules', 'ai', 'human')),
  model_version   text,
  prompt_version  text,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  books_processed integer not null default 0,
  cost_usd        numeric(10, 4),
  notes           text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table public.book_tropes (
  id                    uuid primary key default gen_random_uuid(),
  work_id               uuid not null references public.works (id) on delete cascade,
  trope_id              uuid not null references public.tropes (id) on delete cascade,
  status                text not null check (status in ('present', 'absent', 'unknown')),
  confidence            numeric(4, 3) not null check (confidence between 0 and 1),
  verification          text not null check (verification in (
                          'ai_suggested', 'rule_based', 'source_tag',
                          'human_verified', 'community_verified')),
  classification_run_id uuid references public.classification_runs (id),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  -- One current verdict per book per trope. History lives in classification_evidence.
  unique (work_id, trope_id)
);

create table public.classification_evidence (
  id                    uuid primary key default gen_random_uuid(),
  book_trope_id         uuid not null references public.book_tropes (id) on delete cascade,
  supports              text not null check (supports in ('present', 'absent')),
  evidence_type         text not null check (evidence_type in ('quote', 'source_tag', 'rule_match', 'human_note')),
  field                 text,   -- which stored field the evidence came from, e.g. 'description'
  quote                 text,
  url                   text,
  source_id             uuid not null references public.data_sources (id),
  classification_run_id uuid references public.classification_runs (id),
  model_version         text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  -- A quote-type piece of evidence must actually contain a quote.
  check (evidence_type <> 'quote' or (quote is not null and length(trim(quote)) > 0))
);

------------------------------------------------------------------------
-- Indexes (foreign keys, duplicate checks, search)
------------------------------------------------------------------------

create unique index editions_isbn_13_key on public.editions (isbn_13) where isbn_13 is not null;
create unique index editions_isbn_10_key on public.editions (isbn_10) where isbn_10 is not null;
create unique index trope_aliases_trope_alias_key on public.trope_aliases (trope_id, lower(alias));

create index editions_work_id_idx            on public.editions (work_id);
create index editions_asin_idx               on public.editions (asin) where asin is not null;
create index work_authors_author_id_idx      on public.work_authors (author_id);
create index work_series_series_id_idx       on public.work_series (series_id);
create index work_genres_genre_id_idx        on public.work_genres (genre_id);
create index genres_parent_id_idx            on public.genres (parent_id);
create index external_ids_entity_idx         on public.external_ids (entity_type, entity_id);
create index format_availability_work_idx    on public.format_availability (work_id);
create index format_availability_edition_idx on public.format_availability (edition_id);
create index external_links_work_id_idx      on public.external_links (work_id);
create index import_runs_source_started_idx  on public.import_runs (source_id, started_at desc);
create index tropes_parent_id_idx            on public.tropes (parent_id);
create index trope_aliases_lower_alias_idx   on public.trope_aliases (lower(alias));
create index trope_relations_related_idx     on public.trope_relations (related_trope_id);
create index book_tropes_trope_status_idx    on public.book_tropes (trope_id, status);
create index book_tropes_run_idx             on public.book_tropes (classification_run_id);
create index evidence_book_trope_idx         on public.classification_evidence (book_trope_id);

-- Fuzzy matching for duplicate detection and title search.
create index works_normalised_title_trgm   on public.works   using gin (normalised_title extensions.gin_trgm_ops);
create index authors_normalised_name_trgm  on public.authors using gin (normalised_name extensions.gin_trgm_ops);
create index series_normalised_name_trgm   on public.series  using gin (normalised_name extensions.gin_trgm_ops);

------------------------------------------------------------------------
-- updated_at triggers
------------------------------------------------------------------------

do $$
declare
  t text;
begin
  foreach t in array array[
    'data_sources', 'works', 'editions', 'authors', 'work_authors', 'series', 'work_series',
    'genres', 'work_genres', 'external_ids', 'format_availability', 'external_links',
    'import_runs', 'tropes', 'trope_aliases', 'trope_relations', 'classification_runs',
    'book_tropes', 'classification_evidence'
  ] loop
    execute format(
      'create trigger set_updated_at before update on public.%I
         for each row execute function public.set_updated_at()', t);
  end loop;
end;
$$;

------------------------------------------------------------------------
-- Security: row-level security
-- Every table has RLS on. Public tables get a read-only policy for the
-- anon and authenticated roles. No insert/update/delete policies exist,
-- so only the service role (which bypasses RLS) can write.
-- import_runs and classification_runs are internal: no public access.
------------------------------------------------------------------------

do $$
declare
  t text;
begin
  foreach t in array array[
    'data_sources', 'works', 'editions', 'authors', 'work_authors', 'series', 'work_series',
    'genres', 'work_genres', 'external_ids', 'format_availability', 'external_links',
    'import_runs', 'tropes', 'trope_aliases', 'trope_relations', 'classification_runs',
    'book_tropes', 'classification_evidence'
  ] loop
    execute format('alter table public.%I enable row level security', t);
  end loop;

  foreach t in array array[
    'data_sources', 'works', 'editions', 'authors', 'work_authors', 'series', 'work_series',
    'genres', 'work_genres', 'external_ids', 'format_availability', 'external_links',
    'tropes', 'trope_aliases', 'trope_relations', 'book_tropes', 'classification_evidence'
  ] loop
    execute format(
      'create policy "Public read access" on public.%I for select to anon, authenticated using (true)', t);
  end loop;
end;
$$;
