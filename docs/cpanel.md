# Namecheap shared hosting with cPanel

Lotirex uses Python and SQLite. It does not require PHP or a MySQL database.
The deployment package includes the compiled frontend, so Node.js is not
needed on the hosting account.

On shared hosting, Passenger serves the website through the supplied WSGI
adapter. cPanel Cron Jobs runs the results updater separately, because web
processes can restart or stop when the site is idle. This deployment has been
tested locally; the account's Python App and network access still need to be
checked on Namecheap before the site goes live.

## Confirm the account supports these features

- **Setup Python App** in cPanel, preferably with **Python 3.12**, the version
  used for verification. The available versions depend on the account; do not
  select an older version without checking compatibility.
- **SSH or cPanel Terminal**, with permission to install packages into the
  application's virtual environment.
- **Cron Jobs** that can run every 15 minutes.
- Writable, persistent local storage for SQLite and backups.
- Outbound HTTPS access from Python to `www.loteriadominicana.com.do` and
  `loteriadominicana.com.do`, with working TLS certificate verification.
- A domain or subdomain, with HTTPS enabled through cPanel's SSL tools.

The current database is small, but reserve space for the Python environment,
backups, source audits and logs. Monitor the account's disk and process limits
as the history grows. If the account cannot run the Python application or the
updater, a VPS or another Python hosting service is needed.

## Build the upload package

This branch includes Python source and the already compiled frontend; frontend
source and an npm build are not present. To rebuild the deployment ZIP after a
backend change, use Python 3.12 with the runtime dependencies installed:

```bash
cd /workspace/Lotirex
/workspace/lotirex-runtime/.venv/bin/python scripts/package_cpanel.py --database /workspace/lotirex-runtime/data/lotirex.sqlite3
```

The output is `artifacts/lotirex-cpanel.zip`. The packager retains the compiled
frontend from the existing root ZIP, overlays the checked-in Python code, and
creates a consistent SQLite snapshot. It refuses a database containing foreign
games. The archive contains private database data; do not put it in public_html.

## Dominican-only upgrade and nightly updates

The app accepts Dominican Republic games only. New York, Florida, Mega Millions,
Powerball, Anguila, and King Lottery are excluded from ingestion and the catalog.
When an older database contains foreign games, startup first creates a consistent
private `backups/pre-dominican-only-*.sqlite3` snapshot and then removes those
games and their draws. Dominican history stays intact. Keep this backup private.

Upload the new ZIP into the same private application root and replace code and
`dist`, while preserving your live `data` and `backups` directories. Restart the
Python App so the migration and new code run. Check `/api/catalog`: all games
must have country `DO`. An existing database is never replaced by the seed. The packaged
`seed/domestic-results.json` is merged into it once per bundle on startup;
accepted results stay intact and conflicts are audited. This release includes
3,327 website results from March 11 through October 13, 2026, including five
available draws for October 13. Eight dates remain partial or unavailable;
see `/api/data-quality`. A bundled result is not a claim that every source
game or date was complete.

Use the Cron Jobs command below every 15 minutes. Python, rather than the server
clock, controls the first fetch of each date at **23:00 America/Santo_Domingo**
(UTC-4 all year). Other runs catch up earlier dates and retry incomplete results;
they do not fetch the current day before 23:00. Cron continues when Passenger is
idle or restarts, so do not enable the in-process scheduler in the Python App.

A VPS or cloud development environment can instead run a separate supervised
worker from the application root:

```bash
python -m backend.nightly
```

Do not run both the worker and cron for the same database. File locking protects
against overlap, but one scheduling method is sufficient. The worker is a live
process and must restart after machine restart; merely uploading it does not
register a cPanel Cron Job.

Conflicting source numbers are retained in `/api/data-quality` as
`conflicting_draw_for_date`; the accepted result is preserved and other results
continue importing. Invalid source numbers are also quarantined. A partial run
is not proof that every game has published results. Unreachable sources remain
pending, and TLS verification and robots.txt checks remain enabled.

For a manual inclusive backfill:

```bash
python -m backend.sync --from 2026-03-11 --to 2026-10-13 --max-days 400
```

## Create the Python application

The deployment target is `lotirex.com`. The examples below use cPanel username
`USER` and application directory `/home/USER/lotirex`. Replace `USER` with the
hosting account's cPanel username, which can differ from an FTP username.

