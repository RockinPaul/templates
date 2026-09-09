# Railway Templates Guide

How to turn an open-source project into a one-click Railway marketplace template, written down after building and publishing eight of them in September 2026: **cognee**, **Multica**, **projectmem**, **gortex**, **Observal**, **WeKnora**, **EverOS** and **Notesnook**. Everything here was learned the hard way on real deployments; the "gotcha" sections are the most valuable part. The guide is written for a developer or an agent who has never touched Railway templates and has to repeat this end to end.

Related repos (all under `github.com/RockinPaul`): `cognee_railway_template`, `multica_railway_template`, `projectmem_railway_template`, `gortex_railway_template`, `observal_railway_template`, `weknora_railway_template`. Read one of the last two for a current, complete example.

---

## 1. The pattern in one page

A Railway template is **not** a file in a repo. It is an object in the Railway workspace (the "template definition") that lists services, their sources, variables, volumes, healthchecks and domains. The repo only supplies what the services build from.

The approach that worked every time:

1. **Thin wrapper repo.** One public GitHub repo per template. It contains one Dockerfile per repo-built service, each `FROM <upstream image>:<pinned tag>`, plus small entrypoints and config overrides that adapt upstream to Railway. No fork of upstream code. Upgrading is a one-line tag bump.
2. **Reference project.** A normal Railway project where the services run from that repo (and from stock images for databases). It is the living proof that the stack works, and the source from which the template definition is generated.
3. **Generated template.** `railway templates create --project <id>` turns the reference project into a template definition. It captures sources, healthchecks, volumes, domains and *variable references*, but **no literal variable values**. Those are typed into the dashboard composer by a human (there is no API for it).
4. **Publish.** `railway templates publish <id> --category ... --description ... --readme-file TEMPLATE_OVERVIEW.md --image <url>`. The marketplace code becomes the slug of the template **name**, so rename it in the dashboard before publishing.
5. **Verify the published thing.** `railway deploy -t <code>` into a scratch project, exercise it like a user, delete the scratch project.
6. **Write it down.** README, TEMPLATE_OVERVIEW, CHANGELOG in the repo; memory notes; cognee dataset.

Never publish without steps 2 and 5. Half of the bugs below were only visible on Railway, the other half only locally.

---

## 2. Toolchain and access

- `railway` CLI (v4+): `railway login`, `railway init -n <name>`, `railway add`, `railway variables`, `railway volume`, `railway domain`, `railway up`, `railway deploy -t`, `railway logs`, `railway ssh`, `railway templates create|publish`, and the escape hatch `railway api '<graphql>'`.
- `gh` CLI for repos (`gh repo create`, `gh api`).
- Docker (OrbStack on macOS: `open -a OrbStack` and wait if the daemon died between turns).
- A **linked directory per project**: `railway init` or `railway link` writes the project link into the current directory. Keep one scratch directory per reference project and run `railway variables/logs/ssh` from there. Running them elsewhere fails with unhelpful JSON parse errors.
- The Railway **GitHub App** must have access to the template repos (all-repos access is simplest). Even so, a freshly created repo may be unknown to Railway for up to ~40 minutes (see gotchas).

Useful GraphQL calls (all via `railway api '...'`):

| Purpose | Call |
|---|---|
| List services, deployments, domains | `query { project(id:"…") { services { edges { node { id name serviceInstances { edges { node { latestDeployment { id status createdAt } domains { serviceDomains { domain } } source { image repo } } } } } } } volumes { edges { node { id name volumeInstances { edges { node { mountPath serviceId } } } } } } environments { edges { node { id name } } } } }` |
| Connect a service to a repo | `mutation { serviceConnect(id:"<serviceId>", input:{ repo:"Owner/repo", branch:"main" }) { id name } }` |
| Create a volume | `mutation { volumeCreate(input:{ projectId, environmentId, serviceId, mountPath:"/data" }) { id name } }` |
| Delete a volume | `mutation { volumeDelete(volumeId:"…") }` |
| Healthcheck, start command, restart policy | `mutation { serviceInstanceUpdate(serviceId, environmentId, input:{ healthcheckPath:"/health", healthcheckTimeout:600, startCommand:"…" }) }` |
| Redeploy a service without a code change | `mutation { serviceInstanceDeployV2(serviceId, environmentId) }` |
| Delete a variable | `mutation { variableDelete(input:{ projectId, environmentId, serviceId, name:"KEY" }) }` |
| Read a template definition | `query { template(id:"…") { name code status serializedConfig } }` (also `template(code:"…")`) |
| Deployment logs | `query { deploymentLogs(deploymentId:"…", limit:400) { timestamp message severity } }` |
| Delete a project | `mutation { projectDelete(id:"…") }` |

There is **no** mutation to rename a template or to edit its variables; `TemplatePublishInput` only has `category, demoProjectId, description, image, readme, workspaceId`.

---

## 3. Workflow, step by step

### 3.1 Investigate upstream (before promising anything)

Clone upstream shallowly into a scratch directory and answer these, in writing:

