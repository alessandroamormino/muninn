# Disaster Recovery Runbook

> Full-stack recovery from zero (BAK-05, D-09). The **default path below is fully
> local and offline** against the bundled MinIO — you can follow it end to end with
> **no cloud account**. The exact same steps target any real S3-compatible bucket
> (AWS, Backblaze, Wasabi, …) by changing only the endpoint + credentials.

A backup is a **bundle** (D-04/D-05): a Qdrant snapshot tied to the state set captured
at the same instant. Recovery means: bring the stack up clean → restore the collection
from the off-host snapshot → restore the state set → verify `/search`.

---

## What a backup contains (D-04)

| Part | Source | Restored by |
|------|--------|-------------|
| `snapshot.snapshot` | Qdrant collection snapshot | API restore **or** manual file copy into the `qdrant_snapshots` volume |
| `state.tar.gz` | `.sync/` (users.db, sync_logs.db, entity_state.json, sync_state.json, search_history.db) + `configuration/*.yaml` | **Manual** extraction (offline, orchestrator stopped) |
| `manifest.json` | bundle metadata (collection, snapshot_name, created_at, size_bytes, s3_keys) | reference only |

Bundle key prefix in the bucket: `backups/{collection}/{bundle_id}/`.

> **Security note:** bundles contain secrets at rest (`users.db` password hashes,
> connector credentials inside `configuration/*.yaml`). Encryption-at-rest of the
> bundle is **deferred to Phase 29 / GDPR-03**. Keep the bucket private.

---

## 0. Prerequisites

`.env` must contain (the `.env.example` defaults already target the bundled MinIO):

```bash
# Vector store + activate Qdrant AND the bundled MinIO together.
# NOTE: COMPOSE_PROFILES is read automatically from .env — never pass --profile on the CLI.
VECTOR_STORE_ENGINE=qdrant
VECTOR_STORE_URL=http://vector-db-qdrant:6333
COMPOSE_PROFILES=qdrant,backup

# Auth
JWT_SECRET=<python -c "import secrets; print(secrets.token_hex(32))">
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme

# Local MinIO root creds (dev defaults — change for any non-local use)
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin123

# S3 backup target — pointing at the bundled MinIO.
S3_BACKUP_ENDPOINT_URL=http://minio:9000
S3_BACKUP_BUCKET=smart-search-backups
S3_BACKUP_ACCESS_KEY=minioadmin
S3_BACKUP_SECRET_KEY=minioadmin123
S3_BACKUP_REGION=us-east-1
```

The bucket `smart-search-backups` is **auto-created by the S3 client on the first
backup** — no manual `mc mb` / `aws s3 mb` step is required.

> **Per-entity `backup:` block (required).** The S3 target is resolved from each
> entity's OWN `configuration/{Entity}/config.yaml`, not from a global singleton
> (the global `configuration/config.yaml` is itself just one more entity). An
> entity with no `backup:` block — or an empty bucket — cannot be backed up and
> the API returns `400`. Add this block to every entity you want to back up:
>
> ```yaml
> backup:
>   enabled: true
>   schedule: manual          # or a cron string for scheduled backups
>   keep_n: 7                 # retention: newest N bundles per collection
>   s3:
>     access_key: ${S3_BACKUP_ACCESS_KEY}
>     secret_key: ${S3_BACKUP_SECRET_KEY}
>     bucket: ${S3_BACKUP_BUCKET}
>     endpoint_url: ${S3_BACKUP_ENDPOINT_URL}
>     region: ${S3_BACKUP_REGION}
> ```
>
> Credentials stay as `${VAR}` and are resolved from `.env` at runtime (D-03).

> **After a restore, the query embedder must match the index embedder.** Restore
> brings back the document vectors *as-is* (no re-embedding). Semantic `/search`
> still embeds each **query** at runtime, and that must use the **same model** the
> documents were indexed with (same vector space + dimensions) — otherwise Qdrant
> compares incompatible vectors. Verify the restored entity's `embedding:` block
> (`type`/`model`) matches what produced the snapshot, and that the embedder
> (e.g. Ollama) is reachable.

