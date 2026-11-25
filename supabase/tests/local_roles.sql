-- For testing on a plain PostgreSQL (CI or your own machine), NOT on Supabase.
-- Recreates the roles Supabase provides, so the migrations and smoke test
-- behave the same way they do on Supabase.
--
-- Deliberately gives NO automatic table permissions to anon / authenticated:
-- newer Supabase projects don't either, so the migrations must grant access
-- explicitly (see 20261125010000_grant_public_read.sql). Run this first.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
end;
$$;