- **Images.** Does upstream publish container images (GHCR/Docker Hub)? Which tags (`vX.Y.Z` preferred over `latest`)? Which architectures? `docker manifest inspect <image>` shows both. Compressed size per image (`docker manifest inspect -v`, sum `layers[].size` of the amd64 entry). No image → you build one from a release binary or PyPI (gortex, projectmem).
- **Services.** Read `docker-compose*.yml` with a script that prints per service: image, ports, volumes, depends_on, healthcheck, env keys, profiles. Decide the minimum viable set. Optional profiles become "not included in v1".
- **Shared volumes** between services are a red flag: Railway volumes attach to exactly one service. Check whether the data really needs to be shared (Observal's Data Migration feature does; WeKnora's docreader turned out to send images inline over gRPC in v0.8, so it did not).
- **Databases.** Which extensions do migrations need? `grep -rhi "CREATE EXTENSION" migrations` decides between Railway's Postgres image, pgvector, or a specialised image (ParadeDB for `pg_search`).
- **Configuration surface.** `.env.example` sections; what is mandatory; which secrets and their length rules (grep the code for `len(` checks, e.g. `SYSTEM_AES_KEY` must be exactly 32 bytes in WeKnora; `SECRET_KEY` ≥ 32 in Observal).
- **First admin.** How does the first user get created? Patterns seen: open registration + bootstrap env var promoting an email at startup (WeKnora), seeded demo accounts (Observal), loopback-only bootstrap endpoints that cannot work behind a proxy (Observal `/auth/bootstrap`), Lite-only auto-setup (WeKnora), bearer token generated by the template (gortex, projectmem), login codes printed to stdout (Multica).
- **Networking.** Where does each process bind (`0.0.0.0`, `::`, `[::]`, empty)? Does the frontend proxy the API (then only the frontend needs a public domain)? Any hardcoded hostnames or ports in built assets (Observal's web bundle hardcoded `hostname:8000` for websockets)?
- **Entrypoints.** What do upstream entrypoints do (chown, gosu, migrations, stamping)? Reuse them; wrap, don't replace.
- **License** (must allow redistribution; all eight were MIT/Apache-2.0/AGPL-3.0), **release cadence** (daily releases mean frequent tag bumps), **resource needs** (Helm `values.yaml` limits are a good source).
- **Version skew.** Always read config files from the **release tag you pin**, not from `main` (`git fetch --depth 1 origin tag vX.Y.Z; git show vX.Y.Z:path`). WeKnora's main referenced an nginx `log_format` that the v0.8.0 image did not ship.

Write the result as a short feasibility note with a services table, risks to verify in rehearsal, and the open decisions.

### 3.2 Decide with the user

Typical decisions that need a human: which optional components to include, which upstream repo to index/demo by default, whether an internal service gets a public domain (usually no), how the first admin is created, template name, cover image. Ask them once, together, before building.

### 3.3 Repo layout

```
<name>_railway_template/
  Dockerfile.<service>        one per repo-built service, FROM upstream:<tag>
  <service>-entrypoint.sh     thin wrapper: validate env, adapt, exec upstream entrypoint
  web/                        nginx/Caddy overrides for the UI service
  README.md                   for people reading the repo (first login, variables, how it fits, upgrading)
  TEMPLATE_OVERVIEW.md        the marketplace page (Railway's expected structure, see §6)
  CHANGELOG.md                what changed, how to upgrade an existing deployment
  .gitignore, .dockerignore
```

Give every repo-built service its own Dockerfile even when it is a one-liner (`FROM image:tag`), so all pins are bumped in git and every deployer is notified through Railway's repo-update flow. Railway selects the file through the `RAILWAY_DOCKERFILE_PATH` variable on the service.

### 3.4 Writing the wrappers

Principles that held up:

- **Reuse upstream's entrypoint.** Wrap it: validate variables, fix the bind address, wait for the database if the app crashes without it, then `exec` upstream's script with the original CMD (`exec /app/scripts/docker-entrypoint.sh "$@"`).
- **Fail fast and loudly on bad secrets.** Empty token → exit 1 with a message. Wrong key length → exit 1 (upstream often only warns and silently disables encryption).
- **Bind on both address families.** Railway's private network has been IPv6-only historically and hands out private IPv4 addresses today; peers arrive either way. Per runtime:
  - Go `net.Listen`: `[::]:PORT` (dual-stack by default). If the app formats `host:port` itself, host must be `[::]`, not `::` (`:::8080` is rejected).
  - uvicorn/asyncio: `--host ''` (empty) binds IPv4 and IPv6. `--host ::` is **IPv6-only** because asyncio sets `IPV6_V6ONLY` on explicit IPv6 hosts.
  - nginx: `listen PORT; listen [::]:PORT;` (nginx defaults `[::]` to `ipv6only=on`, so no conflict).
  - Python grpc: `[::]:PORT` is dual-stack.
  - Caddy/Node: `:PORT` is dual-stack.
- **Never point a reverse proxy at a fixed upstream address.** Railway changes a service's private IP on every redeploy. nginx resolves static `proxy_pass` hostnames once at startup and never again → 502 after the backend redeploys. Use a `resolver` taken from `/etc/resolv.conf` plus a variable upstream (`set $api http://host:port; proxy_pass $api;`). Caddy re-resolves by itself.
- **Volumes are root-owned and contain `lost+found`.** Either run as root and hand the directory to the app user (`RAILWAY_RUN_UID=0`, then `chown` + `setpriv`/`gosu`), or rely on upstream entrypoints that already do this. Put database data in a **subdirectory** (`PGDATA=/var/lib/postgresql/data/pgdata`) because initdb refuses a non-empty directory.
- **Keep pid files and unix sockets off the volume** (`/tmp`), and remove stale ones at start; a pid file with pid 1 on a persistent volume blocks the next start (gortex).
- **Don't ship secrets to the browser.** If an upstream web UI would expose a backend token, put a proxy with basic auth in front that injects the token server-side (gortex: Caddy `basic_auth` + `header_up Authorization "Bearer {$TOKEN}"`).
- **Set sane defaults in `ENV`** of the Dockerfile for everything that has one right answer on Railway (`GIN_MODE=release`, `STORAGE_TYPE=local`, `WEKNORA_SANDBOX_DOCKER_ENABLED=false`, locale `en-US`, `TZ=UTC`). Leave secrets and references to the template variables.
- **Patch built frontends at start when you must**, and refuse to start when the patch target disappears (`sed` the bundle; `grep` first; `exit 1` if neither the original nor the patched form is found). This turns a silent breakage after an upstream change into a failed deploy.

### 3.5 Local rehearsal (do it before touching Railway)

Bring the whole stack up with plain `docker run` on one network, from the exact Dockerfiles in the repo, and exercise the user journey with `curl`. A reusable skeleton:

```bash
#!/bin/bash                      # bash, not zsh: zsh does not word-split "$COMMON"
docker network create xnet
docker volume create x_pg; docker volume create x_files
# simulate Railway: root-owned volume with lost+found
docker run --rm -v x_pg:/v -v x_files:/f alpine sh -c 'mkdir -p /v/lost+found /f/lost+found'
docker run -d --name xpg --network xnet -v x_pg:/var/lib/postgresql/data -e PGDATA=/var/lib/postgresql/data/pgdata -e POSTGRES_PASSWORD=pw ... <postgres image>
docker run -d --name xredis --network xnet redis:8.2 redis-server --requirepass pw
docker run -d --name xapp --network xnet -v x_files:/data -p 18080:8080 -e ... x-app
docker run -d --name xweb --network xnet -p 18081:80 -e API_UPSTREAM=http://xapp:8080 x-web
until curl -sf localhost:18080/health; do sleep 2; done
# then: register/login/me through the proxy, websocket upgrade (expect 101), restart app (migrations idempotent), user/uid checks
```

Checks that paid off: `/proc/net/tcp` and `/proc/net/tcp6` inside the container to prove dual-stack listening; `docker top <c> -o user,args` or `/proc/1/status` `Uid:` for privilege drop; `ls -ln <volume>` for ownership; a **header-echo upstream** (a ten-line Python `http.server` that prints `X-Forwarded-For`, `X-Forwarded-Proto`, path) behind the template's nginx to verify header maps and that a trailing slash in the upstream URL does not eat the request path.

Pitfalls of the rehearsal itself:

- Two smoke scripts using the same container names destroy each other. Run one at a time.
- Container name must differ from network name (Docker DNS confusion).
- In zsh an unquoted `$COMMON` holding several `-e K=V` pairs is passed as **one** argument; docker then sees one bogus env var and your secret is empty. Use bash scripts.
- The official `postgres:18` image refuses a volume mounted at `/var/lib/postgresql/data` (PGDATA moved); mount at `/var/lib/postgresql`. Railway's `postgres-ssl:18` still uses `/var/lib/postgresql/data`.
- The app image may lack `curl`, `ps`, `timeout`; use `python -c` / `bash /dev/tcp` / `/proc` instead.
- Docker Desktop/OrbStack may stop between turns: `docker info` first.

### 3.6 Reference project on Railway

```bash
mkdir ref && cd ref && railway init -n <name>-railway-template     # links this dir
# stock-image services with their own variables
railway add -s Postgres -i ghcr.io/railwayapp-templates/postgres-ssl:18 -v POSTGRES_USER=postgres -v POSTGRES_PASSWORD=<gen> -v POSTGRES_DB=<db> -v PGDATA=/var/lib/postgresql/data/pgdata ...
railway add -s Redis -i redis:8.2 -v REDIS_PASSWORD=<gen> -v 'REDISHOST=${{RAILWAY_PRIVATE_DOMAIN}}' -v REDISPORT=6379 -v REDISUSER=default -v 'REDISPASSWORD=${{REDIS_PASSWORD}}' -v 'REDIS_URL=redis://${{REDISUSER}}:${{REDIS_PASSWORD}}@${{REDISHOST}}:${{REDISPORT}}'
# empty services for repo-built parts (source connected later)
railway add -s api -v RAILWAY_DOCKERFILE_PATH=Dockerfile.api -v PORT=8000 -v SECRET_KEY=<gen> ...
# cross-service references once all services exist
railway variables --service api -e production --skip-deploys --set 'DATABASE_URL=postgresql://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}' --set 'REDIS_URL=${{Redis.REDIS_URL}}'
# volumes, domain, healthchecks (GraphQL, see §2)
railway domain -s web -e production -p 3000
# connect repos (after the first push), then watch
railway api 'mutation { serviceConnect(id:"…", input:{repo:"Owner/repo", branch:"main"}) { id } }'
```

Notes:

- `railway add -r Owner/repo` often answers "You do not have access to this resource" for new repos; `serviceConnect` works (sometimes only after Railway's repo list catches up, ~40 min for a brand-new repo; `githubRepoBranches` "not found" is not a reliable signal).
- `railway add` is interactive when flags are missing; pass every flag. The "Enter a service name"/"Enter a variable" echo lines in non-TTY output are harmless.
- Referenced values like `${{docreader.RAILWAY_PRIVATE_DOMAIN}}` read back as empty until that service has deployed once. Don't panic.
- Poll deployment status with a script (see appendix) and stop when nothing is `BUILDING|DEPLOYING|INITIALIZING|QUEUED|WAITING`.
- `railway logs --service X -e production` and `railway ssh --service X -e production -- <cmd>` work from the linked directory. The ssh session runs as root regardless of the container user; read `/proc/1/status` to know what pid 1 runs as.
- `railway up --service X --detach` deploys local files without a repo (used before repos were connected); `serviceInstanceDeployV2` redeploys the current source.

### 3.7 Verify on Railway like a user

Through the **public** domain only (that is all a deployer gets):

- SPA `/` → 200 and the runtime config it renders.
- Proxied API health/version endpoints → 200 with the pinned version.
- Register/login/whoami. Websocket upgrade → 101. SSE streams if the product has them.
- Real client IP in audit logs/rate limiting (compare with `curl https://api.ipify.org`).
- Privilege drop and volume ownership via `railway ssh`.
- Logs of every service for `error|fatal|panic|MISCONF|Permission denied`.
- Restart/redeploy the API: migrations idempotent, keys/uploads survive.
- First-admin path exactly as documented (e.g. register, set bootstrap email, redeploy, check the flag).

### 3.8 Independent review pass

Spawn a reviewer (a code-review agent) on the repo with the Railway facts and ask it to **write the report to a file**; messages from sub-agents got lost more than once. Triage its findings against evidence: two "HIGH" claims (peers are IPv6-only so `X-Forwarded-For` is ignored; admin-created users auto-delete demo accounts) were wrong and were rejected after testing. Recurring valid findings: `add_header` inheritance in nginx locations, `X-Forwarded-Proto` overwritten with `http` behind TLS termination, doc commands that don't exist upstream (`observal doctor --patch`), body-size limits that disagree between proxy and API, unbounded wait loops hiding their cause, root processes where upstream runs unprivileged, README claims contradicting code.

### 3.9 Template: generate, fill, publish, verify

```bash
railway templates create --project <projectId>          # → template id, code (random until renamed), UNPUBLISHED
railway api 'query { template(id:"<id>") { name code status serializedConfig } }'   # dump and inspect
```

Dump the definition with a script that prints, per service, source/deploy/volumes/networking and every variable with `defaultValue`, `isOptional`, `description`. Every literal default is `null`: hand the user a **one-per-line list** of `KEY = value` per service (they cannot copy from prose tables) plus short descriptions. Conventions: `${{secret(64)}}` for JWT/secret keys, `${{secret(32)}}` for passwords and exactly-32-byte keys, `${{secret(24)}}` for demo passwords; `RAILWAY_DOCKERFILE_PATH`, `PORT`, log levels, locale; database images get their stock aliases (`PGUSER=${{POSTGRES_USER}}`, `REDISPASSWORD=${{REDIS_PASSWORD}}`, `REDIS_URL=…`). Optional user inputs (bootstrap email, tokens for private repos) stay **empty and marked optional**; Railway omits empty optional variables at deploy.

After the user fills the composer:

1. **Read the definition back** and diff against the expected list. Real slips caught this way: a generated secret placed on the alias (`REDISPASSWORD`) instead of `REDIS_PASSWORD`; a leading space in ` ${{secret(48)}}`; a single-space default `" "` that the backend did not trim (broke every fresh Multica deploy with HTTP 500).
2. Make sure the **name** is final (code = slug of the name; `weknora-railway-template` would have become the code).
3. Publish:
   ```bash
   railway templates publish <id> --category "AI/ML" --description "<= 75 chars" --readme-file TEMPLATE_OVERVIEW.md --image https://raw.githubusercontent.com/<upstream>/<tag>/docs/img/x.png
   ```
   `--image` must be a URL (a local path fails with "Invalid image URL"); use a raw GitHub URL of an upstream screenshot or logo. Description longer than 75 characters is rejected. If upstream's banner is a GitHub **user-attachment** (`github.com/user-attachments/assets/…`) it is not a stable URL: curl gets 403 without a browser user agent and the redirect target is a signed S3 URL that expires in minutes. Download it with a browser UA, commit it to the template repo as `docs/cover.jpg`, and pass that raw URL (EverOS).
4. One-click verification: `mkdir v && cd v && railway init -n <name>-template-verify && railway deploy -t <code>`. If the template has empty required defaults the command prompts and dies without a TTY; pass `-v service.KEY=value`. Wait for all services, run §3.7 against the new domain, read generated secrets' lengths from `railway variables --json`, then `projectDelete`. Probe the API with the **exact request bodies from your README**, not hand-typed ones: an EverOS `add` written from memory returned 422 "Field required: session_id" and then "messages.0.sender_id", which looks like a broken deploy but is only a wrong payload. Expect indexing lag: a keyword search right after a flush was empty for one to two seconds until the cascade worker had upserted the Markdown.
5. Republishing later (new readme/description/image) is the same command; the code stays.

### 3.10 Capture knowledge

Per template: a memory note with ids (project, environment, services, volumes, template id/code, domains), the decisions, and the gotchas; a plan file with status; a dataset in cognee (`<name>_railway_template`) with 5–9 dense notes (architecture, wrapper details, first login, platform gotchas, publication). The cognee `remember` call can hang for 10+ minutes while the server already finished; kill and retry, deduplication returns "Stored" in one second. Verify with `recall` in `CHUNKS` mode; graph-style answers for fresh datasets may say "no information" for a while.

---

## 4. Railway platform facts (as observed, September 2026)

**Networking**
- Private DNS: `<service>.railway.internal`. Peers appeared as private **IPv4** addresses to the receiving app (Observal recorded its proxy as `10.x`), although the network is documented as IPv6-first. Bind dual-stack and never assume one family.
- Private IPs change on redeploy → resolver + variable upstream in nginx (§5.1).
- Public edge: TLS terminates at Railway. Requests arrive over plain HTTP with `X-Forwarded-Proto: https` and `X-Forwarded-For: <client>, <edge public IP>` (edge IPs seen in `152.233.0.0/16`, container-side peer `100.64.0.0/10`). The edge **appends** its own address. Apps that pick the *rightmost non-trusted* entry (Observal, Gin) therefore record the edge unless the proxy strips that hop (§5.2). Apps that use `$scheme` from nginx build `http://` URLs (OIDC callbacks) unless the header is passed through.
- Only services with a domain are reachable from outside; everything else is private. One public domain per stack is the goal.
- `railway domain -s <svc> -p <port>` creates `<svc>-production-xxxx.up.railway.app`. Templates record the port (`serviceDomains: {"<hasDomain>:80": {port: 80}}`).

**Ports**
- Railway injects `PORT` if you set it; images that hardcode a port (nginx 80) work if the domain's target port matches. Simplest: set `PORT` explicitly on every service and make the process listen on it.

**Volumes**
- One volume per service, root-owned, with a `lost+found` directory at the mount root. Consequences: initdb needs a subdirectory (`PGDATA`); redis 8.2's entrypoint prints "Unknown file './lost+found'… Permissions will not be modified" and then cannot write snapshots (`MISCONF`) — remove the empty directory first (`rmdir /data/lost+found 2>/dev/null` is enough; Railway's own Redis template uses `rm -rf $RAILWAY_VOLUME_MOUNT_PATH/lost+found/`); apps running as non-root cannot write until something chowns the directory (`RAILWAY_RUN_UID=0` then drop privileges).
- `RAILWAY_VOLUME_MOUNT_PATH` is available inside the container.
- Deleting a volume: `volumeDelete`; the data is restorable for 48 h through the dashboard.

**Variables**
- References: `${{Service.VAR}}`, `${{RAILWAY_PRIVATE_DOMAIN}}`, `${{RAILWAY_PUBLIC_DOMAIN}}`, `${{secret(N)}}` (N hex chars). Referenced values resolve only after the referenced service exists/deployed.
- Empty **optional** variables are omitted at deploy. A single space is not empty (Multica).
- Variables are also available as Docker **build args** (`ARG` in the Dockerfile).
- `RAILWAY_DOCKERFILE_PATH` selects the Dockerfile; `RAILWAY_RUN_UID=0` forces root for images that set `USER`.
- `railway variables --service X -e production --set K=V --skip-deploys`; `--json` to read (from the linked dir).

**Builds and deploys**
- Connecting a repo triggers a build; every push to the branch rebuilds all services connected to it (even those whose files did not change).
- Restart policy default `ON_FAILURE`, 10 retries. A service that crashes while a database is still initialising can exhaust them; add a wait loop or accept the retries.
- Healthcheck: HTTP path only, on the private network; set `healthcheckTimeout` high (600 s) when migrations run before the port opens. gRPC-only services get no healthcheck.
- Rollback starts the old image on the new schema; forward-only migrations then log errors (Multica). Don't promise rollbacks.

**Templates**
- Generation captures structure and references, not literal values. Values, descriptions and the template name are dashboard-only.
- The published **code** is the name slug. Rename before publishing; no rename API. An abandoned template with a random code (`mVtlXT-…`, `tS2HIy`, `shcTAS`) is what you get otherwise.
- `railway deploy -t <code>` uses the template's defaults; missing defaults → interactive prompts → failure without a TTY.
- Marketplace description ≤ 75 characters. `--image` is a URL. The overview readme has an expected structure (§6).
- Deployers are notified of updates when the template repo's branch changes.

---

## 5. Component patterns that worked

### 5.1 nginx in front of an API (SPA + proxy)

Derive the config from **upstream's file at the pinned tag** and change only:

```nginx
# http-level maps (a file in conf.d that sorts before default.conf)
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
map $http_x_forwarded_proto $fwd_proto { default $scheme; https https; http http; }
map $http_x_forwarded_for $client_xff {
    default                                  $remote_addr;
    "~^(?<chain>.+?)\s*,\s*[^,\s]+\s*$"   $chain;    # drop Railway's edge hop
    "~^\s*(?<single>[^,\s]+)\s*$"          $single;
}
server {
    listen __PORT__; listen [::]:__PORT__;
    resolver __RESOLVER__ valid=10s ipv6=on; resolver_timeout 5s;
    set $api __API_UPSTREAM__;              # e.g. http://api.railway.internal:8000, no trailing slash
    location /api/ {
        proxy_pass $api;                    # variable → raw request URI is forwarded; never add a URI part
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $client_xff;
        proxy_set_header X-Forwarded-Proto $fwd_proto;
        proxy_read_timeout 3600s; proxy_send_timeout 3600s;
    }
    location /assets/ { expires 1y; add_header Cache-Control "public, immutable" always; <repeat CSP/X-Frame/nosniff here> }
}
```

Render `__RESOLVER__` (first `nameserver` of `/etc/resolv.conf`, bracket IPv6), `__PORT__`, `__API_UPSTREAM__` (strip a trailing slash) with `sed` in the entrypoint, `nginx -t` in the image during the build or the smoke test, then exec upstream's entrypoint if it does useful work (WeKnora's writes `config.js` and runs `envsubst` on a fixed variable list; add your placeholders outside that list).

