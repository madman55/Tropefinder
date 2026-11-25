# Setup

Do these on your computer, in order. Each step says how to check it worked. Steps 6 and 7 can wait until those stages begin.

Time needed: about 45 minutes. Cost: £0.

## 1. Put the code on GitHub (private)

1. On github.com, click **New repository**. Name it `tropefinder`, choose **Private**, and do **not** add a README (we have one).
2. Install [GitHub Desktop](https://desktop.github.com/) if you don't have it. It handles hidden files like `.gitignore` that the website upload can miss.
3. In GitHub Desktop: **File → Add local repository**, choose the unzipped `tropefinder` folder, and accept the offer to create a repository there. Commit everything with the message `Stage 1: foundation`, then **Publish repository** (keep "private" ticked).

**Check:** the repo page on GitHub shows `README.md`, `docs/`, `pipeline/`, `supabase/` and a `.github` folder. There must be **no** `.env` file.

## 2. Install Python and run the tests

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/). On Windows, tick **Add Python to PATH**.
2. Open a terminal in the `tropefinder` folder and run:

```bash
pip install -r requirements.txt
python -m pipeline.taxonomy
python -m pytest
```

**Check:** the first command ends with `No problems found.` and the second with `7 passed`.

## 3. Create the Supabase project

1. Sign up at [supabase.com](https://supabase.com) and create a new project on the **Free** plan.
2. Name: `tropefinder`. Region: **London (eu-west-2)**. Let it generate a database password and save it in a password manager.
3. Wait for the project to finish setting up (a minute or two).

## 4. Build the database

In the Supabase dashboard, open **SQL Editor → New query**, then:

Run each file in its own new query, in this order. On a phone, open the file on GitHub, tap **Raw**, then **Select all → Copy** (the small copy icon copies the file *name*, not its contents).

1. `supabase/migrations/20261125000000_initial_schema.sql` → **Run**. If Supabase warns about Row Level Security, choose **Run and enable RLS** (the file enables it anyway). Expect `Success. No rows returned`.
2. `supabase/migrations/20261125010000_grant_public_read.sql` → **Run**. Expect `Success`.
3. `supabase/seed.sql` → **Run**.
4. `supabase/tests/schema_smoke_test.sql` → **Run**. Supabase warns about "destructive operations": choose **Run query**. The test only touches rows it creates and undoes everything at the end.

**Check:** the last result shows `ALL SCHEMA CHECKS PASSED`. The test leaves no data behind. If it shows an error instead, copy the message and send it to Claude.

Also check **Table Editor**: you should see 19 tables, and `data_sources` should have 7 rows.

## 5. Create your local `.env` file

1. Copy `.env.example` to a new file called `.env` in the same folder.
2. From Supabase **Project Settings → API**, copy the Project URL into `SUPABASE_URL` and the **secret** (service_role) key into `SUPABASE_SECRET_KEY`.
3. Put your own email in `OPENLIBRARY_USER_AGENT`.

**Check:** GitHub Desktop must **not** list `.env` as a change. If it does, stop and tell Claude; something is wrong with `.gitignore`.

## 5b. GitHub repository secrets (for jobs that run on GitHub)

On GitHub: repo **Settings → Secrets and variables → Actions → New repository secret**. Add `SUPABASE_URL` (the Project URL) and `SUPABASE_SECRET_KEY` (Supabase **Project Settings → API Keys → Secret keys**; or the legacy service_role key). Later stages add `HARDCOVER_TOKEN` and `ANTHROPIC_API_KEY` the same way.

To load the trope list: **Actions → sync taxonomy → Run workflow**. Tick *Dry run* first to preview.

## 6. Hardcover token (before Stage 4)

1. Make a free account at [hardcover.app](https://hardcover.app).
2. Go to **Settings → API** and copy the token into `HARDCOVER_TOKEN` in `.env`.
3. Read Hardcover's API rules page and confirm the terms in `docs/DATA_SOURCES.md` are still right.

## 7. Anthropic API key (before Stage 6)

1. Sign in at [console.anthropic.com](https://console.anthropic.com), add about $5 of credit, and set a monthly spend limit (e.g. $5).
2. Create an API key and put it in `ANTHROPIC_API_KEY` in `.env`.

## Never do this

- Never paste a secret key into a file that gets committed, a chat, a screenshot or an issue.
- Never put the secret Supabase key in the web app. The app will only ever use the public (anon / publishable) key.
- If a key leaks, rotate it in the provider's dashboard immediately.