1. Add `lotirex.com` under **Domains** in cPanel. Point its DNS to this hosting
   account using the nameservers or records supplied by Namecheap. Enable
   HTTPS after DNS resolves to the server.
2. In **Setup Python App**, create an application with Python 3.12 and
   production mode. Set the application root to `lotirex`, outside
   `public_html`. Set its application URL to `lotirex.com` at `/`.
3. Set the startup file to `passenger_wsgi.py` and the entry point to
   `application`.
4. Add these application environment variables:

   | Variable | Value |
   |---|---|
   | `LOTIREX_DB` | `/home/USER/lotirex/data/lotirex.sqlite3` |
   | `LOTIREX_SCHEDULER` | `0` |

5. Upload the archive using encrypted FTPS, SFTP or cPanel File Manager and extract its
   contents directly into `/home/USER/lotirex`. The startup file, `backend`,
   `dist` and `seed` must be immediately inside that directory. Remove the
   uploaded archive after extraction.

On first startup, the website or updater copies the packaged seed into
`data/lotirex.sqlite3` if the live database does not exist. An existing live
database is never overwritten. The seed is an installation snapshot; updates
and backups use the live file under `data`.

The live database, seed and backups must stay outside public web directories.
Passenger serves the built frontend and API; the application does not expose
a database download route. Do not create a public alias or symlink to `data`,
`seed` or backups.
This setup assumes the website is mounted at the domain's `/`, rather than
at a nested URL such as `/lotirex`.

## Install dependencies and start the website

If the homepage responds with `Build the frontend with npm run build`, the
running backend cannot find `dist/index.html` beside its `backend` directory.
The deployment ZIP already includes the compiled frontend; this branch does
not include frontend build source.

Extract `lotirex-frontend.zip` directly into the Python App's application root.
The required layout is:

```text
application-root/
  passenger_wsgi.py
  backend/app.py
  dist/index.html
  dist/assets/index-Cvd8BhtO.css
  dist/assets/index-DmRm6B0F.js
```

For the uploaded configuration, that root is `/home/pentuic/Lotirex`. The
frontend must be `/home/pentuic/Lotirex/dist/index.html`, rather than under
`public`, `public_html`, or another nested `Lotirex` directory. Restart the
Python App after extraction so its asset mount is registered.

cPanel displays a command for activating this application's virtual
environment. Use that exact command in SSH or Terminal, then run:

```bash
cd /home/USER/lotirex
python -m pip install --disable-pip-version-check -r requirements-cpanel.txt
mkdir -p data logs backups
chmod 700 data seed backups
chmod 600 seed/lotirex.sqlite3
```

Use cPanel's application restart control. If Passenger needs an explicit
restart marker, create it in the application directory:

```bash
mkdir -p tmp
touch tmp/restart.txt
```

Open the HTTPS website. Check `/api/health` for status `ok` and a positive draw
count, then verify company and game selection, statistics and recent results.
`scheduler: false` is expected on shared hosting: Cron Jobs performs updates.
Use `/api/data-quality` to review pending dates and preserved source issues.
Once the first startup has created the live database, restrict its permissions:

```bash
chmod 600 data/lotirex.sqlite3
```

If the application fails to start, inspect cPanel's Python application error
log before changing the app. Confirm that the selected Python interpreter has
the dependencies installed, the startup file and callable match, and the
application user can write to `data`.

## Add the results updater to Cron Jobs

Find the absolute Python executable inside the virtual environment created by
cPanel. It is shown in the activation command's directory. For example, an
activation command mentioning
`/home/USER/virtualenv/lotirex/3.12/bin/activate` corresponds to
`/home/USER/virtualenv/lotirex/3.12/bin/python`. The actual path may differ.

First run the updater manually with that interpreter:

```bash
cd /home/USER/lotirex
/home/USER/virtualenv/lotirex/3.12/bin/python -m backend.cron --max-days 7
```

In **Cron Jobs**, select every 15 minutes (`*/15 * * * *`) and use this
command, substituting the actual absolute paths:

```bash
cd /home/USER/lotirex && /home/USER/virtualenv/lotirex/3.12/bin/python -m backend.cron --max-days 7 >> /home/USER/lotirex/logs/update.log 2>&1
```