Rules: `add_header` in a location **replaces** the server-level set, so repeat security headers in every location that adds its own. A `proxy_pass` with a variable **and** a URI replaces the request path — never combine. `client_max_body_size` should match the API's own limit. Regex locations cannot take a URI in `proxy_pass` anyway. `gzip` and `proxy_send_timeout` are worth copying from upstream.

### 5.2 Client IP and scheme behind Railway

Whatever the app's trust model, with the maps above it receives `X-Forwarded-For: <client>` (or `<client>, <cf>` when the deployer fronts Railway with another proxy, which then belongs in the app's trusted-proxies setting) and `X-Forwarded-Proto: https`. Verified on Observal (audit log switched from `100.64.x`/`152.233.x` to the real address) and with a header-echo upstream for WeKnora. Apps trust the nginx peer because it sits in a private range (Observal default `172.16/12,10/8,192.168/16,127.0.0.1`; Gin default private ranges + `fc00::/7`).

### 5.3 Postgres flavours

| Need | Image | Notes |
|---|---|---|
| plain Postgres | `ghcr.io/railwayapp-templates/postgres-ssl:18` | Railway's own; keeps `/var/lib/postgresql/data`; ships `PG*` alias variables and `SSL_CERT_DAYS`, `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=60` |
| pgvector | `pgvector/pgvector:pg17` | used by Multica |
| pg_search / BM25 | `paradedb/paradedb:v0.22.2-pg17` | official-postgres based: `PGDATA` subdirectory required; **do not** set `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE` on the service — ParadeDB's init script runs plain `psql` that honours them and dies with "could not translate host name"; reference the service's `POSTGRES_*` and `RAILWAY_PRIVATE_DOMAIN` instead |

