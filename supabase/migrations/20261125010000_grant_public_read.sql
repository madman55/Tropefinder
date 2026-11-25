-- TropeFinder: explicit table permissions.
--
-- Newer Supabase projects no longer give the public roles (anon, authenticated)
-- automatic access to new tables. That is safer, but it means we must grant
-- read access ourselves. Row-level security still applies on top of these grants.
--
--   * Public catalogue and trope tables: anyone may SELECT (read). Nothing else.
--   * Internal log tables (import_runs, classification_runs): no public access at all.
--   * service_role (the Python jobs' secret key): full access.

grant usage on schema public to anon, authenticated, service_role;

-- Start from nothing, so the result is the same on old and new Supabase projects.
revoke all on all tables in schema public from anon, authenticated;

grant select on
  public.data_sources,
  public.works,
  public.editions,
  public.authors,
  public.work_authors,
  public.series,
  public.work_series,
  public.genres,
  public.work_genres,
  public.external_ids,
  public.format_availability,
  public.external_links,
  public.tropes,
  public.trope_aliases,
  public.trope_relations,
  public.book_tropes,
  public.classification_evidence
to anon, authenticated;

grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
