# Data sources

Re-check each source's terms before building or changing its integration, and update the date here and in `supabase/seed.sql`.

| Source | Use | Terms (checked 2026-11-25) | Limits |
| --- | --- | --- | --- |
| [Open Library](https://openlibrary.org/developers/api) | Primary catalogue | Data dedicated to the public domain (CC0). Not to be used as a bulk or high-traffic backend; use [monthly dumps](https://openlibrary.org/developers/dumps) for bulk. Cache responses. | 1 req/s anonymous; 3 req/s with a User-Agent naming the app and a contact email |
| [Hardcover](https://docs.hardcover.app/api/getting-started/) | Genres, moods, tags, content warnings, series, format flags, missing descriptions; gap-filling for seed books | Confirmed 2026-11-26 on Hardcover's docs: aggregate data allowed if identified as Hardcover data; commercial products may not use user-owned data (libraries, reviews, journals, lists); publicly displayed API images need a DMCA takedown policy (we don't use images); must not be used to train public or commercial LLMs. Token must stay server-side. | Free: 60 req/min, 5,000/day, burst 10; search times out at 2 s |
| [Google Books](https://developers.google.com/books/terms) | Optional enrichment | Cannot charge users for an app using it without Google's permission. Must attribute Google and follow [branding guidelines](https://developers.google.com/books/branding). Remove content on request. | API key; Google Cloud quota |
| Manual | Gap-filling | Own data | — |
| Claude Haiku | Trope verdicts only | Paid API; output is our own classification | Batch API; spend limit set in console |

## Not used

- **Goodreads:** public API closed.
- **Amazon / Audible:** no open API; scraping breaches their terms.
- **StoryGraph:** no public API.
- **Royal Road:** no public API; not scraped.

## Display rules

- Show short description snippets with a link to the source rather than full publisher blurbs.
- Attribute the source on any page showing its data.
