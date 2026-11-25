-- TropeFinder: reference rows every environment needs.
-- Safe to run more than once: existing rows are left alone.
-- Terms checked 2026-11-25. Re-check before relying on them (see docs/DATA_SOURCES.md).

insert into public.data_sources (slug, name, kind, licence, terms_url, rate_limit_note, terms_checked_on, notes)
values
  ('openlibrary', 'Open Library', 'catalogue', 'CC0 1.0 (public domain)',
   'https://openlibrary.org/developers/api',
   '1 req/s anonymous; 3 req/s with User-Agent naming app + contact email. Use monthly dumps for bulk.',
   '2026-11-25', 'Primary catalogue source.'),
  ('hardcover', 'Hardcover', 'community', 'Reported as same licensing as Open Library; verify on Hardcover API rules page',
   'https://docs.hardcover.app',
   'Reported ~60 requests/minute; needs personal API token. Verify before use.',
   '2026-11-25', 'Reader tags, moods, series, format flags. Covers must be self-hosted for professional use.'),
  ('google_books', 'Google Books', 'catalogue', 'Google Books API Terms of Service',
   'https://developers.google.com/books/terms',
   'Needs API key; quota per Google Cloud project.',
   '2026-11-25', 'Optional enrichment only. Terms forbid charging users without Google permission; attribution required.'),
  ('manual', 'Manual entry', 'manual', 'Own data', null, null, '2026-11-25',
   'Books or facts entered by hand by the project owner.'),
  ('rules', 'Keyword rules', 'classifier', 'Own data', null, null, '2026-11-25',
   'Trope suggestions from keyword patterns in pipeline/classify/rules.py.'),
  ('ai_claude_haiku', 'Claude Haiku (AI classifier)', 'classifier', 'Own data (model output)',
   'https://platform.claude.com/docs/en/about-claude/pricing', 'Batch API', '2026-11-25',
   'Trope verdicts only; never used for book facts. Every quote is checked against stored text.'),
  ('human', 'Human review', 'manual', 'Own data', null, null, '2026-11-25',
   'Verdicts set or confirmed by a person.')
on conflict (slug) do nothing;
