# Grapyth AI Gateway

Grapyth AI Gateway is a lightweight, self-hosted service that keeps AI provider
credentials, model policy, usage limits, and billing outside calling
applications. It currently ships with an OpenAI adapter. Provider-specific code
is isolated behind a small interface so additional providers can be added
without changing the public policy and accounting flow.

## Why this gateway

The reason to own this gateway instead of adopting a general-purpose AI proxy
is its accounting boundary: authentication, limit checks, provider execution,
and final cost recording follow one deliberately small request lifecycle. A
generic proxy would require custom extensions for this contract while adding a
larger platform to the trusted path.

The Gateway stores call status, token usage, and cost metadata. It does not
persist AI request or response bodies and disables provider-side response
storage. Calling applications may persist their own copies outside the Gateway.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --editable ".[test]"
$env:GRAPYTH_GATEWAY_ADMIN_PASSWORD='replace-with-a-long-password'
$env:GRAPYTH_GATEWAY_MASTER_KEY=(.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
.\.venv\Scripts\python.exe -m uvicorn gateway.server:create_app --factory --host 127.0.0.1 --port 8077 --no-access-log
```

Open `http://127.0.0.1:8077/admin`, authenticate as `admin`, add an OpenAI
credential in Providers, verify a Pricing plan, then create a client with an
explicit provider, plan, reasoning effort, and billing mode.

Provider keys are encrypted with the master key and never returned by the API.
The client token returned during creation or rotation is shown once; only its
SHA-256 hash and final six-character hint are stored.

The built-in `gpt-5.6-terra` plan uses OpenAI Standard short-context pricing:
$2.00 input, $0.20 cached input, $2.50 cache write, and $12.00 output per one
million tokens. The Gateway forces `service_tier=default` and rejects requests
above 272,000 input tokens so the enforced provider mode and context boundary
match that plan. Verify rates against the
[official OpenAI pricing page](https://developers.openai.com/api/docs/pricing)
before changing or publishing a pricing plan.

## Docker

Build the image from the standalone Gateway directory:

```powershell
docker build --tag grapyth-ai-gateway .
```

Create `.env` with `GRAPYTH_GATEWAY_ADMIN_PASSWORD` and
`GRAPYTH_GATEWAY_MASTER_KEY`, then create the persistent volume and container:

```powershell
docker volume create grapyth-ai-gateway-data
docker run `
  --detach `
  --name grapyth-ai-gateway `
  --restart unless-stopped `
  --env-file .env `
  --publish 127.0.0.1:8077:8077 `
  --volume grapyth-ai-gateway-data:/var/lib/grapyth-gateway `
  grapyth-ai-gateway
Invoke-RestMethod http://127.0.0.1:8077/ready
```

Stop or start the existing container:

```powershell
docker stop grapyth-ai-gateway
docker start grapyth-ai-gateway
```

## Gateway administration through SSH

The gateway admin UI remains bound to VPS loopback. From the operator computer, open:

```bash
ssh -N -L 18077:127.0.0.1:8077 operator@vps-address
```

Then browse to `http://127.0.0.1:18077/admin`. Basic authentication still applies inside the tunnel. Do not add an Nginx route for `/admin`.

Keep the runtime copy of `/etc/grapyth/gateway.env` on the VPS with mode `0600` when using the Gateway controller. A direct development checkout may instead use its ignored `deploy/env/gateway.env`. A password manager is not required on the VPS. Store the authoritative master key, gateway admin password, and recovery notes in KeePassXC on the operator computer.

The master key must be backed up separately from `gateway.db`. Losing it does not erase call, credit, or installation records, but encrypted provider credentials can no longer be decrypted and must be entered again. Treat a live owner setup link as a transient administrator credential and send it only to the intended owner. Before a public launch, rotate every secret that has appeared in logs, terminal output, screenshots, or chat transcripts.


## Production boundary

Run exactly one Uvicorn worker in one Gateway container or replica. Rate limits,
installation concurrency locks, and the global provider semaphore are
process-local; adding workers or replicas would weaken those controls.

Keep port 8077 on loopback or a private container network and expose the Gateway
only through a TLS-terminating reverse proxy. Do not publish `/admin`, `/ready`,
or the application port directly to the Internet. HTTP Basic authentication for
the admin interface is safe only behind TLS.

Restrict `.env` to the operator account and never commit it. Store the
authoritative master key in protected off-host storage, separately from database
backups. Losing the master key leaves accounting data intact but makes encrypted
provider credentials unrecoverable; changing it requires entering those
credentials again. The runtime cryptography boundary imports only Fernet; it
does not expose certificate or PKCS#7 parsing or decryption.

As of 2026-08-03, dependency scanners report
[`CVE-2026-69247`](https://github.com/pyca/cryptography/security/advisories/GHSA-g6cj-pr64-35w5)
for `cryptography==49.0.0`; its stable fix is not yet available. The issue is
limited to PKCS#7 decryption of attacker-controlled `EnvelopedData`, which the
Gateway does not import or expose. Reassess this exception before adding any
certificate or PKCS#7 feature, and upgrade to a stable fixed release when one is
published.

## Backup, restore, and rollback

Create a consistent live backup through the Gateway and verify it before copying
it to protected off-host storage. Do not copy live SQLite, WAL, and SHM files
independently.

```powershell
docker exec grapyth-ai-gateway python -m gateway.manage backup --target /var/lib/grapyth-gateway/backups/gateway-YYYYMMDD-HHMMSS.db
docker exec grapyth-ai-gateway python -m gateway.manage verify --source /var/lib/grapyth-gateway/backups/gateway-YYYYMMDD-HHMMSS.db
New-Item -ItemType Directory -Force .\offline-backups | Out-Null
docker cp grapyth-ai-gateway:/var/lib/grapyth-gateway/backups/gateway-YYYYMMDD-HHMMSS.db .\offline-backups\
```

Test restores on a temporary data volume. To restore the active volume, first
place the verified backup in its `backups` directory, stop the service, and use a
one-off container with the same volume:

```powershell
docker stop grapyth-ai-gateway
docker run --rm --volumes-from grapyth-ai-gateway grapyth-ai-gateway python -m gateway.manage restore --source /var/lib/grapyth-gateway/backups/gateway-YYYYMMDD-HHMMSS.db --confirm-service-stopped
docker start grapyth-ai-gateway
Invoke-RestMethod http://127.0.0.1:8077/ready
```

The restore command verifies the source and creates a `before-restore` safety
backup when an active database exists. Before every upgrade, preserve a verified
database backup, the matching image identifier, and the separately stored master
key. Older images reject newer migration history; roll back stored data by
stopping the Gateway, restoring the matching pre-upgrade backup, and then
starting the older image.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m pytest tests/e2e -q
```

The admin layout tests require a local Chrome or Edge installation.

## Data compatibility

An empty database is initialized automatically. Existing databases and backups
must contain a valid `schema_migrations` history. Known versioned schemas are
upgraded atomically; unversioned, modified, unknown, or newer schemas are
rejected without repair. Replace a disposable unversioned database or restore a
verified versioned backup. Upgrades correct only the exact untouched legacy
built-in pricing plan; customized plans and historical call pricing snapshots
remain unchanged.