Upstream init scripts on a fresh database can print scary tracebacks and then recover (Observal: `DuplicateColumnError` from `alembic upgrade` after `create_all`, followed by "Fresh database detected: stamping"). Document it so deployers don't file bugs.

### 5.4 Redis

`redis:8.2` with a volume at `/data` and the start command

```
/bin/sh -c "rmdir /data/lost+found 2>/dev/null; exec docker-entrypoint.sh redis-server --requirepass $REDIS_PASSWORD --appendonly yes --save 60 1 --dir /data"
```

plus the stock alias variables (`REDISHOST`, `REDISPORT`, `REDISUSER`, `REDISPASSWORD`, `REDIS_URL`). Apps read either `REDIS_URL` or `host:port` + password.

### 5.5 MCP servers (cognee, projectmem, gortex)

- Streamable HTTP at `/mcp`, `/health` open, bearer auth via `Authorization: Bearer <MCP_TOKEN>` with `hmac.compare_digest`; refuse to start without a token when the bind is not localhost.
- The Python `mcp` SDK's DNS-rebinding guard returns **HTTP 421** behind Railway's proxy: `streamable_http_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))` (or the project's env flag, e.g. cognee's `MCP_DISABLE_DNS_REBINDING_PROTECTION=true`).
- Client setup lines for the README: `claude mcp add --transport http NAME URL --header "Authorization: Bearer TOKEN"`; OpenCode `headers` object.
- Read-only tool presets for shared deployments (gortex `GORTEX_TOOLS=readonly`).

