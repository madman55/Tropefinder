# TropeFinder

**Find stories. Not just books.**

TropeFinder is a book discovery platform that finds books by the story elements inside them (tropes, character types, settings, plot devices) rather than broad genres. You can require, prefer and exclude tropes, and every match shows the evidence behind it.

It is a discovery tool, not a shop: it links out to retailers and libraries.

## Status

**Phase 1, Stage 1 (foundation).** The database schema, trope taxonomy and project docs exist and pass local tests. No books have been imported yet. See [docs/TASKS.md](docs/TASKS.md) for exactly what is done and what is next.

## How it fits together

| Folder | What it is |
| --- | --- |
| `pipeline/` | Python: imports book data and classifies tropes (the collection and trope engines) |
| `taxonomy/tropes.yaml` | The trope list, edited by hand |
| `supabase/` | Database schema (migrations), reference data (seed) and schema tests |
| `data/` | The proof-of-concept book list and your hand labels |
| `web/` | The React app (Phase 3; empty for now) |
| `docs/` | Architecture, roadmap, setup, tasks, data sources, decisions |

Only the Python jobs write to the database. The app will only read it, using the public key, with row-level security on every table.

## Quick start

Full instructions: [docs/SETUP.md](docs/SETUP.md).

```bash
pip install -r requirements.txt
python -m pipeline.taxonomy   # check the trope list
python -m pytest              # run the tests
```

## Principles

- Never invent book information. Every fact records its source.
- An absent trope in a description is not proof it is absent from the book. Unknown is a valid answer.
- AI suggestions are labelled as AI suggestions until a person verifies them.
- No secrets in the repository. Ever.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Setup](docs/SETUP.md)
- [Tasks](docs/TASKS.md)
- [Data sources and their terms](docs/DATA_SOURCES.md)
- [Decisions](docs/decisions/)
