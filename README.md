# TropeFinder

**Find stories. Not just books.**

TropeFinder is a book discovery platform that finds books by the story elements inside them (tropes, character types, settings, plot devices) rather than broad genres. You can require, prefer and exclude tropes, and every match shows the evidence behind it.

It is a discovery tool, not a shop: it links out to retailers and libraries.

## Status

**Phase 1 complete (26 Nov 2026).** 438 books in the database, from Open Library and Hardcover, classified against 79 tropes with quoted evidence. "Present" verdicts agree with hand labels 95% of the time. See [docs/TASKS.md](docs/TASKS.md) for results, known issues and what is next.

### Pipeline (all run from the Actions tab)

| Workflow | What it does |
| --- | --- |
| discover books | finds candidate books, commits `data/candidates.csv` for review |
| import open library | imports a list (dry run first) |
| enrich hardcover | adds tags, formats, series, missing descriptions; creates books Open Library lacks; `recheck` removes bad links |
| sync taxonomy | loads `taxonomy/tropes.yaml` (runs automatically when it changes) |
| classify tropes | tags + Claude Haiku with quote checks; scores against `data/gold_labels.csv` |
| audit catalogue | read-only health check |

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