> **Same procedure, real S3.** To recover against AWS S3 / Backblaze B2 / any
> S3-compatible bucket, change **only** these values: `S3_BACKUP_ENDPOINT_URL`
> (set it to the provider endpoint, or **unset it entirely for AWS S3**),
> `S3_BACKUP_ACCESS_KEY`, `S3_BACKUP_SECRET_KEY`, `S3_BACKUP_BUCKET`,
> `S3_BACKUP_REGION`. Every other step in this runbook is identical — to the code
> it is just a different `endpoint_url` (D-01). In the `aws s3` commands below,
> drop the `--endpoint-url $S3_BACKUP_ENDPOINT_URL` flag when targeting AWS.

The endpoint host differs by caller:
- **inside the compose network** (orchestrator → MinIO): `http://minio:9000`
- **from your host shell / AWS CLI**: `http://localhost:9000`
- **MinIO web console** (browser): `http://localhost:9001`

For the AWS CLI on the host, export the MinIO creds so `aws s3` can authenticate:

```bash
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin123
export AWS_DEFAULT_REGION=us-east-1
ENDPOINT=http://localhost:9000   # use the provider endpoint (or omit the flag) for real S3
```

---

## 1. Bring up a clean stack

```bash
docker compose up -d
```

`COMPOSE_PROFILES=qdrant,backup` in `.env` brings up Qdrant **and** MinIO alongside
the orchestrator and frontend automatically. **Do not pass `--profile` on the CLI.**

Confirm services are healthy:

```bash
docker compose ps
curl -s http://localhost:8000/health        # expect 200 {"status":"ok"}
```

Confirm the MinIO console is reachable at <http://localhost:9001> (log in with
`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`).

---

## 2. Identify the bundle to restore

List the bundles for the collection in the bucket and pick the latest `{bundle_id}`:

```bash
aws s3 ls --endpoint-url $ENDPOINT s3://$S3_BACKUP_BUCKET/backups/{collection}/
# e.g. backups/Products/
#   PRE 20260626-0300-a1b2c3/
#   PRE 20260627-0300-d4e5f6/   <- newest

aws s3 ls --endpoint-url $ENDPOINT s3://$S3_BACKUP_BUCKET/backups/{collection}/{bundle_id}/
#   snapshot.snapshot
#   state.tar.gz
#   manifest.json
```

(`--endpoint-url $ENDPOINT` is what points the AWS CLI at MinIO; **omit it for real
AWS S3**.) Inspect `manifest.json` to confirm `collection` and `snapshot_name`:

```bash
aws s3 cp --endpoint-url $ENDPOINT \
  s3://$S3_BACKUP_BUCKET/backups/{collection}/{bundle_id}/manifest.json - | cat
```

---

## 3. Restore the collection (Qdrant snapshot)

**Path A — preferred: via the API** (orchestrator is up; you have an admin token).

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN_USERNAME\",\"password\":\"$ADMIN_PASSWORD\"}" | jq -r .access_token)

curl -s -X POST \
  "http://localhost:8000/backup/{collection}/restore?bundle_id={bundle_id}&confirm=true" \
  -H "Authorization: Bearer $TOKEN"
# {"status":"started"}  — poll progress:
curl -s http://localhost:8000/backup/status -H "Authorization: Bearer $TOKEN"
# {"collection":"{collection}","phase":"restoring",...} -> ... "phase":"done"
```

`confirm=true` is **mandatory** — restore overwrites the live collection (D-08,
destructive). Without it the API returns `400`. (The Backup tab in the UI enforces the
same gate with a confirmation dialog.) The API downloads `snapshot.snapshot` from S3,
places it on the `qdrant_snapshots` volume, and calls Qdrant `recover_snapshot` —
**no re-embedding** occurs.

**Path B — manual fallback** (orchestrator/API unavailable; raw Qdrant + filesystem).

The Qdrant `recover_snapshot` location is interpreted **inside the Qdrant container**
as `file:///qdrant/snapshots/{collection}/{snapshot_name}`. Place the snapshot there
(it is the shared `qdrant_snapshots` volume), then call recover:

