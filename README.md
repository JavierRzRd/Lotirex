# Lotirex cPanel deployment

Download `lotirex-cpanel.zip` from this branch using GitHub’s **Download raw file** button.
The package includes the compiled website, Python runtime code, and a SQLite seed with 75,526 records.
No PHP or MySQL database is required. The package and its Passenger/cron deployment mode passed 125 tests.

For an existing cPanel upload that hangs after Passenger is enabled, replace
`backend/wsgi.py` with the copy on this branch. It initializes the ASGI event
loop after Passenger forks its worker process.

See [the installation guide](docs/cpanel.md) for Python 3.12 application settings, dependency installation, Cron Jobs and backups.

Target domain: `lotirex.com`. Extract into the private `lotirex` application directory outside `public_html`.
The seed initializes an absent database; existing live results are never replaced.

Archive SHA-256: `6d18020560371e0eb034afc4d171e8e4dfa5f82f6d18f7a1ad0f147d4b23d00e`

FTP passwords and other credentials are not included.
