-- TropeFinder: the search engine (Phase 2).
--
-- search_books(must, prefer, exclude, formats, max_results) ranks books by trope verdicts.
--
--   must     tropes the book has to have      -> decides the band
--   prefer   tropes that are nice to have     -> raise the score
--   exclude  tropes the reader doesn't want   -> a book where one is PRESENT is removed;
--                                               where it is only "not known", the book stays
--                                               and the trope is listed as not ruled out
--   formats  e.g. {audiobook}                 -> only books a source confirms in that format
--
-- A trope counts as present if it or any of its child tropes is present
-- (choosing "soldier" also finds "special forces"). "Absent" only counts when stated for
-- the trope itself.
--
-- Bands (never presents unverified matches as exact):
--   confirmed  every must-have trope is present
--   possible   at least one must-have present, the rest not known (none ruled out)
--   (with no must-haves, books with at least one preferred trope are listed as "confirmed",
--    and with neither, every book qualifies and is ranked by title)
--
-- Runs with the caller's permissions (security invoker), so row-level security applies.

create or replace function public.search_books(
  must        text[] default '{}',
  prefer      text[] default '{}',
  exclude     text[] default '{}',
  formats     text[] default '{}',
  max_results integer default 50
)
returns table (
  work_id               uuid,
  title                 text,
  authors               text,
  first_published_year  integer,
  band                  text,
  score                 numeric,
  must_total            integer,
  must_matched          text[],
  must_unknown          text[],
  prefer_matched        text[],
  exclude_not_ruled_out text[],
  has_audiobook         boolean,
  has_ebook             boolean,
  series                text,
  cover_url             text
)
language sql
stable
security invoker
set search_path = ''
as $$
with recursive
sel as (
  -- the reader's choices, kept only if they name an active trope
  select distinct s.slug, s.kind, t.id as root_id
  from (
    select unnest(coalesce(search_books.must,    '{}'::text[])) as slug, 'must'    as kind
    union all
    select unnest(coalesce(search_books.prefer,  '{}'::text[])), 'prefer'
    union all
    select unnest(coalesce(search_books.exclude, '{}'::text[])), 'exclude'
  ) s
  join public.tropes t on t.slug = s.slug and t.is_active
),
tree as (
  -- each chosen trope plus all of its descendants
  select slug, kind, root_id, root_id as trope_id from sel
  union all
  select tr.slug, tr.kind, tr.root_id, c.id
  from tree tr
  join public.tropes c on c.parent_id = tr.trope_id and c.is_active
),
hits as (
  -- per book and chosen trope: present (itself or a child), absent (itself), else unknown
  select bt.work_id, tr.slug, tr.kind,
         case when bool_or(bt.status = 'present') then 'present'
              when bool_or(bt.status = 'absent' and bt.trope_id = tr.root_id) then 'absent'
              else 'unknown' end as status,
         max(bt.confidence) filter (where bt.status = 'present') as confidence,
         bool_or(bt.status = 'present'
                 and bt.verification in ('source_tag', 'human_verified', 'community_verified')) as strong
  from tree tr
  join public.book_tropes bt on bt.trope_id = tr.trope_id
  group by bt.work_id, tr.slug, tr.kind
),
counts as (
  select count(*) filter (where kind = 'must')   as n_must,
         count(*) filter (where kind = 'prefer') as n_prefer
  from sel
),
per_book as (
  select w.id as work_id,
         coalesce(array_agg(h.slug order by h.slug) filter (where h.kind = 'must'    and h.status = 'present'), '{}') as must_matched,
         coalesce(array_agg(h.slug order by h.slug) filter (where h.kind = 'must'    and h.status = 'absent'),  '{}') as must_absent,
         coalesce(array_agg(h.slug order by h.slug) filter (where h.kind = 'prefer'  and h.status = 'present'), '{}') as prefer_matched,
         coalesce(array_agg(h.slug order by h.slug) filter (where h.kind = 'exclude' and h.status = 'present'), '{}') as exclude_present,
         coalesce(array_agg(h.slug order by h.slug) filter (where h.kind = 'exclude' and h.status = 'absent'),  '{}') as exclude_absent,
         count(*) filter (where h.kind = 'must' and h.status = 'present' and h.strong) as strong_must,
         avg(h.confidence) filter (where h.status = 'present') as avg_confidence
  from public.works w
  left join hits h on h.work_id = w.id
  group by w.id
),
scored as (
  select p.*,
         array(select s.slug from sel s where s.kind = 'must' and s.slug <> all(p.must_matched) order by s.slug) as must_unknown,
         array(select s.slug from sel s where s.kind = 'exclude'
                 and s.slug <> all(p.exclude_present) and s.slug <> all(p.exclude_absent) order by s.slug) as exclude_not_ruled_out,
         case
           when cardinality(p.exclude_present) > 0 or cardinality(p.must_absent) > 0 then null
           when c.n_must > 0 and cardinality(p.must_matched) = c.n_must then 'confirmed'
           when c.n_must > 0 and cardinality(p.must_matched) >= 1 then 'possible'
           when c.n_must > 0 then null
           when c.n_prefer > 0 and cardinality(p.prefer_matched) = 0 then null
           else 'confirmed'
         end as band,
         round(cardinality(p.must_matched) * 10 + cardinality(p.prefer_matched) * 3
               + p.strong_must + coalesce(p.avg_confidence, 0), 3) as score
  from per_book p cross join counts c
),
fmt as (
  select f.work_id,
         bool_or(f.format = 'audiobook') as has_audiobook,
         bool_or(f.format = 'ebook') as has_ebook,
         array_agg(distinct f.format) as formats
  from public.format_availability f
  where f.work_id is not null and f.confirmed
  group by f.work_id
)
select s.work_id,
       w.title,
       (select string_agg(a.name, ', ' order by wa.position)
          from public.work_authors wa join public.authors a on a.id = wa.author_id
          where wa.work_id = s.work_id and wa.role = 'author') as authors,
       w.first_published_year,
       s.band,
       s.score,
       (select count(*)::integer from sel where kind = 'must') as must_total,
       s.must_matched,
       s.must_unknown,
       s.prefer_matched,
       s.exclude_not_ruled_out,
       coalesce(f.has_audiobook, false),
       coalesce(f.has_ebook, false),
       (select sr.name || coalesce(' #' || trim(trailing '.' from trim(trailing '0' from ws.position::text)), '')
          from public.work_series ws join public.series sr on sr.id = ws.series_id
          where ws.work_id = s.work_id
          order by ws.position nulls last, sr.name limit 1) as series,
       (select e.cover_url from public.editions e
          where e.work_id = s.work_id and e.cover_url is not null
          order by e.published_year nulls last limit 1) as cover_url
from scored s
join public.works w on w.id = s.work_id
left join fmt f on f.work_id = s.work_id
where s.band is not null
  and (cardinality(coalesce(search_books.formats, '{}'::text[])) = 0
       or coalesce(f.formats, '{}') @> search_books.formats)
order by (s.band = 'confirmed') desc, s.score desc, w.title
limit greatest(1, least(coalesce(search_books.max_results, 50), 200));
$$;

revoke all on function public.search_books(text[], text[], text[], text[], integer) from public;
grant execute on function public.search_books(text[], text[], text[], text[], integer) to anon, authenticated, service_role;

-- Tell Supabase's API layer to pick up the new function straight away.
notify pgrst, 'reload schema';
