# Tasks

Updated: 2026-11-25. Only things actually run are marked as tested; the test environment is named each time.

## Done

- [x] Phase 1 architecture and plan written and agreed.
- [x] Decisions recorded: Hardcover as second source, Claude Haiku for AI tagging, private repo, hand-labelled gold set ([decisions](decisions/)).
- [x] Database schema: 19 tables, row-level security on all, public read-only policies, indexes, duplicate guards.
  - Tested on local PostgreSQL 16 (old and new Supabase permission models) and in GitHub Actions: all 21 smoke-test checks pass.
  - First run on Supabase (25 Nov) found that new Supabase projects give the public role no table access by default. Fixed with an explicit-permissions migration (`20261125010000_grant_public_read.sql`). Re-tested on Supabase 25 Nov: ALL SCHEMA CHECKS PASSED.
- [x] Seed data for 7 data sources.
- [x] Trope taxonomy v0.1: 78 tropes in 8 categories. Checker passes.
- [x] Taxonomy checker and 7 unit tests (all passing locally).
- [x] Proof-of-concept book list (30 candidates) and blank gold-label template.
- [x] Docs: README, architecture, roadmap, setup, data sources, this file.
- [x] GitHub Actions workflow for tests: passing on GitHub.
- [x] Repo created (private) and Stage 1 pushed.
- [x] Supabase project created; schema migration and seed applied.

**Stage 1 complete (25 Nov 2026).**

## Next (Joseph)

- [ ] `.env` with Supabase URL and secret key (needed from Stage 2; only on a machine that runs the Python jobs).
- [ ] Fill in `data/gold_labels.csv` for the ~20 books you know (present / absent / unknown). Leave books you haven't read blank.

## Outstanding (later stages)

- [ ] Stage 2: sync `taxonomy/tropes.yaml` into the database (idempotent).
- [ ] Stage 3: Open Library importer for the 30 seed books, with duplicate checks and a coverage report.
- [ ] Stage 4: Hardcover enrichment (tags, formats, series) and tag-to-trope mapping.
- [ ] Stage 5: gold labels complete.
- [ ] Stage 6: classification layers (tags, rules, Claude Haiku with quote checking); accuracy report against gold labels.
- [ ] Stage 7: scale to 50, then 500 books; scheduled incremental import.

## Known gaps and risks

- Hardcover terms are from a third-party summary; must be confirmed on Hardcover's own rules page (SETUP step 6).
- Some seed books (indie and web-serial titles) may not be in Open Library. That is a finding to record, not a bug.