### 5.6 Privilege drop when the image runs as root only for the volume

```sh
KEYS=/data/keys; mkdir -p "$KEYS"
if [ "$(id -u)" = 0 ]; then
  chown -R appuser:appgroup "$KEYS"; mkdir -p /home/appuser && chown appuser:appgroup /home/appuser
  RUN_AS="env HOME=/home/appuser setpriv --reuid=appuser --regid=appgroup --init-groups"
fi
$RUN_AS /app/entrypoint.sh; exec $RUN_AS python -m uvicorn main:app --host '' --port "$PORT"
```

`HOME` must follow the user: asyncpg probes `~/.postgresql/postgresql.crt` and raises `PermissionError` on an unreadable `/root`. Verify with `/proc/1/status`.

### 5.7 First-admin patterns and how to document them

- **Bootstrap email at startup** (WeKnora `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL`): register first, then set the variable and redeploy once; the log says "will retry on next restart" until then. Expose it as an empty optional template variable.
- **Seeded demo accounts** (Observal `SEED_DEMO_ACCOUNTS`, `DEMO_*_PASSWORD=${{secret(24)}}`): document how to read the generated password from the service variables, create the real admin, and delete demo users manually (auto-cleanup only fired for SCIM in 1.13.1 — verify claims like this by calling the API).
- **Template-generated token** (`${{secret(48)}}`) for headless services; put the exact client command in the README.
- **Loopback-only bootstrap endpoints** cannot be used behind Railway's proxy; say so.

### 5.8 Caddy as an authenticating proxy (gortex web)

`basic_auth` on everything except `/healthz`; `/healthz` proxied to the app (not a static 200) so a dead UI fails the check; API paths reverse-proxied with `header_up Authorization "Bearer {$TOKEN}"`; `flush_interval -1` for SSE; `caddy hash-password` at start; run as the `node` user. Note `pkill -f "node server.js"` does not match a Next.js standalone server (`next-server`).

### 5.9 Bearer-token gateway plus an unauthenticated private port, two processes in one container (EverOS)

EverOS ships **no authentication**, and its official plugins (OpenClaw, Hermes, DSH) send no header at all. The one-service design that satisfies both: Caddy on `PORT` checks a bearer token for the public domain, the app itself listens on `:8000`, which Railway only exposes on the private network, so plugins in the same project talk to `everos.railway.internal:8000` without a token. Document loudly that the private port has destructive endpoints (`flush`, `delete`) and trusts every container in the project.

```caddyfile
{
	admin off
	auto_https off
}

:{$PORT} {
	log {
		output stdout                      # Caddy redacts Authorization in access logs
	}

	@health path /health                   # open, but proxied (not a static 200) so a dead app fails the check
	handle @health {
		reverse_proxy 127.0.0.1:{$EVEROS_API__PORT}
	}

	@preflight method OPTIONS              # browsers send OPTIONS without Authorization
	handle @preflight {
		reverse_proxy 127.0.0.1:{$EVEROS_API__PORT}
	}

	@authed header Authorization "Bearer {$API_TOKEN}"
	handle @authed {
		request_body {
			max_size 64MB
		}
		reverse_proxy 127.0.0.1:{$EVEROS_API__PORT} {
			flush_interval -1
		}
	}

	handle {
		header Content-Type application/json
		respond `{"error":{"code":"UNAUTHORIZED","message":"missing or invalid bearer token"}}` 401
	}
}
```

Three things the entrypoint must do around this:

