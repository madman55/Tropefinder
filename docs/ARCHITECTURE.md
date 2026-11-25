# Architecture

## In one paragraph

Python jobs (the **collection engine** and the **trope engine**) fetch book data from sources whose terms allow it, classify each book against the trope list in `taxonomy/tropes.yaml`, and write everything, with evidence and sources, into a Supabase Postgres database. The **web app** (Phase 3) only reads that database using the public key. Only the Python jobs hold the secret key, and they get it from `.env` locally or GitHub Secrets in Actions.

```text
 Open Library ─┐
 Hardcover ────┼─► Collection engine (Python) ─┐
 Google Books ─┘   import · dedupe · series     │
  (optional)                                    ▼
                                         Supabase Postgres ◄──► Trope engine (Python)
                                         RLS: public read only    tags · rules · Claude Haiku
                                                ▲                 evidence for every verdict
                                                │ read-only
                                         Web app (Phase 3, React PWA)
```

## Components

| Component | Folder | Runs where | Writes to DB? |
| --- | --- | --- | --- |
| Collection engine | `pipeline/sources`, `normalise`, `dedupe`, `load` | Your computer, later GitHub Actions on a schedule | Yes (secret key) |
| Trope engine | `pipeline/classify`, `taxonomy/` | Same | Yes (secret key) |
| Web app | `web/` | Browser, hosted on GitHub Pages | No (public key, RLS) |

Each can be replaced without rebuilding the others, because they only share the database schema.

## Data model

Four groups of tables (full definitions in `supabase/migrations/`):

- **Catalogue:** `works` (one per story), `editions` (formats/ISBNs), `authors`, `work_authors`, `series`, `work_series`, `genres`, `work_genres`.
- **Provenance:** `data_sources`, `external_ids` (main duplicate guard), `format_availability`, `external_links`, `import_runs`.
- **Trope intelligence:** `tropes`, `trope_aliases`, `trope_relations`, `book_tropes` (current verdict), `classification_evidence`, `classification_runs`.
- **Users (Phase 4):** profiles, reading history, preferences, lists, community votes.

## Trust rules

1. A verdict is `present`, `absent` or `unknown`. No row means unknown, never absent.
2. `absent` needs explicit evidence or a human.
3. Every verdict records how it was made (`verification`: ai_suggested, rule_based, source_tag, human_verified, community_verified).
4. AI output must include a quote; the pipeline discards any quote not found word-for-word in the stored text.
5. The AI never supplies book facts (titles, authors, dates, formats).
6. Search never calls the AI; verdicts are stored.

## Duplicate prevention (in order)

1. Same source + same source ID → same record (`external_ids` unique constraint).
2. Same ISBN-13 → same edition (unique index).
3. Normalised title + author similarity above a strict threshold → flagged for review, never auto-merged.

## Security

- Row-level security on every table; public tables have a read-only policy.
- `import_runs` and `classification_runs` have no public access.
- Secrets only in `.env` (git-ignored) and GitHub Secrets.
