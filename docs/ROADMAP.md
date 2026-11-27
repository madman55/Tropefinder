# Roadmap

## Phase 1 — Database foundation (current)

Each stage has a pass condition; we don't move on until it's met and recorded in [TASKS.md](TASKS.md).

| Stage | Work | Pass condition |
| --- | --- | --- |
| 1 | Repo, docs, Supabase project, schema | Migration builds all tables on an empty database; smoke test passes on Supabase |
| 2 | Load taxonomy into the database | Running the sync twice changes nothing the second time |
| 3 | Import 30 seed books from Open Library | Every title imported or logged "not found"; a second run adds zero rows |
| 4 | Enrich from Hardcover | Report of books gaining tags and tags mapped to tropes |
| 5 | Hand-label gold set | ~20 books × 10 tropes labelled by Joseph |
| 6 | Classify (tags, rules, Claude Haiku) | ≥ 85% of "present" verdicts agree with gold labels; zero unverifiable quotes stored; unknown rate reported |
| 7 | Scale to 50, then 500 | Hand check of 50 random books finds no duplicate works; size, run time and AI cost recorded |

## Phase 2 — Search engine, with a simple search page

Trope search, include/exclude filters, mandatory vs optional requirements, transparent ranking with confirmed / possible / unverified bands, accuracy testing, natural-language search: meaning search with Supabase's built-in gte-small embeddings and pgvector (done 27 Nov 2026; suggestions and a labelled "Similar in feel" list, never counted as confirmed).

Decided 26 Nov 2026: a plain, working search page is built alongside the engine (not left to Phase 3), so Joseph can try real searches on his phone and spot bad results early. It uses only the public key and row-level security. Phase 3 then turns it into the polished, installable app.

## Phase 3 — User interface (polish the Phase 2 page into the full app)

Mobile-first React + TypeScript PWA on GitHub Pages: discovery and search pages, book pages with "why this matches", favourites, reading history, installable.

## Phase 4 — Community and expansion

User accounts, community trope verification, larger automated collection, personalised recommendations, new-release alerts.

## Phase 5 — Scaling and monetisation

More genres, better classification, clearly labelled commercial integrations (affiliate links, premium features), performance and cost tuning.
