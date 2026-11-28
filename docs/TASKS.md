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

## Phase 2 — search engine and simple search page (in progress)

- [x] `search_books()` database function (migration `20261127000000_search_books.sql`): must-have / nice-to-have / don't-want tropes, child tropes count for their parent, "absent" only when stated for the trope itself, audiobook/ebook filter, bands "confirmed" (all must-haves present) and "possible" (some present, none ruled out), and for each book what matched, what isn't confirmed and which unwanted tropes aren't ruled out. Runs with the caller's permissions, so row-level security applies.
- [x] `supabase/tests/search_test.sql`: 5 checks, passing on local PostgreSQL; added to GitHub's automatic tests.
- [x] Search page `web/index.html` (plain HTML/CSS/JS, no build step): three trays (must have / nice to have / don't want), trope picker by category with search, audiobooks-only switch, shareable search links, results with reasons, book sheet with evidence quotes, sources and links. Reviewed in a phone-sized browser with sample data.
- [x] Deploy workflow (`pages.yml`) writes `config.js` from repository variables and refuses a secret key.
- [x] Repo made public; Pages set to GitHub Actions; `SUPABASE_PUBLISHABLE_KEY` variable added (Joseph).
- [x] Search migration run in Supabase; `search_test.sql` passed there (Joseph, 26 Nov).
- [x] Live at https://madman55.github.io/Tropefinder/ . The deploy now checks itself: page served, 79 tropes readable with the public key, a real search returns results, and a write with the public key is refused (HTTP 401).
- Joseph's test (27 Nov): typed "superpowers in modern world" into the find box and got nothing. The engine itself returns 26 confirmed books for superpowers + modern-earth (checked with the new "try a search" workflow); the box only matched whole trope names. Fixed: the box now offers tropes matching each word of a phrase. Real typed searches are still to come.
- First live search (magic + modern firearms, nice: soldiers, not: romance): confirmed Jonathan Strange and Mr Norrell, Small Favor, Wings of Ebony; Monster Hunter International and Control Point missing from the top 10. Suggests over-generous "modern firearms" verdicts and gaps in obvious ones; to review with Joseph's searches.
- [ ] First real searches; Joseph's searches recorded as test cases.

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
- [x] **Stage 6 complete (26 Nov 2026):** all 30 books classified and stored with evidence.
  - Layers: Hardcover tags / Open Library subjects mapped through trope names and aliases; Claude Haiku (claude-haiku-5-5) judging from stored text only, with every quote checked word-for-word. (A separate keyword-rules layer was not needed: tag mapping covers that role.)
  - Tags alone (free): 9 of 9 "present" right, but found only 18% of Joseph's present tropes.
  - Prompt v1: present 17/19 right (89%); absent 3/7 right. Fixes: "temperature" no longer accepted by the model; one response format not parsed.
  - Prompt v2 ("absent" only if stated outright): present 18/20 (90%); absent still 0/3 right, so AI-only "absent" verdicts are no longer stored (`AI_ABSENT_TRUSTED = False`).
  - Real run: present 18 of 20 right (90%, target 85%); found 35% of Joseph's present tropes; 6 invented quotes rejected; cost $0.06 for 30 books.
  - Open: Joseph to re-check Monster Hunter International (modern-earth, romance), where the classifier and his labels differ.
  - Known limit: recall is capped by how little the stored descriptions and tags say. Community verification (Phase 4) is the main route to raising it.
- [x] **Stage 7 complete (26 Nov 2026): 438 books.** Phase 1 is complete.
  - Discovery (`pipeline/discover.py`): Open Library subject searches (English, 1980+, no comics/children's/criticism) and Hardcover genre keyword searches. The first two lists were reviewed and rejected (manga volumes, children's books, classics, non-fiction, anthologies, sequels); filters tightened, 26 books excluded on review with reasons (`data/discovery_exclusions.csv`), max 3 books per author. Final list: 409 candidates (target was 470; good candidates ran out under the stricter filters).
  - Open Library import: 342 imported, 67 not found (mostly indie LitRPG), 3,788 editions, ~46 min.
  - Hardcover: 321 enriched, 66 created from Hardcover, 22 not found; 4,611 tags. Review found 7 wrong links from Open Library records that mix ISBNs across a series (e.g. A Game of Thrones → A Clash of Kings); matches now also require compatible titles, and `--recheck` removed the bad links. Re-run: everything else unchanged.
  - Classification (Batch API): 408 books in ~3 min, $0.40; 2,199 present verdicts; 74 invented quotes rejected. Against Joseph's labels: present 19/20 right (95%), found 36% of his present tropes.
  - Audit (`pipeline/audit.py`): 438 works, 4,137 editions, **0 likely duplicate works**; 345 works from both sources, 68 Hardcover-only, 25 Open Library-only; 227 with an audiobook flag; 46 without a description; 10 with no present trope. 50-book random sample reviewed: no duplicates.
  - Total AI cost so far: about $0.60.

**Known issues (Phase 1)**

- Ambiguous Open Library records ("Halo", "Gen13", "Deathlands") can't be matched reliably; "Halo" is linked to one Halo novel.
- Threadbare Volume One lost its Hardcover data (Hardcover lists it under its real title, *Stuff and Nonsense*, which the title check can't connect).
- Some Open Library works list the same author twice and some first-publication years are wrong (e.g. The Blood of Olympus shows 2001).
- One book (The Killing Spell) failed classification on a garbled response; it is retried on the next classify run.
- 10 books have too little text for any trope; 46 have no description.
- Database size not yet recorded (check Supabase → Project usage).

## Known gaps and risks

- Hardcover terms are from a third-party summary; must be confirmed on Hardcover's own rules page (SETUP step 6).
- Some seed books (indie and web-serial titles) may not be in Open Library. That is a finding to record, not a bug.

## 27 Nov 2026: tag accuracy pass
- Taxonomy 0.3 synced (7 new tropes, 41 aliases, 3 "implies" links); plural-aware tag matching.
- `migrate database` workflow + SUPABASE_ACCESS_TOKEN: migrations now applied from GitHub, recorded in `private.applied_migrations`. Applied 20261127010000_search_implies.
- Re-classified all 438 books with prompt v3 (Batch API, $0.46). Present precision 15/16 (94%); recall 15/53 (28%, was 36%: worth checking which earlier finds were lost).
- Search "superpowers + modern world": 31 confirmed (was 0). Some look loose (Dead Until Dark, Rivers of London count vampire/magic abilities as superpowers): review the Superpowers definition next.
- Recall "drop" explained: the score only counted the exact labelled trope, while search also counts child and implying tropes. Scoring now matches search; with that, 18 of 53 found (34%). The rest is run-to-run AI variation and short descriptions (romance and military characters are rarely mentioned in blurbs).
- Taxonomy 0.4 (Joseph's choice, 27 Nov): new broad "Special powers" trope (any extraordinary abilities); Superpowers narrowed to superhero-style and made its child, along with psychic abilities, superhuman strength and healing; magic, cybernetic enhancements and power progression imply it. Joseph's six broad "superpowers" labels (MHI, Old Man's War, DCC, HWFWM, Beware of Chicken, Sufficiently Advanced Magic) moved to special-powers, which is what he meant. Prompt v4.
- Re-tagged with prompt v4 ($0.47): present precision 19/20 (95%), found 19/53 (36%). Search "superpowers + modern Earth" now 27 confirmed, superhero-style only (Dead Until Dark and Rivers of London gone; they show under Special powers). Borderline: The Time Traveler's Wife.
- Next: meaning search (Phase 2), then grow to ~2,000 books, then Phase 3 app.

## 27 Nov 2026: meaning search (Phase 2)
- Supabase "embed" Edge Function (built-in gte-small, 384 numbers per text) deployed from GitHub (`deploy edge functions`); checked live: similar texts 0.86 vs unrelated 0.68.
- pgvector tables `work_embeddings` / `trope_embeddings`, `match_books` / `match_tropes` (migrations 20261127020000-030000). All 438 books and 87 tropes embedded (`embed books and tropes`; re-runs only touch changed text; also runs after each taxonomy sync).
- Page: typing a description shows "Sounds like" trope suggestions (tap to add), turns "no X" / "without X" into "Don't want" suggestions, and Find books adds a "Similar in feel" list that is clearly labelled as not confirmed. Don't-want tropes and the audiobook filter apply to it too.
- Calibration from real queries: trope suggestions within 0.05 of the best match and at least 0.815; books at least 0.80.
- Rate limiting: the function caps 8 texts x 2,000 characters per call; the page waits for a pause in typing and caches. No per-visitor limit yet (fine at this scale; revisit before sharing widely).

## 28 Nov 2026: library growth to 1,784 books
- Discovery widened (13 more Open Library subjects, 27 more Hardcover terms, 5 per author) and filtered (non-fiction, publisher-credited tie-ins, Disney novelisations). 1,348 new candidates.
- Open Library import: 1,187 imported, 159 not found, 1 needs review (2 h 17 min). Hardcover: 1,095 enriched, 158 created from Hardcover alone, 92 not found (1,457 requests). Fixed: the same book twice in one run duplicated tags.
- AI tagging (prompt v4, Batch API): 1,351 books, $1.43. 2 failed (Dark Intelligence, The Immortal Highlander). Accuracy on Joseph's labels unchanged: 19/20 present verdicts right; 19/53 found.
- Embeddings: 1,664 books (re-embedded the old ones too, since their tropes changed); 832 function calls.
- Audit: 1,784 works, 6.2 present tropes per book on average, 202 without a description, 99 with no present trope. 5 "likely duplicates" are false alarms from shared series prefixes ("Buffy the Vampire Slayer: ...", "Star Wars - ...") except "Vampire Academy: The Ultimate Guide" (a companion guide, not a novel).
- Problems found by test searches: children's picture books (Little Robots, Geeger the Robot Goes to School), non-fiction (21st Century Robot, Robot Haiku) and explicit adult titles got in. Next: a clean-up pass that flags children's, non-fiction and adult books (from Hardcover genres and AI), hides children's and non-fiction, and adds an adult-content flag.
- Total AI spend so far: about $3.40.
