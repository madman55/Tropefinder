-- TropeFinder search engine test. Run after all migrations and the seed.
-- Builds a few made-up books inside a transaction, searches as the public (anon)
-- role, checks the answers, then rolls everything back.
-- Success: the last line printed is "ALL SEARCH CHECKS PASSED".

begin;

do $$
declare
  src uuid := (select id from public.data_sources where slug = 'manual');
  t_soldier uuid; t_sf uuid; t_magic uuid; t_romance uuid; t_litrpg uuid;
  w_a uuid; w_b uuid; w_c uuid; w_d uuid; w_e uuid;
begin
  insert into public.tropes (slug, name, category, definition) values ('zz-soldier', 'Soldier', 'protagonist', 'x') returning id into t_soldier;
  insert into public.tropes (slug, name, category, definition, parent_id) values ('zz-special-forces', 'SF', 'protagonist', 'x', t_soldier) returning id into t_sf;
  insert into public.tropes (slug, name, category, definition) values ('zz-magic', 'Magic', 'abilities', 'x') returning id into t_magic;
  insert into public.tropes (slug, name, category, definition) values ('zz-romance', 'Romance', 'relationships', 'x') returning id into t_romance;
  insert into public.tropes (slug, name, category, definition) values ('zz-litrpg', 'LitRPG', 'structure-and-tone', 'x') returning id into t_litrpg;

  insert into public.works (title, normalised_title) values ('ZZ A: soldier mage', 'zz a')       returning id into w_a;
  insert into public.works (title, normalised_title) values ('ZZ B: special forces only', 'zz b') returning id into w_b;
  insert into public.works (title, normalised_title) values ('ZZ C: soldier mage romance', 'zz c') returning id into w_c;
  insert into public.works (title, normalised_title) values ('ZZ D: no soldiers stated', 'zz d')  returning id into w_d;
  insert into public.works (title, normalised_title) values ('ZZ E: soldier mage, no romance', 'zz e') returning id into w_e;

  insert into public.book_tropes (work_id, trope_id, status, confidence, verification) values
    (w_a, t_soldier, 'present', 0.9, 'source_tag'), (w_a, t_magic, 'present', 0.8, 'ai_suggested'),
    (w_b, t_sf, 'present', 0.9, 'ai_suggested'),
    (w_c, t_soldier, 'present', 0.9, 'ai_suggested'), (w_c, t_magic, 'present', 0.9, 'ai_suggested'),
    (w_c, t_romance, 'present', 0.9, 'ai_suggested'),
    (w_d, t_soldier, 'absent', 0.9, 'human_verified'), (w_d, t_magic, 'present', 0.9, 'ai_suggested'),
    (w_e, t_soldier, 'present', 0.9, 'human_verified'), (w_e, t_magic, 'present', 0.9, 'human_verified'),
    (w_e, t_romance, 'absent', 0.9, 'human_verified'), (w_e, t_litrpg, 'present', 0.9, 'ai_suggested');

  insert into public.format_availability (work_id, format, source_id, confirmed) values (w_e, 'audiobook', src, true);

  -- "superhero protagonist" implies "superpowers"
  insert into public.tropes (slug, name, category, definition) values ('zz-superpowers', 'Superpowers', 'abilities', 'x');
  insert into public.tropes (slug, name, category, definition) values ('zz-superhero', 'Superhero', 'protagonist', 'x');
  insert into public.trope_relations (trope_id, related_trope_id, relation)
    select h.id, p.id, 'implies' from public.tropes h, public.tropes p where h.slug = 'zz-superhero' and p.slug = 'zz-superpowers';
  insert into public.works (title, normalised_title) values ('ZZ F: caped hero', 'zz f');
  insert into public.book_tropes (work_id, trope_id, status, confidence, verification)
    select w.id, t.id, 'present', 0.9, 'source_tag' from public.works w, public.tropes t where w.title = 'ZZ F: caped hero' and t.slug = 'zz-superhero';
end;
$$;

set local role anon;

do $$
declare
  got text;
  q constant text := $q$ public.search_books(array['zz-soldier', 'zz-magic'], array['zz-litrpg'], array['zz-romance']) $q$;
begin
  -- Confirmed: A and E (both musts present). C has romance -> removed. B: soldier via child, magic unknown -> possible.
  -- D: soldier explicitly absent -> removed.
  execute 'select string_agg(title || ''='' || band, '', '' order by title) from ' || q into got;
  if got is distinct from 'ZZ A: soldier mage=confirmed, ZZ B: special forces only=possible, ZZ E: soldier mage, no romance=confirmed' then
    raise exception 'FAIL - bands: %', got;
  end if;
  raise notice 'ok   - bands, child trope counts for parent, exclusion and absent handled';

  -- E ranks first: same musts as A, plus the preferred LitRPG trope and stronger evidence.
  execute 'select title from ' || q || ' limit 1' into got;
  if got <> 'ZZ E: soldier mage, no romance' then
    raise exception 'FAIL - ranking';
  end if;
  raise notice 'ok   - preferred trope raises the score';

  -- A: romance not known -> listed as not ruled out. E: romance stated absent -> nothing listed.
  execute 'select array_to_string(exclude_not_ruled_out, '','') from ' || q || ' where title like ''ZZ A%''' into got;
  if got <> 'zz-romance' then
    raise exception 'FAIL - A should list romance as not ruled out';
  end if;
  execute 'select cardinality(exclude_not_ruled_out)::text from ' || q || ' where title like ''ZZ E%''' into got;
  if got <> '0' then
    raise exception 'FAIL - E has romance ruled out';
  end if;
  execute 'select array_to_string(must_unknown, '','') from ' || q || ' where title like ''ZZ B%''' into got;
  if got <> 'zz-magic' then
    raise exception 'FAIL - B should list magic as unknown';
  end if;
  raise notice 'ok   - unknowns and not-ruled-out exclusions are reported';

  -- Format filter: only E is a confirmed audiobook.
  select string_agg(title, ', ') into got from public.search_books(array['zz-soldier'], '{}', '{}', array['audiobook']);
  if got is distinct from 'ZZ E: soldier mage, no romance' then
    raise exception 'FAIL - audiobook filter: %', got;
  end if;
  raise notice 'ok   - audiobook filter';

  -- Unknown slugs are ignored, and the result size is capped.
  if (select count(*) from public.search_books(array['zz-soldier', 'not-a-trope'])) < 3 then
    raise exception 'FAIL - unknown slug should be ignored';
  end if;
  if (select count(*) from public.search_books(max_results => 1)) <> 1 then
    raise exception 'FAIL - max_results';
  end if;
  raise notice 'ok   - unknown tropes ignored, result size capped';

  select string_agg(title, ', ') into got from public.search_books(array['zz-superpowers']);
  if got is distinct from 'ZZ F: caped hero' then
    raise exception 'FAIL - implied trope: %', got;
  end if;
  raise notice 'ok   - a trope that implies the chosen one counts for it';
end;
$$;

reset role;
select 'ALL SEARCH CHECKS PASSED' as result;
rollback;
