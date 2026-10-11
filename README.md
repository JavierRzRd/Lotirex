# Lotirex cPanel deployment

Download `lotirex-cpanel.zip` from this branch using GitHub’s **Download raw file** button.
The package includes the compiled website, Python runtime code, and a SQLite seed with 75,526 records.
No PHP or MySQL database is required. The package and its Passenger/cron deployment mode passed 124 tests.

See [the installation guide](docs/cpanel.md) for Python 3.12 application settings, dependency installation, Cron Jobs and backups.

Target domain: `lotirex.com`. Extract into the private `lotirex` application directory outside `public_html`.
The seed initializes an absent database; existing live results are never replaced.

Archive SHA-256: `674a567e2b4d30216a7b08d4684e93c6ed82bd895194fd921c609d68bbbac3ea`

FTP passwords and other credentials are not included.