- **Validate the token before Caddy sees it.** Caddy's `header` matcher treats a leading or trailing `*` in the value as a wildcard (a token ending in `*` would turn into a prefix match) and a `"` breaks the config. Enforce `[A-Za-z0-9_-]` and a minimum length (24) with a `case` pattern and refuse to start otherwise; the template default `${{secret(48)}}` satisfies it. Also `caddy validate` before `caddy run`, so a bad config fails the deploy instead of the watchdog.
- **Fail fast on keys the upstream needs at startup.** EverOS raises `LLMNotConfiguredError` when the chat key is empty, which on Railway is a silent crash loop. An explicit empty check with a one-line message is worth more than any README paragraph. The same goes for required config files: the docs claimed containers could skip `everos init`, the server refused to start without `<root>/everos.toml`, so the entrypoint runs the idempotent init when the two toml files are missing (test the **cold start on an empty volume**, not only restarts).
- **Watch the sidecar correctly.** Caddy runs in the background and the app is `exec`ed as PID 1. A Python (or Go, or Node) PID 1 does not reap children, so a dead Caddy becomes a **zombie** and `kill -0 $PID` keeps succeeding forever; the first watchdog never fired and the container would have sat there healthy with a dark public port. Check the process state instead and take the container down so Railway restarts it:

```sh
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
CADDY_PID=$!
caddy_alive() { [ -r "/proc/$CADDY_PID/status" ] && ! grep -q '^State:[[:space:]]*Z' "/proc/$CADDY_PID/status"; }
( while caddy_alive; do sleep 5; done; echo "caddy exited; stopping the container" >&2; kill -TERM 1 ) &
exec $RUN_AS everos server start --root "$EVEROS_ROOT" --port "$EVEROS_API__PORT"
```

Verified by killing Caddy inside the running container: the container exits about six seconds later on both SIGTERM and SIGKILL. Test tooling in `python:*-slim`: there is no `kill` binary (`docker exec c kill …` fails; use `docker exec c sh -c 'kill …'`), no `ps`/`pkill` (read `/proc/*/comm`), no `curl` (python `urllib`).

Other EverOS specifics worth copying: upstream has no image, so `pip install "everos[multimodal]==${EVEROS_VERSION}"` with an `ARG` for upgrades; configuration is `EVEROS_<SECTION>__<KEY>` env, and an **empty** `EVEROS_API__HOST=` gives uvicorn the dual-stack bind from §7 through the environment; `EVEROS_MULTIMODAL__FILE_URI_ALLOW_DIRS='["/data/uploads"]'` because `file://` content items would otherwise read any path the process can; search defaults to `hybrid` and fails `PROVIDER_NOT_CONFIGURED` without an embedding model, so every README example uses `"method":"keyword"` and the six embedding/rerank variables are empty optional.

### 5.10 A many-service stack wired entirely by references (Notesnook, 6 services)

Notesnook's sync server is MongoDB + MinIO + four app services (identity, sync, sse, monograph) that each need their own public domain and must all agree on shared hosts and secrets. The template deploys with **two optional inputs**; everything else is references baked at generation. The moves that got it there:

- **Every service listens on Railway's `PORT` (8080).** Railway's healthcheck and routing use the port the app actually listens on, which is the injected `PORT`. An app bound to its own port (8264, 3000) fails the healthcheck (Kestrel logs `Overriding address(es) 'http://*:8080'`). Set each app's bind/discovery port to 8080 and point its domain's `targetPort` at 8080. A baked `ENV PORT=3000` does **not** win — Railway's runtime `PORT=8080` overrides the image env, so a service that reads `PORT` listens on 8080 regardless; match the domain to that.
- **Single-node MongoDB replica set: advertise `localhost`, connect with `directConnection=true`.** The app needs a replica set (transactions), but a single node advertised on its private domain becomes a **ReplicaSetGhost** — mongod cannot recognise its own overlay hostname as the configured member, and clients get 30s "server selection" timeouts. Initiate the set on `localhost` (mongod always recognises itself → PRIMARY) and have clients use `mongodb://${{mongo.RAILWAY_PRIVATE_DOMAIN}}:27017/<db>?directConnection=true`, which skips SDAM discovery and talks to the node directly; transactions still work. Railway healthchecks are HTTP-only, so the wrapper's entrypoint runs `rs.initiate` itself (upstream compose did it from the Docker healthcheck).
- **Build each service from its own subdirectory with `rootDirectory`, not `RAILWAY_DOCKERFILE_PATH`.** The dockerfile-path variable is a literal, so template generation nulls it into a composer field — a marketplace deploy then fails because a stranger will not know to set six dockerfile paths. `serviceInstanceUpdate(input:{rootDirectory:"<svc>"})` is part of the service source and **is** preserved; put each `Dockerfile` in `<svc>/` with COPY paths relative to that dir, and the composer loses six required fields.
- **Bake fixed constants into thin wrappers so they never reach the composer.** `FROM streetwriters/identity:<tag>` + `ENV SELF_HOSTED=1 IDENTITY_SERVER_PORT=8080 MONGODB_DATABASE_NAME=identity …`. Anything left as a literal service variable nulls into the composer; anything baked in the image just works. Shared values that must match across services stay as references: `NOTESNOOK_API_SECRET=${{secret(64)}}` on one service, `${{identity.NOTESNOOK_API_SECRET}}` on the others; MinIO creds `${{secret()}}` on minio, `${{minio.MINIO_ROOT_USER}}` on the API.
- **Presigned S3 behind Railway:** MinIO gets a public domain; the API signs upload/download URLs against that public host (`S3_SERVICE_URL`) while proxying internal PUTs to `http://${{minio.RAILWAY_PRIVATE_DOMAIN}}:8080`. Keep the data in a `/data/s3` subdirectory so the volume-root `lost+found` is not read as a bucket.

Verification is the real client's path, not hand-typed requests: signup is `POST /users` (form `email`,`password`,`client_id=notesnook`) returning an access token; attachment round-trip is `PUT /s3?name=` then `GET /s3?name=` — and curl needs `--upload-file --http1.1`, because `--data-binary` over HTTP/2 sends the body as zero bytes and the server reports `Sent 0 request content bytes`.

---

## 6. Documentation set

**README.md** (repo): deploy button `https://railway.com/deploy/<code>?referralCode=…`, services table (service → runs → source), first login exactly as it works, variables table per service with defaults and purpose, "How it fits together" (one bullet per non-obvious mechanism: bind, proxy, patches, volumes, client IPs, sizing, expected startup noise), "Not supported/included", "Upgrading", files list. Make sure every command in it exists upstream (`observal doctor patch --all-harnesses`, not upstream's own README's `--patch`).

**TEMPLATE_OVERVIEW.md** (marketplace page), Railway's expected structure:

