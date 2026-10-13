# Lotirex Dominican-only cPanel deployment

Download `lotirex-cpanel.zip` from this branch using GitHub’s **Download raw file** button.
The package includes the compiled website, Python runtime code, and a SQLite seed with 63,292 Dominican draws across 18 games.
No PHP or MySQL database is required. Nine regression tests and real Passenger-adapter HTTP checks passed for this release.

Foreign games are rejected and removed from older databases after a private backup.
The packaged website-results bundle merges into existing installations without
replacing accepted draws. Conflicts and malformed source results remain visible
in `/api/data-quality`. It includes available results through October 13, 2026;
eight dates remain partial or unavailable, so the backfill is not complete.

Automatic scraping starts at **23:00 America/Santo_Domingo** (UTC-4) each night.
On cPanel, register the documented Cron Job every 15 minutes; Python controls
the daily start time and retries pending results. Uploading files alone does
not activate Cron Jobs. On a supervised server, use `python -m backend.nightly`.

The Python source and regression tests are included in this branch. Run tests
with `python -m unittest discover -s tests -v` using the installed Python 3.12
environment. The frontend is precompiled; its build source is not included.

For an existing cPanel upload that hangs after Passenger is enabled, replace
`backend/wsgi.py` with the copy on this branch. It initializes the ASGI event
loop after Passenger forks its worker process.

See [the installation guide](docs/cpanel.md) for Python 3.12 application settings, dependency installation, Cron Jobs and backups.

Target domain: `lotirex.com`. Extract into the private `lotirex` application directory outside `public_html`.
The seed initializes an absent database; existing live results are never replaced.

Archive SHA-256: `e3c22b11507dc2c9d0191e6a88211e6066fa2b8a5834b39e5f09a030aea4ba35`

FTP passwords and other credentials are not included.