These commands use the default database at
`/home/USER/lotirex/data/lotirex.sqlite3`. If you choose another database path,
set `LOTIREX_DB` for the cron and backup commands too; Python App environment
variables are not automatically inherited by Cron Jobs.

The cron frequency follows the server's timezone, but Python chooses result
dates using `America/Santo_Domingo`. The current day's first scheduled fetch
is due at or after **23:00 Dominican time** (UTC−4). The following runs collect
late results, including US draws after midnight, and retry unfinished dates.
There is no need to change the hosting server's timezone or set `CRON_TZ`.

The updater gives recent dates priority, limits each run to seven dates, and
uses the same file lock as manual backfills. Overlapping jobs do not ingest
the same batch concurrently. Already accepted results remain unchanged;
invalid source results are held for review. A partial run can mean that some
results have not yet been posted, so inspect the log and data-quality endpoint
instead of treating every partial run as an installation failure.
The command exits with status `0` for a successful, partial or already-busy
run, `1` for a source/update failure, and `2` for invalid arguments. It writes
its status as JSON to the log.

After cron has run, confirm the log has a recent entry and the website's last
update status advances. Configure cPanel cron email to an address you monitor
if desired, and rotate or trim `logs/update.log` periodically so it does not
fill the hosting account.

## Backups and later releases

Back up the database using SQLite's backup API while the application is
running. Copying only `lotirex.sqlite3` can miss committed changes still held
in its WAL file. The supplied backup command creates a consistent snapshot
and never overwrites an existing backup:

```bash
cd /home/USER/lotirex
/home/USER/virtualenv/lotirex/3.12/bin/python -m backend.backup
```

By default, it writes a UTC timestamped file into the application's `backups`
directory. For a specific destination, supply a new filename:

```bash
/home/USER/virtualenv/lotirex/3.12/bin/python -m backend.backup --output /home/USER/lotirex/backups/pre-release.sqlite3
```

Add a daily backup Cron Job at a convenient server-local time, for example
`30 3 * * *`, with this command:

```bash
cd /home/USER/lotirex && /home/USER/virtualenv/lotirex/3.12/bin/python -m backend.backup >> /home/USER/lotirex/logs/backup.log 2>&1
```

Keep several dated backups outside `public_html`, delete older copies as
appropriate for the account's disk quota, and retain an off-account copy.
Test restoration to a separate file before relying on a backup. Backup files
are private data, so restrict their permissions as well as the directory's.

For later code releases, back up the live database, upload the updated code
and `dist`, install any changed dependencies, and restart the Python app.
Preserve the live `data` directory. New packages place their database snapshot
under `seed`, so uploading one does not replace live results. Do not manually
copy the seed over the live database, reimport the workbook or reset the
database as a routine deployment step.

Uploading this package does not configure DNS, create the cPanel application,
or publish the website automatically. Deployment needs the chosen hostname
and secure hosting access; the hosting-account checks above must pass first.

## Catalog errors and missing historical draws

The catalog and data-quality routes now use Python to inspect JSON, so they work
with SQLite builds that lack JSON1 functions. Extract the updated package into
the existing application root and restart the Python App. If `/api/catalog`
still fails while `/api/health` works, inspect the latest Python traceback in
`stderr.log`; an HTTP 500 alone does not confirm the underlying hosting error.

A health count of 3,327 indicates only the small website-results bundle is
visible; it does not include the full history. Check `LOTIREX_DB` in Python App
settings against the intended live path, `/home/pentuic/Lotirex/data/lotirex.sqlite3`
for the uploaded configuration. Do not overwrite or delete any existing DB.

If that intended database is missing historical records, validate the packaged
seed and inspect the target without changing it:

```bash
cd /home/pentuic/Lotirex
/home/pentuic/virtualenv/Lotirex/3.12/bin/python -m backend.restore_history --dry-run
```

Ensure the reported database path matches Python App's `LOTIREX_DB`. If Python
App uses a custom path, set the same `LOTIREX_DB` for the terminal and cron too.
Then merge the missing history with an automatic backup:

```bash
/home/pentuic/virtualenv/Lotirex/3.12/bin/python -m backend.restore_history
```

The command validates the seed first, creates `backups/pre-history-merge-*.sqlite3`,
inserts missing Dominican draws, and audits conflicts without replacing accepted
numbers. Repeating the merge does not duplicate draws. Restart the Python App
and recheck health, catalog, and the website.