```
# Deploy and Host <Name> on Railway
<one paragraph: what it is, what this template deploys, pinned version>
## About Hosting <Name>
## Common Use Cases
## Dependencies for <Name> Hosting
### Deployment Dependencies   (upstream repo, docs, template repo links)
### Implementation Details    (per service: image/tag, healthcheck, volume, port; first-login summary)
## Why Deploy <Name> on Railway?
Railway is a singular platform to deploy your infrastructure stack. Railway will host your infrastructure so you don't have to deal with configuration, while allowing you to vertically and horizontally scale it.
By deploying <Name> on Railway, you are one step closer to supporting a complete full-stack application with minimal burden. Host your servers, databases, AI agents, and more on Railway.
```

**CHANGELOG.md**: dated release entries (what services, what the wrappers change, what is not included, volumes/healthchecks) and an "Upgrading an existing deployment" section (back up first, what redeploys, migrations, no downgrade).

Description (≤ 75 chars) examples: "Registry and insight engine for coding-agent Skills, MCP servers, Agents." · "Tencent's open-source RAG knowledge base and agent platform." · "Code-intelligence MCP server for AI agents, with web UI."

---

## 7. Gotcha catalogue (quick reference)

Networking / binding
- uvicorn `--host ::` is IPv6-only; use `--host ''`.
- Go apps formatting `%s:%d` need `SERVER_HOST=[::]`; `::` → `:::8080` "too many colons".
- nginx static `proxy_pass` hostname → 502 after backend redeploy; resolver + variable upstream.
- Variable `proxy_pass` + URI part replaces the request path; strip trailing slashes from upstream URLs.
- Railway edge appends its IP to `X-Forwarded-For`; `$proxy_add_x_forwarded_for` appends the CGNAT peer on top. Strip the last hop.
- `X-Forwarded-Proto $scheme` behind Railway is `http` → OIDC callbacks/cookies break; pass the edge's header through.
- MCP SDK DNS-rebinding guard → HTTP 421 on Railway.
- Unix socket paths > 104 chars fail (put sockets in `/tmp`).
- Caddy `header Authorization "Bearer {$TOKEN}"` matcher: leading/trailing `*` is a wildcard, `"` breaks the config; validate the token charset `[A-Za-z0-9_-]` and length in the entrypoint.
- Preflight `OPTIONS` carries no Authorization; let it through to the app's CORS middleware or browser clients cannot call the API.

Volumes / processes
- `lost+found` at the mount root: `PGDATA` subdirectory; Redis 8.2 skips permission fixes (rmdir it); apps as non-root can't write (chown as root, then drop).
- Stale pid file with pid 1 on a volume blocks the next start.
- `setpriv`/`gosu` keep `HOME=/root`; export `HOME` for the target user.
- Images often lack `curl`, `ps`, `timeout`; `bash` `/dev/tcp` and `/proc` are your tools. `kill` is a shell builtin, not a binary, in slim images: `docker exec c sh -c 'kill …'`.
- An app `exec`ed as PID 1 does not reap children: a dead sidecar is a zombie and `kill -0` still succeeds. Watch `/proc/$PID/status` for `State: Z` and `kill -TERM 1` so Railway restarts the container.
- Upstreams that raise on a missing key at startup (`LLMNotConfiguredError`) crash-loop silently on Railway; check the variable in the entrypoint and exit with a message.
- "Containers can skip init" in upstream docs may be false (EverOS refused to start without `everos.toml`); run the idempotent init when the config is missing and test a cold start on an empty volume.

Databases
- Migrations needing `pg_search` → ParadeDB image; `pgvector` → pgvector image; check `CREATE EXTENSION` lines.
- ParadeDB/official-postgres init scripts honour `PGHOST`/`PGUSER`/`PGDATABASE` env; don't set Railway-style aliases on such a service.
- Fresh-DB init noise (DuplicateColumnError then stamp) is normal for some upstreams; document.
- Rollbacks with forward-only migrations fail at runtime.

Variables / templates
- Generated templates have no literal values; the composer is dashboard-only; hand over a one-per-line list.
- `${{secret(N)}}` with N = the exact length the app requires (32 for AES-256 keys).
- Read the definition back: aliases vs. real keys, leading spaces, single-space defaults, empty vs optional.
- Referenced values are empty until the referenced service deployed once.
- Empty optional variables are omitted at deploy; `" "` is not empty.
- Template generation **preserves** `${{...}}` references and `${{secret(N)}}`; it **nulls every literal** into a composer field. So wire all inter-service hosts/URLs/shared-secrets as references in the reference project, and bake fixed constants into wrapper images, before generating — otherwise the deployer must type them.
- Build a repo service from a subdirectory with `serviceInstanceUpdate(input:{rootDirectory:"<svc>"})` (preserved in the template) instead of a `RAILWAY_DOCKERFILE_PATH` variable (nulled into a required composer field).
- Rename a template by renaming the reference **project** (`projectUpdate(input:{name})`) then regenerating — the template inherits the project name, and there is no template-rename mutation. Publish slugs the name into the code.
- Template code = name slug; rename before publishing; publish description ≤ 75 chars; `--image` must be a URL.
- `railway deploy -t` prompts for missing defaults and fails without a TTY, even for OPTIONAL fields (`-v svc.KEY=val`).
- `serviceInstanceUpdate` and `serviceDomainUpdate` return a **Boolean**, not an object — a `{ id }` selection set fails to parse. `serviceDomainUpdate` needs the whole input (`serviceDomainId`, `serviceId`, `environmentId`, `domain`, `targetPort`), not just the changed field.
- Deep-nested `railway api` GraphQL is easy to mis-brace on the shell; write the query to a file and check `{` vs `}` counts before sending.
- GitHub user-attachment banners are not stable image URLs (403 for curl, expiring signed redirect); commit a copy to the template repo and use its raw URL.
- Reference values such as `${{EVEROS_LLM__API_KEY}}` survive template generation; every literal default does not. Verify with the read-back which of your composer entries actually stuck.

Repos / CLI
- `railway add -r` "You do not have access" → `serviceConnect`; new repos may be "Not Authorized" for ~40 min.
- `railway variables/logs/ssh` need the linked directory.
- Every push rebuilds every connected service.
- Sub-agent reports can get lost; have them write files.
- **Before `git add` in a new folder, `git rev-parse --show-toplevel` must print that folder.** A parent directory that is itself a git repo swallows the add: `git init` guarded by `--is-inside-work-tree` did not run, and `git add -A` pushed 455 workspace files (agent config, memory notes, other projects) to a brand-new public repo. `git init` unconditionally in the new directory; prefer explicit paths over `-A` in directories you did not create this session.
- A force-push does **not** remove a leaked commit from GitHub: it stays fetchable by hash (`repos/<r>/commits/<sha>`, full tree) and the repo activity feed lists the old hash next to the new one. Make the repo private at once, then delete and recreate it (`gh auth refresh -s delete_repo`, `gh repo delete`, `gh repo create`, push) and verify the old hashes return 404.
- cognee `remember` may hang after finishing server-side; retry is a 1-second dedup.

