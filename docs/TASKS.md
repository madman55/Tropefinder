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

## Outstanding (later stages)

- [x] **Stage 2 complete (25 Nov 2026):** taxonomy synced into Supabase.
  - [x] Loader written (`pipeline/sync_taxonomy.py`, `pipeline/db.py`) with 9 tests on an in-memory database.
  - [x] Tested against local PostgreSQL 16 as service_role: run 1 = 78 tropes, 144 aliases, 5 relations, 18 parent links; run 2 = 0 changes.
  - [x] GitHub workflow `sync taxonomy` (manual button + automatic when tropes.yaml changes).
  - [x] Repository secrets SUPABASE_URL and SUPABASE_SECRET_KEY added (Joseph).
  - [x] On Supabase via GitHub Actions: dry run planned 245 changes; real run applied 78 tropes, 144 aliases, 18 parent links, 5 relations; separate second run = 0 changes.
- [x] **Stage 3 complete (25 Nov 2026):** 28 of 30 seed books imported from Open Library.
  - [x] Importer (`pipeline/import_openlibrary.py`), Open Library client with 1 req/s limit, retries and response cache, matching helpers (`pipeline/normalise.py`).
  - [x] Matching rule: title similarity ≥ 0.88 AND author surname match; otherwise "not found", never guessed. Same title + author under a new ID → "needs review", not merged.
  - [x] 10 new tests (26 total). Local PostgreSQL run with sample data: run 1 imported 2 works / 3 editions; run 2 changed nothing.
  - [x] Migration `20261125020000_work_subjects.sql` (stores Open Library subjects).
  - [x] Migration applied on Supabase (Joseph).
  - [x] Dry run: 28 matched (all with correct author), 2 not found. Matches reviewed.
  - [x] Real import: 28 works, 281 editions (299 seen; 18 skipped as duplicate ISBNs), 88 API requests at 1/s.
  - [x] Second run: 28 unchanged, 0 editions added.

**Stage 3 findings (coverage report)**

- Data-quality note: Open Library's *Mother of Learning* work (OL26911545W) mixes ISBNs from several arcs; Hardcover lists Arcs 1–4 separately. Seed entry renamed to "Mother of Learning: Arc 1" (26 Nov) so the right volume is matched.

- Not in Open Library: *Super Powereds: Year 1* (no results) and *The Wandering Inn* (1 result, author didn't match). Logged, not entered by hand.
- Only 17 of 28 have a description. Missing for: Monster Hunter International, Rivers of London, Hounded, Wearing the Cape, Ex-Heroes, Columbus Day, Unsouled, Mother of Learning, Ascend Online, The Land: Founding, Sufficiently Advanced Magic. Mostly the indie/LitRPG titles: exactly the core audience. Hardcover (Stage 4) needs to fill this gap.
- Edition formats are mostly "unknown" in Open Library; format filtering will need Hardcover's has_audiobook / has_ebook flags.
- Not yet stored: series (Open Library has no reliable series data; Hardcover in Stage 4).
- [x] **Stage 4 complete (26 Nov 2026):** Hardcover enrichment; all 30 seed books now in the database.
  - [x] Hardcover terms confirmed on Hardcover's own docs (26 Nov): 60 req/min, 5,000/day; aggregate data OK if labelled Hardcover; no user-owned data; no images; not for training public/commercial LLMs.
  - [x] Migration `20261126000000_work_source_tags.sql`; client `pipeline/sources/hardcover.py`; `pipeline/enrich_hardcover.py`; workflow "enrich hardcover"; 8 new tests (34 total); local PostgreSQL run twice (second run unchanged).
  - [x] Migration applied on Supabase; HARDCOVER_TOKEN secret added (Joseph).
  - [x] Fixes found by dry runs: token needed "Bearer " (client now retries in that form); Mother of Learning matched Arc 2 (seed renamed to Arc 1; exact titles now outrank sequels).
  - [x] Real run: 28 enriched, 2 created (Super Powereds: Year 1, The Wandering Inn), 781 Hardcover tags, all 11 missing descriptions filled, audiobook/ebook flags and series added. 32 API requests.
  - [x] Second run: 30 unchanged.
  - [ ] Tag-to-trope mapping (moved to Stage 6 with classification).
- [x] **Stage 5 complete (26 Nov 2026):** Joseph labelled the 9 seed books he has read: 90 labels (51 present, 28 absent, 11 unknown), in `data/gold_labels.csv` (entered via the "TropeFinder gold labels" Sheet).
  - Finding: Joseph read tropes as "anywhere in the book" (e.g. soldiers in the story), not "the main character". Adopted as the rule for every category except protagonist; new story-wide trope `military-characters` (taxonomy v0.2), which the "Soldier" column maps to.
  - Labels are used exactly as given. Where the classifier disagrees, the cell is shown to Joseph for a second look rather than assumed wrong either way.
- [ ] Stage 6: classification layers (tags, rules, Claude Haiku with quote checking); accuracy report against gold labels.
- [ ] Stage 7: scale to 50, then 500 books; scheduled incremental import.

## Known gaps and risks

- Hardcover terms are from a third-party summary; must be confirmed on Hardcover's own rules page (SETUP step 6).
- Some seed books (indie and web-serial titles) may not be in Open Library. That is a finding to record, not a bug.
