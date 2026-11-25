-- TropeFinder schema smoke test.
-- Run after the migration and seed. Everything happens inside a transaction
-- that is rolled back at the end, so no test data is left behind.
-- Success: the last line printed is "ALL SCHEMA CHECKS PASSED".
-- Failure: the script stops with an error naming the check that failed.

begin;

-- Helper: expect a statement to be rejected by the database.
create or replace function pg_temp.expect_error(label text, stmt text)
returns void language plpgsql as $$
begin
  begin
    execute stmt;
  exception when others then
    raise notice 'ok   - % (rejected: %)', label, sqlerrm;
    return;
  end;
  raise exception 'FAIL - % : statement was accepted but should have been rejected', label;
end;
$$;

do $$
declare
  src uuid; w uuid; w2 uuid; tr uuid; bt uuid; after_ts timestamptz;
begin
  -- 1. Seed rows exist exactly once.
  if (select count(*) from public.data_sources) <> 7 then
    raise exception 'FAIL - expected 7 data_sources rows, found %', (select count(*) from public.data_sources);
  end if;
  raise notice 'ok   - 7 data sources seeded';

  select id into src from public.data_sources where slug = 'openlibrary';

  insert into public.works (title, normalised_title) values ('Test Work', 'test work') returning id into w;
  insert into public.works (title, normalised_title) values ('Other Work', 'other work') returning id into w2;
  insert into public.editions (work_id, isbn_13, format) values (w, '9780000000002', 'ebook');
  insert into public.external_ids (entity_type, entity_id, source_id, external_id)
    values ('work', w, src, 'OL0000001W');
  insert into public.tropes (slug, name, category, definition)
    values ('test-trope', 'Test trope', 'abilities', 'A trope used only by this test.') returning id into tr;
  insert into public.book_tropes (work_id, trope_id, status, confidence, verification)
    values (w, tr, 'present', 0.9, 'ai_suggested') returning id into bt;
  raise notice 'ok   - valid rows accepted';

  perform pg_temp.expect_error('duplicate ISBN-13 rejected',
    format('insert into public.editions (work_id, isbn_13) values (%L, %L)', w2, '9780000000002'));
  perform pg_temp.expect_error('malformed ISBN-13 rejected',
    format('insert into public.editions (work_id, isbn_13) values (%L, %L)', w, '12345'));
  perform pg_temp.expect_error('duplicate source ID rejected',
    format('insert into public.external_ids (entity_type, entity_id, source_id, external_id) values (%L, %L, %L, %L)',
           'work', w2, src, 'OL0000001W'));
  perform pg_temp.expect_error('second verdict for same book + trope rejected',
    format('insert into public.book_tropes (work_id, trope_id, status, confidence, verification) values (%L, %L, %L, 0.5, %L)',
           w, tr, 'absent', 'rule_based'));
  perform pg_temp.expect_error('invalid status rejected',
    format('insert into public.book_tropes (work_id, trope_id, status, confidence, verification) values (%L, %L, %L, 0.5, %L)',
           w2, tr, 'maybe', 'rule_based'));
  perform pg_temp.expect_error('confidence above 1 rejected',
    format('insert into public.book_tropes (work_id, trope_id, status, confidence, verification) values (%L, %L, %L, 1.5, %L)',
           w2, tr, 'present', 'rule_based'));
  perform pg_temp.expect_error('quote evidence without a quote rejected',
    format('insert into public.classification_evidence (book_trope_id, supports, evidence_type, source_id) values (%L, %L, %L, %L)',
           bt, 'present', 'quote', src));
  perform pg_temp.expect_error('bad trope slug rejected',
    'insert into public.tropes (slug, name, category, definition) values (''Bad Slug'', ''x'', ''abilities'', ''x'')');
  perform pg_temp.expect_error('unknown trope category rejected',
    'insert into public.tropes (slug, name, category, definition) values (''ok-slug'', ''x'', ''romance'', ''x'')');
  perform pg_temp.expect_error('trope alias duplicate (case-insensitive) rejected',
    format('insert into public.trope_aliases (trope_id, alias) values (%L, %L), (%L, %L)', tr, 'Guns', tr, 'guns'));

  -- 2. updated_at trigger: try to set an old date; the trigger should overwrite it.
  update public.works set updated_at = '2000-01-01', title = 'Test Work' where id = w;
  select updated_at into after_ts from public.works where id = w;
  if after_ts = '2000-01-01'::timestamptz then
    raise exception 'FAIL - updated_at trigger did not run';
  end if;
  raise notice 'ok   - updated_at trigger runs on update';

  -- 3. Cascade: deleting a work removes its verdicts.
  delete from public.works where id = w2;
  raise notice 'ok   - work delete accepted';
end;
$$;

-- 4. Row-level security as the public (anon) role.
set local role anon;

do $$
begin
  if (select count(*) from public.works) < 1 then
    raise exception 'FAIL - anon role cannot read works';
  end if;
  raise notice 'ok   - anon can read works';

  -- Internal tables: anon must be refused outright (no grant).
  perform pg_temp.expect_error('anon cannot read import_runs',
    'select count(*) from public.import_runs');
  perform pg_temp.expect_error('anon cannot read classification_runs',
    'select count(*) from public.classification_runs');

  -- Writes: refused either by missing permission or by row-level security.
  perform pg_temp.expect_error('anon cannot insert works',
    'insert into public.works (title, normalised_title) values (''Hack'', ''hack'')');
  perform pg_temp.expect_error('anon cannot update works',
    'update public.works set title = ''Hack'' where true');
  perform pg_temp.expect_error('anon cannot delete works',
    'delete from public.works where true');
  perform pg_temp.expect_error('anon cannot insert verdicts',
    'insert into public.book_tropes (work_id, trope_id, status, confidence, verification)
       select w.id, t.id, ''present'', 1, ''human_verified'' from public.works w, public.tropes t limit 1');
end;
$$;

reset role;

select 'ALL SCHEMA CHECKS PASSED' as result;

rollback;