Docs
- Copy nothing from upstream READMEs without checking the CLI: flags drift (`--patch` vs `patch`).
- Claims about auto-behaviour (demo cleanup, migrations, trusted proxies) must be tested, not inferred from one code path.

---

## 8. Case files

| Template | Code | Services | Key decisions and lessons |
|---|---|---|---|
| Cognee AI Memory Platform with MCP | `cognee-ai-memory-p-1` | cognee-api, cognee-mcp (http `/mcp`), Postgres | MCP 421 fixed with the DNS-rebinding flag; `EMBEDDING_API_KEY=${{LLM_API_KEY}}`; personal instance later switched to cheaper OpenRouter models; the old README code `SKKN1Y` was dead |
| Multica | `multica` | frontend (Next.js), backend (Go, `/readyz`, uploads volume), pgvector | Turned a stale fork into a thin wrapper over `ghcr.io/multica-ai/*:v0.4.41`; `RESEND_API_KEY=" "` default broke login (500) — blank optional defaults; realtime works through the frontend proxy despite upstream docs; rollback logs schema errors |
| projectmem | `projectmem` | projectmem (Python, volume `/data`) | No upstream image: `pip install projectmem==0.3.2 mcp==2.2.0`; stdio-only tool wrapped in a Starlette Streamable-HTTP app with bearer auth; entrypoint registers projects idempotently; `serviceConnect` instead of `railway add -r` |
| gortex | `gortex` | gortex (Go binary, checksum-verified download, XDG dirs on `/data`), web (Next.js behind Caddy basic auth) | Default repo = gortex's own; `GORTEX_TOOLS=readonly`; socket/pid in `/tmp`; Caddy injects the daemon token so the browser never sees it; `/healthz` proxied to Node; repo connection lag |
| Observal | `observal` | web (nginx), api + worker (same image, role switch), Postgres 18 (Railway), ClickHouse (upstream tuning), Redis | uvicorn dual-stack bind; XFF edge-hop strip; websocket origin patched in the bundle at start; api starts as root only to hand the key volume to `appuser` (HOME!); seeded demo accounts with generated passwords; Data Migration screen unsupported (needs shared volume); `observal doctor patch --all-harnesses` |
| WeKnora | `weknora` | frontend (nginx, v0.8.0-tag config), app (Go, `[::]`, Postgres wait), docreader (gRPC, inline images), ParadeDB, Redis | Config from the release tag, not main; `SERVER_HOST=[::]`; Redis `rmdir lost+found`; no `PG*` aliases on ParadeDB; forwarded-proto/for maps for OIDC and client IPs; bootstrap admin by email + redeploy; rename before publish |
| EverOS | `everos` | everos (python:3.12-slim + `everos[multimodal]==1.3.1` from PyPI, Caddy 2.11 in the same container, volume `/data`) | No upstream image and no auth: Caddy bearer gate on `PORT`, app on private `:8000` for the header-less official plugins; token charset/length validated (Caddy matcher wildcards); fail-fast on the LLM key; `everos init` on cold start despite docs; zombie-aware Caddy watchdog (PID 1 does not reap); `EVEROS_API__HOST=` for dual-stack; `file://` reads fenced to `/data/uploads`; keyword search until embeddings exist; cover committed to the repo because upstream's banner is a user-attachment |
| Notesnook | `notesnook-sync-server` | mongo (7.0.12, single-node RS), minio (+mc), identity/notesnook-sync/sse/monograph (streetwriters images) — 6 services, each built from its own `rootDirectory` | Every app on Railway's `PORT` 8080 (image `PORT` env is overridden); single-node Mongo advertises `localhost` + clients `directConnection=true` (else ReplicaSetGhost); thin wrappers bake the fixed constants so the composer is two optional fields; all wiring by references, shared secret + MinIO creds via `${{secret()}}`/service refs; presigned attachments against the public MinIO domain, data in `/data/s3`; verified with the real signup/token/attachment path |

Reference-project and template ids live in the per-template memory notes (`railway-<name>-template-ids`) and in each repo's docs.

---

## Appendix A. Status poll script

```bash
#!/bin/bash   # usage: status.sh <projectId>   (run from the linked dir)
railway api "query { project(id:\"$1\") { services { edges { node { name serviceInstances { edges { node { latestDeployment { id status createdAt } domains { serviceDomains { domain } } } } } } } } } }" 2>/dev/null | python3 -c "
import sys,json
for e in json.load(sys.stdin)['data']['project']['services']['edges']:
    n=e['node']; si=n['serviceInstances']['edges'][0]['node']; ld=si.get('latestDeployment') or {}
    print(f\"{n['name']:11} {ld.get('status') or '-':12} {(ld.get('createdAt') or '')[11:19]} {(ld.get('id') or '')[:8]} {' '.join(d['domain'] for d in si['domains']['serviceDomains'])}\")"
```

Loop until no line contains `BUILDING|DEPLOYING|INITIALIZING|QUEUED|WAITING`.

## Appendix B. Template definition dump

```bash
railway api 'query { template(id:"<id>") { name code status serializedConfig } }' | python3 -c "
import sys,json
t=json.load(sys.stdin)['data']['template']; cfg=t['serializedConfig']
if isinstance(cfg,str): cfg=json.loads(cfg)
print(t['name'], t['code'], t['status'])
for s in cfg['services'].values():
    print('---', s['name'], s.get('source'), s.get('deploy'), [v['mountPath'] for v in (s.get('volumeMounts') or {}).values()], s.get('networking'))
    for k,v in sorted((s.get('variables') or {}).items()):
        dv=v.get('defaultValue'); print(f'  {k} = {json.dumps(dv)}', '[optional]' if v.get('isOptional') else '', '<-- EMPTY' if dv in (None,'') else ('<-- WHITESPACE' if dv!=dv.strip() else ''), '|', v.get('description') or '')"
```

## Appendix C. Pre-publish checklist

- [ ] Every image tag pinned; tags exist for amd64 and arm64.
- [ ] Local smoke: health, dual-stack listen, user journey through the proxy, websocket 101, second start idempotent, uid/ownership.
- [ ] Cold start on an empty root-owned volume works (init, chown); killing a sidecar takes the container down.
- [ ] `git rev-parse --show-toplevel` prints the template repo before every add/push; nothing but the template files is tracked.
- [ ] Reference project: all services SUCCESS, verified through the public domain, logs clean, client IP correct.
- [ ] Review report read and triaged; fixes committed; redeploy verified.
- [ ] README/OVERVIEW/CHANGELOG consistent with each other and with upstream's CLI.
- [ ] Template definition read back: no empty non-optional values, no whitespace, secrets on the right keys, name final.
- [ ] Published with category, ≤ 75-char description, overview readme, image URL.
- [ ] One-click deploy into a scratch project verified, then deleted.
- [ ] Memory notes, plan file, cognee dataset updated; repo README button points at the real code.