```bash
SNAP=$(aws s3 cp --endpoint-url $ENDPOINT \
  s3://$S3_BACKUP_BUCKET/backups/{collection}/{bundle_id}/manifest.json - | jq -r .snapshot_name)

# Download the snapshot bytes from the bundle to a local file
aws s3 cp --endpoint-url $ENDPOINT \
  s3://$S3_BACKUP_BUCKET/backups/{collection}/{bundle_id}/snapshot.snapshot ./$SNAP

# Copy it into the qdrant_snapshots volume at the path recover_snapshot expects
docker compose exec -T vector-db-qdrant mkdir -p /qdrant/snapshots/{collection}
docker compose cp ./$SNAP vector-db-qdrant:/qdrant/snapshots/{collection}/$SNAP

# Trigger the in-container recovery from the file:// location
curl -s -X PUT "http://localhost:6333/collections/{collection}/snapshots/recover" \
  -H 'Content-Type: application/json' \
  -d "{\"location\":\"file:///qdrant/snapshots/{collection}/$SNAP\"}"
# {"result":true,...}
```

---

## 4. Restore the state set (offline — the part the live API does NOT do)

The live restore endpoint **only** restores the Qdrant collection. The state set
(`.sync/` + `configuration/`) is restored manually here. This includes
`configuration/*.yaml`, which is **gitignored and exists only inside backups** — without
this step the collection has no config and the orchestrator cannot serve it.

> **Stop the orchestrator first.** `users.db`, `sync_logs.db`, `search_history.db`,
> `entity_state.json` and `sync_state.json` are open SQLite/JSON files held by the
> running orchestrator. Overwriting them under a live process corrupts the open
> handles. Stop the orchestrator, extract, then restart.

```bash
# 1. Stop ONLY the orchestrator (Qdrant + MinIO stay up)
docker compose stop orchestrator

# 2. Download + unpack the state tarball
aws s3 cp --endpoint-url $ENDPOINT \
  s3://$S3_BACKUP_BUCKET/backups/{collection}/{bundle_id}/state.tar.gz ./state.tar.gz
mkdir -p ./restore && tar -xzf ./state.tar.gz -C ./restore
ls -R ./restore        # expect .sync/* and configuration/*

# 3. Restore configuration/ (host bind mount ./configuration:/app/configuration)
cp -a ./restore/configuration/. ./configuration/

# 4. Restore .sync/ into the sync_data named volume.
#    The orchestrator is stopped, so copy via a throwaway helper container that
#    mounts the same volume:
docker run --rm -v smart-search_sync_data:/dest -v "$PWD/restore/.sync":/src:ro \
  alpine sh -c 'cp -a /src/. /dest/'
#    (Volume name is "<project>_sync_data" — check with: docker volume ls | grep sync_data)
```

> The `configuration/` directory uses a **host bind mount** (`./configuration`), so it
> is restored directly on the host. `.sync/` is a **named volume** (`sync_data`), so it
> is restored via a helper container mounting that volume.

---

## 5. Restart the orchestrator

```bash
docker compose start orchestrator
docker compose ps          # orchestrator -> healthy
```

---

## 6. Verify `/search` (success criteria 2 + 5)

```bash
# Health
curl -s http://localhost:8000/health                       # 200 {"status":"ok"}

# Collection has objects again (no re-embedding happened)
curl -s "http://localhost:8000/info?collection={collection}" \
  -H "Authorization: Bearer $TOKEN" | jq '.total_objects'   # > 0

# Semantic search returns relevant results
curl -s "http://localhost:8000/search?q=<a known query>&collection={collection}" \
  -H "Authorization: Bearer $TOKEN" | jq '.results | length'  # > 0
```

While restoring, watch the orchestrator logs (`docker compose logs -f orchestrator`):
there must be **no embedding calls** — the vectors come back with the snapshot, not from
re-embedding (success criterion 2). If `/search` returns relevant results on a stack that
started from wiped volumes, the DR drill is successful.

---

## Quick reference — full drill on a wiped stack

```bash
docker compose down -v          # destroy ALL volumes (incl. MinIO bundle — see note)
docker compose up -d            # clean stack (COMPOSE_PROFILES=qdrant,backup from .env)
# ... then steps 2 → 6 above against an off-host bundle.
```

> `docker compose down -v` also wipes `minio_data` — i.e. the local bundle itself. To
> rehearse recovery of an **existing** bundle, either bring the stack down **without**
> `-v` (keeps `minio_data`), or re-create a backup first, or target a **real S3 bucket**
> (whose contents survive a local `down -v`).
</content>
</invoke>
