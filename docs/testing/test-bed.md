# The real-database test bed

Most tests run on SQLite, DuckDB and mocks. Anything that depends on how a
real server behaves (catalogs, quoting, permissions, read-only sessions,
timeouts) is tested against real servers instead: PostgreSQL, MySQL and SQL
Server, each loaded with a well-known sample database and a large generated
schema. One compose file, `testbed/docker-compose.yml`, defines all of it,
and the same file runs on your machine, on GitHub's runners in CI, and on one
small GCP VM started on demand.

The design is in [Phase 2, section 3](../architecture/phase2.md#3-the-real-database-test-bed).

| Server | Sample database | Generated wide schema |
|---|---|---|
| PostgreSQL 16 | Pagila, in database `pagila` | database `wide`, schemas `sales_01` ... `ops_20` and `audit` |
| MySQL 8.0 | Sakila, in database `sakila` | databases `wide_s01` ... `wide_s20` and `wide_audit` (MySQL's schemas are databases) |
| SQL Server 2022 | AdventureWorksLT, in database `AdventureWorksLT2022` | database `wide`, schemas `sales_01` ... `ops_20` and `audit` |

## Local use

You need Docker with the compose plugin.

```bash
python testbed/passwords.py > testbed/.env               # once: random passwords, ignored by version control
docker compose -f testbed/docker-compose.yml up -d --wait
set -a; . testbed/.env; set +a                           # the tests read the same variables
REALDB=compose pytest -m realdb
docker compose -f testbed/docker-compose.yml down        # add -v to drop the data
```

In PowerShell, load the variables with
`Get-Content testbed/.env | ForEach-Object { $name, $value = $_ -split '=', 2; Set-Item "env:$name" $value }`
and set `$env:REALDB = "compose"`.

The compose file has no default password, so no password is written in the
repository: `TESTBED_POSTGRES_PASSWORD`, `TESTBED_MYSQL_PASSWORD` and
`TESTBED_MSSQL_PASSWORD` are required, and `up` stops naming the variable if one is
missing. `testbed/passwords.py` prints three fresh random ones. Docker Compose
reads `testbed/.env` by itself, because it sits beside the compose file. A
server keeps the password its data volume was created with, so make a new
`.env` only together with `down -v`.

The first `up` downloads the samples, generates the wide schema and loads
everything, which takes a few minutes (SQL Server wants about 2 GB of
memory). Loading happens once per data volume, so a later `up` is quick.
`--wait` returns only when every server is loaded.

Install the drivers the tests use with
`pip install -e "packages/nl2sql[postgres,mysql,mssql,duckdb]"`. SQL Server
also needs Microsoft's ODBC Driver for SQL Server (17 or 18) on the machine;
the tests use the newest one installed.

## The compose file

| Service | Image | What it does |
|---|---|---|
| `samples` | `python:3.12-slim` | One-shot. Runs `testbed/samples.py`: downloads each sample from a pinned URL, checks its SHA-256, extracts Sakila from its archive, and writes the wide schema for the three servers with `testbed/wide_schema.py`, all into the `samples` volume. Skips what is already there. |
| `postgres` | `postgres:16` | Starts after `samples`. On its first start, `testbed/postgres/init.sh` (the image's own init mechanism) loads Pagila and the wide schema and creates the reader login. |
| `mysql` | `mysql:8.0` | The same with Sakila, through `testbed/mysql/init.sh`. The `mysql` client handles Sakila's `DELIMITER` blocks. |
| `mssql` | `mcr.microsoft.com/mssql/server:2022-latest` | The server. Its healthcheck passes only once `mssql-init` has finished. It has a 60-second `stop_grace_period`: after Docker's default 10 seconds a stop ends in `SIGKILL`, and the next start then recovers every database before any can be opened. |
| `mssql-init` | the same image, for its `sqlcmd` | Runs `testbed/mssql/init.sh`: waits up to five minutes until the server accepts logins and every sample database it already has is `ONLINE` in `sys.databases` (asked from `master`, since a database still in recovery cannot be opened), then restores AdventureWorksLT from the `.bak`, loads the wide schema and creates the reader login, skipping each database that already exists. Then it marks itself loaded for its healthcheck and idles instead of exiting. |

Every server has a healthcheck that passes only once it is loaded, so
`up --wait` is enough before running tests. `--wait` fails on an exited
container unless another service waits for it to complete, as the servers
wait for `samples`. Nothing waits for `mssql-init`, and on a later `up` it
skips everything and would exit at once, so it stays running instead; that is
what makes the same `up -d --wait` work on every start.

The file reads four variables:

| Variable | Default |
|---|---|
| `TESTBED_BIND` | `127.0.0.1` |
| `TESTBED_POSTGRES_PASSWORD` | none: required (`testbed/passwords.py`) |
| `TESTBED_MYSQL_PASSWORD` | none: required |
| `TESTBED_MSSQL_PASSWORD` | none: required |

| Service | Published port | Admin user | Sample database |
|---|---|---|---|
| `postgres` | 15432 | `postgres` | `pagila` |
| `mysql` | 13306 | `root` | `sakila` |
| `mssql` | 11433 | `sa` | `AdventureWorksLT2022` |

The ports avoid a clash with a database already installed locally.

Each server also has a login `nl2sql_reader`, with the server's password,
that may read its sample database and nothing else: not the wide schema.
Tests use it where a limited login matters.

## The sample databases and their licences

Nothing is committed to the repository; the `samples` service downloads each
file and refuses one whose SHA-256 differs from the pinned value, so we never
redistribute the samples. If one is ever vendored, its licence file goes
beside it.

| Sample | Files | Source | Licence |
|---|---|---|---|
| Pagila | `pagila-schema.sql`, `pagila-insert-data.sql` | github.com/devrimgunduz/pagila, pinned to commit `5ba5a57`, from before Pagila moved to PostgreSQL 18 | PostgreSQL License (copyright Devrim Gündüz) |
| Sakila | `sakila-schema.sql`, `sakila-data.sql`, from `sakila-db.tar.gz` | downloads.mysql.com | New BSD |
| AdventureWorksLT 2022 | `AdventureWorksLT2022.bak` | github.com/microsoft/sql-server-samples, release `adventureworks` | MIT |

The server images have their own terms. The SQL Server image runs the free
Developer edition, which needs `ACCEPT_EULA=Y` and is licensed for
development and testing, which is all we do with it.

When an upstream file changes, `samples` stops with the expected and actual
hashes. Check the new file, then pin its URL and hash in `testbed/samples.py`.

## The generated wide schema

`python testbed/wide_schema.py --dialect postgres|mysql|tsql --seed 7` writes
one SQL file. The same seed always gives the same schema:

| Feature | Count | What it tests |
|---|---|---|
| Schemas | 20 (`sales_01` ... `ops_20`), plus a small `audit` | Index scope, policy globs |
| Tables | 1,200 in the 20 schemas, with 5 to 30 columns each | Reflection and retrieval at scale |
| Views | 2 per schema | Views are indexed |
| Odd column types | boolean, uuid, json, binary, xml and array where the server has the type; the nearest plain type where it does not | Profiling never aborts |
| Comments | on about 30% of tables and columns | Descriptions come from the database |
| Foreign keys | within each schema | Join paths |
| Mixed-case names | 10 tables, such as `CustomerAccounts` | Quoting |
| Rows | 3 tables with 1,000,000 rows, 50 with 1,000, the rest empty | Row estimates, profiling limits |

The rows are made on the server (`generate_series` on PostgreSQL, a cross
join of digit tables on MySQL and SQL Server), so the file stays small.

Routing is measured on it too: `tests/realdb/test_routing_wide.py` indexes
Pagila and the whole wide schema side by side on PostgreSQL, and checks that
questions about films go to Pagila and questions about payroll runs or ledger
entries go to the wide schema, however many tables there are to vote
([the routing suite](../evals/cases.md#the-routing-suite)). Indexing the
wide schema takes about 17 minutes on CI's runner, so the test runs on
PostgreSQL only, and only when `REALDB_WIDE_ROUTING` is `true`: the workflow
sets it nightly, on demand, and on pull requests that change
`nl2sql/indexing/` (or the test itself). It skips otherwise.

## The `realdb` marker and fixtures

Every test under `packages/nl2sql/tests/realdb/` is marked `realdb`. Its
fixtures find the servers from the environment:

| Variable | Meaning |
|---|---|
| `REALDB=compose` | The compose servers on `127.0.0.1`, at the published ports, with the passwords in the three `TESTBED_*_PASSWORD` variables |
| `REALDB_POSTGRES`, `REALDB_MYSQL`, `REALDB_MSSQL` | A URL for one server, such as `postgresql://postgres:<password>@127.0.0.1:15432/`, with the password percent-encoded. It wins over `REALDB=compose`. |
| `REALDB_REQUIRED=1` | A missing server is a failure, not a skip |

Without a server, a `realdb` test skips with the fix in its message. Under
`REALDB_REQUIRED=1` it fails instead: a green run that skipped the real
databases proves nothing.

| Fixture or helper | What it gives |
|---|---|
| `server` | Each of the three servers in turn (`RealServer`: its admin URL, its sample database, `engine()` for a SQLAlchemy engine) |
| `datasource(server, database=None, **fields)` | A `Datasource` for one database on the server, the sample by default |
| `engine_on(database=None, **fields)` | An `NL2SQL` engine on one database of `server`, with a fake LLM and no key |

Test code may name dialects and use SQLAlchemy directly; the boundary tests
cover `packages/*/src`, not the tests.

`test_conformance.py` runs the adapter conformance kit
(`nl2sql_sdk.testing`) on each server. Its seed goes into schemas `kit_a`
and `kit_b` of the sample database (databases of those names on MySQL),
written with the admin login and dropped after each test. Its
`limited_login` is a login `nl2sql_kit` the test makes, whose password holds
`@ : / # %` and which may read `kit_a` only.

## CI: the `realdb` workflow

`.github/workflows/realdb.yml` runs the test bed on GitHub's runners. No
cloud credentials are involved.

| Job | Runs | What it does |
|---|---|---|
| `changes` | always | Works out whether a pull request touches database-facing code (the paths below). On the nightly and on-demand runs the answer is always yes. |
| `realdb` | on a pull request when `changes` says so; nightly on `main`; on demand (`workflow_dispatch`), which is run before every release | generates fresh passwords with `testbed/passwords.py` into the job's environment, masked in the log; `docker compose ... up -d --wait`, installs Microsoft's `msodbcsql18` for pyodbc and `packages/nl2sql[postgres,mysql,mssql,duckdb]`, then `pytest -m realdb` with `REALDB=compose` and `REALDB_REQUIRED=1` |
| `realdb-gate` | always, after the other two | Passes when `realdb` passed, or was skipped because nothing database-facing changed; fails otherwise. The decision is `testbed/gate.py`. This is the check to require. |

The paths that make `realdb` run on a pull request:

```text
packages/sdk/**
packages/nl2sql/src/nl2sql/adapters/**
packages/nl2sql/src/nl2sql/indexing/**
packages/nl2sql/src/nl2sql/storage/**
packages/nl2sql/src/nl2sql/schema/**
packages/nl2sql/tests/realdb/**
testbed/**
.github/workflows/realdb.yml
```

To run it by hand: Actions, "Real databases", "Run workflow", or
`gh workflow run realdb.yml --ref main`.

The unit job in `test.yml` runs `pytest -m "not integration and not realdb"`.

The scripts under `testbed/` have their own unit tests, which need no
database: `packages/nl2sql/tests/unit/test_testbed.py` (the wide schema, the
checksums, skipping what is there, and the gate),
`packages/nl2sql/tests/unit/test_realdb_fixtures.py` (the skip, the failure
under `REALDB_REQUIRED=1`, and the variables) and
`packages/nl2sql/tests/unit/test_testbed_vm.py` (`scripts/testbed.py`, against
a fake `gcloud` on `PATH`).

## The GCP VM

The same compose file also runs on one small VM, `nl2sql-testbed` in project
`nl2sql-testbed-261014`, zone `us-central1-a`. It is started for a session
and stopped after it. There are no GitHub-to-GCP credentials: the owner, or an
agent the owner asks, runs it by hand, at least once before each release.

| Setting | Value |
|---|---|
| Machine | `e2-standard-2` (2 vCPU, 8 GB: SQL Server alone wants 2 GB), Debian 12 |
| Docker | Installed with the compose plugin by the VM's startup script on its first boot |
| Disk | 30 GB standard persistent disk. It keeps the images and the data volumes while the VM is stopped, so a later session skips the load. |
| External IP | Ephemeral, for outbound traffic only (image pulls, sample downloads). It is released, and free, while the VM is stopped. |
| Network | Its own VPC, `nl2sql-testbed`, so none of the default network's open rules apply. One firewall rule admits TCP 22, 15432, 13306 and 11433 from IAP's range `35.235.240.0/20` only: no database port is public. |
| Access | IAP: `gcloud compute ssh` and `scp` with `--tunnel-through-iap`, and `gcloud compute start-iap-tunnel` for the three servers |
| Passwords | Secret Manager: `testbed-postgres-root-password`, `testbed-mysql-root-password` and `testbed-mssql-root-password` |

### `scripts/testbed.py`

You need the Google Cloud CLI, logged in (`gcloud auth login`) as an account
with the access listed below. The script is Python so that it runs the same on
Windows and Linux; it finds `gcloud` on `PATH`.

```bash
python scripts/testbed.py run -- pytest -m realdb    # the usual way: up, the command, down
python scripts/testbed.py up                         # or step by step
python scripts/testbed.py status
python scripts/testbed.py down
```

| Command | What it does |
|---|---|
| `up` | Reads the three passwords from Secret Manager into memory, starts the VM, copies `testbed/` to it (never a local `testbed/.env`), waits up to five minutes for Docker (the first boot installs it), runs `docker compose up -d --wait` there with `TESTBED_BIND=0.0.0.0`, then opens the three IAP tunnels in the background and waits until `localhost:15432`, `13306` and `11433` answer. The first session loads the samples, which takes several minutes. |
| `run -- <cmd>` | `up`, then `<cmd>` with `REALDB_POSTGRES`, `REALDB_MYSQL` and `REALDB_MSSQL` pointing at the tunnels, then `down` in a `finally`: also after Ctrl-C, a failing command or a failed `up`. It exits with the command's code. |
| `down` | Closes the tunnels it opened, runs `docker compose stop` on the VM so every server shuts down cleanly, then stops the VM. Stopping the VM alone kills SQL Server, and the next session then waits for its databases to recover. If the servers cannot be stopped within three minutes (the VM is unreachable or already stopped), it says so and stops the VM anyway. Safe to run again. |
| `status` | The VM's state, how long it has been running, and whether each tunnel answers. Never a password. |

`--project`, `--zone` and `--instance` override the defaults above.

The passwords reach the VM on the stdin of the `docker compose` command, so
they live only in that command's environment there, and reach `<cmd>` through
its own environment. They are never written to a file, put on a command line
or printed, and the script never changes its own environment. The tunnels'
process ids are kept in `nl2sql-testbed/tunnels.json` in the system's
temporary folder, so a later `down` can close them.

Every `gcloud compute ssh` and `scp` call passes `--strict-host-key-checking=no`
and `--tunnel-through-iap`. Right after a start, the first connection can fail
while the VM boots and its new SSH key propagates (plink says `Remote side
unexpectedly closed network connection`); the copy and the Docker wait retry
such a connection failure, and only that, up to five times about a minute in
all, then give up, and `run` still stops the VM.

On Windows, `gcloud` uses PuTTY's `plink` and `pscp`, and by default answers
their host-key question by feeding them `y` on stdin instead of passing the
caller's stdin on. The copy and the Docker wait keep that default, which
stores the VM's host key in PuTTY's cache; the `docker compose` step sets
`CLOUDSDK_SSH_PUTTY_FORCE_CONNECT=false` in `gcloud`'s environment only, so
the passwords on its stdin reach the VM. OpenSSH ignores the setting.

The script writes that stdin as bytes, one password per line ending in a bare
newline: in text mode Windows would end each line with a carriage return, and
the servers would be initialised with every password plus `\r` while the tests
use the password alone (MySQL then answers 1045 and SQL Server 18456). The
command on the VM also drops a trailing carriage return from each password it
reads. A server keeps the password its data volume was created with, so if
one was ever initialised with a wrong password, drop the volumes once while
the VM is up, then `down` and `up` again, which loads the samples afresh:

```bash
gcloud compute ssh nl2sql-testbed --project=nl2sql-testbed-261014 --zone=us-central1-a --tunnel-through-iap \
  --command="sudo TESTBED_POSTGRES_PASSWORD=unused TESTBED_MYSQL_PASSWORD=unused TESTBED_MSSQL_PASSWORD=unused docker compose -f testbed/docker-compose.yml down -v"
```

After `up` on its own, remember `down`: a running VM bills by the hour. If
the script is killed hard or the VM is started by hand, the nightly stop job
below stops it at 03:00 UTC. That stop does not stop the servers first, so
SQL Server is killed; the next `up` then waits while it recovers its
databases, which `mssql-init` allows for.

### Cost controls

| Control | Why |
|---|---|
| The VM is stopped whenever no session runs | A running VM bills by the hour |
| Ephemeral external IP only | Released, and free, while stopped |
| No public database port; IAP's range only | Nothing else can reach the servers |
| `run` always stops the VM in a `finally` | A failed test cannot leave it running |
| The nightly stop at 03:00 UTC | The backstop for everything above |
| A budget of $10 a month with alerts | The last line |

| Item | Approximate cost (check the billing report after the first month) |
|---|---|
| Stopped | the 30 GB disk: about $1 to $2 a month |
| Running | about $0.07 an hour |
| The scheduler job | free: one job is inside Cloud Scheduler's free tier |

### Access

The owner's account holds `roles/owner` on the project, which covers both the
set-up below and `scripts/testbed.py`. Anyone else who runs the script needs:

| Role | For |
|---|---|
| `roles/compute.instanceAdmin.v1` | starting, stopping and describing the VM, and the SSH key `gcloud compute ssh` adds to it |
| `roles/iap.tunnelResourceAccessor` | `--tunnel-through-iap` and `start-iap-tunnel` |
| `roles/secretmanager.secretAccessor` on the three secrets | the passwords |

### One-time set-up

Nothing in the repository creates cloud resources. The owner runs these once,
in a `bash` shell (Git Bash on Windows) logged in to `gcloud`. The three
secrets already exist.

```bash
PROJECT=nl2sql-testbed-261014
REGION=us-central1
ZONE=us-central1-a
VM=nl2sql-testbed
STOPPER=testbed-stopper@$PROJECT.iam.gserviceaccount.com

# 1. The APIs.
gcloud services enable compute.googleapis.com iap.googleapis.com cloudscheduler.googleapis.com \
    billingbudgets.googleapis.com --project=$PROJECT

# 2. A network of its own, so the default network's open SSH and RDP rules do not apply.
gcloud compute networks create nl2sql-testbed --subnet-mode=custom --project=$PROJECT
gcloud compute networks subnets create nl2sql-testbed --network=nl2sql-testbed \
    --region=$REGION --range=10.10.0.0/24 --project=$PROJECT

# 3. The one inbound rule: SSH and the three servers, from IAP's range only.
gcloud compute firewall-rules create nl2sql-testbed-allow-iap --network=nl2sql-testbed \
    --direction=INGRESS --action=ALLOW --rules=tcp:22,tcp:15432,tcp:13306,tcp:11433 \
    --source-ranges=35.235.240.0/20 --target-tags=nl2sql-testbed --project=$PROJECT

# 4. The VM: e2-standard-2, Debian 12, a 30 GB standard disk, an ephemeral external IP,
#    no service account. Its startup script installs Docker and the compose plugin once.
#    printf writes it with LF line endings, which an inline script typed in Git Bash on
#    Windows does not get: with CRLF the VM fails to run it (exit 127) and Docker never arrives.
printf '%s\n' '#!/bin/bash' \
    'command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh' > startup.sh
gcloud compute instances create $VM --project=$PROJECT --zone=$ZONE \
    --machine-type=e2-standard-2 --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=30GB --boot-disk-type=pd-standard \
    --network=nl2sql-testbed --subnet=nl2sql-testbed --tags=nl2sql-testbed \
    --no-service-account --no-scopes \
    --metadata-from-file=startup-script=startup.sh
gcloud compute instances stop $VM --zone=$ZONE --project=$PROJECT    # stopped until a session needs it

# 5. The nightly stop: a service account that may only stop this VM, and a scheduler job
#    that calls the Compute Engine API with its token at 03:00 UTC.
gcloud iam service-accounts create testbed-stopper --display-name="Stops the test-bed VM" --project=$PROJECT
gcloud iam roles create testbedStopper --project=$PROJECT --title="Test-bed stopper" \
    --permissions=compute.instances.stop --stage=GA
gcloud compute instances add-iam-policy-binding $VM --zone=$ZONE --project=$PROJECT \
    --member=serviceAccount:$STOPPER --role=projects/$PROJECT/roles/testbedStopper
gcloud scheduler jobs create http nl2sql-testbed-nightly-stop --project=$PROJECT --location=$REGION \
    --schedule="0 3 * * *" --time-zone=Etc/UTC --http-method=POST \
    --uri=https://compute.googleapis.com/compute/v1/projects/$PROJECT/zones/$ZONE/instances/$VM/stop \
    --oauth-service-account-email=$STOPPER \
    --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform

# 6. The budget: $10 a month, with alerts at 50%, 90% and 100%.
BILLING=$(gcloud billing projects describe $PROJECT --format='value(billingAccountName)')
gcloud billing budgets create --billing-account=${BILLING#billingAccounts/} \
    --display-name="nl2sql test bed" --budget-amount=10USD \
    --filter-projects=projects/$PROJECT \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
```

To check the set-up, run `python scripts/testbed.py run -- pytest -m realdb`.
To check the nightly job, start the VM with `python scripts/testbed.py up`,
run `gcloud scheduler jobs run nl2sql-testbed-nightly-stop --location=$REGION --project=$PROJECT`,
and `python scripts/testbed.py status` should then show it stopped; finish
with `python scripts/testbed.py down` to close the tunnels.
