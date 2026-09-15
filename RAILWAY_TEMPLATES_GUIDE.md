# Railway Templates Guide

How to turn an open-source project into a one-click Railway marketplace template, collected while building and publishing twenty-five of them in September 2026: **cognee**, **Multica**, **Fabric**, **LongMemory**, **projectmem**, **gortex**, **Observal**, **WeKnora**, **EverOS**, **Notesnook**, **Pipecat**, **OpenPencil**, **Persistent mise Workspace**, **HertzBeat**, **Laminar**, **OpenKnowledge**, **tlbx**, **codeg**, **Orca**, **DSH + LongMemory**, **Mirage Daemon**, **Yao Agents**, **HolyClaude Workstation**, **Octop** and **Coddy**. Everything here was learned the hard way on real deployments; the "gotcha" sections are the most valuable part. The guide is written for a developer or an agent who has never touched Railway templates and has to repeat this end to end.

Related repos and deploy links are indexed in [README.md](README.md). The newer `pipecat-railway-template`, `openpencil-railway-template` and `mise-railway-template` also demonstrate native `.railway/railway.ts` authoring. September 9 updates below distinguish verified CLI/API capabilities from the original dashboard-only workflow.

---

## 1. The pattern in one page

A Railway template is **not** a file in a repo. It is an object in the Railway workspace (the "template definition") that lists services, their sources, variables, volumes, healthchecks and domains. The repo only supplies what the services build from.

The approach that worked every time:

1. **Thin wrapper or starter repo.** One public GitHub repo per template. Prefer a pinned upstream image plus small entrypoints/config overrides. When upstream is a framework or CLI, build from pinned packages or checksum-verified release artifacts and add only the needed application/adapter code (Pipecat, mise). Avoid an unnecessary upstream fork. Small pin changes still require integration tests; a version bump is not proof of compatibility.
2. **Reference project.** A normal Railway project where the services run from that repo (and from stock images for databases). It is the living proof that the stack works, and the source from which the template definition is generated.
3. **Generated template.** `railway templates create --project <id>` turns the reference project into a template definition. It captures sources, healthchecks, volumes, domains and *variable references*, but literal defaults and some deployment settings can be omitted. Fill and verify them through the authenticated template change-set API (§3.9.1) or the dashboard composer.
4. **Publish.** `railway templates publish <id> --category ... --description ... --readme-file TEMPLATE_OVERVIEW.md --image <url>`. The marketplace code becomes the slug of the template **name**, so finalize the name through a change set or the dashboard before publishing.
5. **Verify the published thing.** `railway deploy -t <code>` into a scratch project, exercise it like a user, delete the scratch project.
6. **Write it down.** README, TEMPLATE_OVERVIEW, CHANGELOG in the repo; memory notes; cognee dataset.

Never publish without steps 2 and 5. Half of the bugs below were only visible on Railway, the other half only locally.

---

## 2. Toolchain and access

- `railway` CLI: older examples below use v4-era syntax; the September 9 additions were verified with CLI **5.49.3** and Railway TypeScript SDK **3.11.0**. Commands include `railway login`, `railway init`, `railway add`, `railway variables`, `railway volume`, `railway domain`, `railway up`, `railway deploy -t`, `railway logs`, `railway ssh`, `railway config plan|apply`, and `railway templates create|publish`. Check local `--help` before copying an older command.
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

`TemplatePublishInput` itself does not expose template-name or variable editing. That is not an API-wide limitation: authenticated `templateChangeSetStage` / `templateChangeSetApply` on the internal endpoint can edit them (§3.9.1). Internal API shapes are not a stable public SDK contract; revalidate them and read back the saved definition.

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
- **License** (check redistribution obligations for each pinned upstream and the wrapper), **release cadence** (daily releases mean frequent tag bumps), **resource needs** (Helm `values.yaml` limits are a good source).
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

Newer templates use native `.railway/railway.ts` with explicit `github("Owner/repo", { branch: "main" })`, `build.dockerfilePath`, watch patterns, volumes and deploy settings. The Dockerfile-path variable remains a supported older pattern, but can become an unwanted composer input when generated. Do not add deprecated `railway.json` / `railway.toml` files to these native-IaC examples. Preview before applying: an omitted variable map can delete existing variables, so preserve known keys or discover owner-variable names with an explicitly scoped, fail-closed helper (§5.13).

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

Use the deployer's intended entry point: the **public** domain for browser/API stacks, or managed **Railway SSH** for a private workspace such as mise. Do not add a public HTTP service just to test an SSH-only template. For browser/API stacks:

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

Inspect the definition per service: source/deploy/volumes/networking and each variable's `defaultValue`, `isOptional`, `description`. Redact actual credentials; do not dump secret values into logs or these records. Literal defaults are generally `null` after generation. Fill them through §3.9.1, or hand the user a **one-per-line list** of `KEY = value` per service for manual composer editing, with each description on its own line. Conventions: `${{secret(64)}}` for JWT/secret keys, `${{secret(32)}}` for passwords and exactly-32-byte keys, `${{secret(24)}}` for demo passwords; fixed Dockerfile paths/ports/log levels/locale as appropriate; stock database aliases remain references. Optional inputs stay empty, not whitespace. Conditional provider keys can be optional in the form while the app validates the selected provider's required keys before creating paid resources (Pipecat).

After filling the definition through either route:

1. **Read the definition back** and diff against the expected list. Real slips caught this way: a generated secret placed on the alias (`REDISPASSWORD`) instead of `REDIS_PASSWORD`; a leading space in ` ${{secret(48)}}`; a single-space default `" "` that the backend did not trim (broke every fresh Multica deploy with HTTP 500).
2. Make sure the **name** is final (code = slug of the name; `weknora-railway-template` would have become the code).
3. Publish:
   ```bash
   railway templates publish <id> --category "AI/ML" --description "<= 75 chars" --readme-file TEMPLATE_OVERVIEW.md --image https://raw.githubusercontent.com/<upstream>/<tag>/docs/img/x.png
   ```
   `--image` must be a URL (a local path fails with "Invalid image URL"); use a raw GitHub URL of an upstream screenshot or logo. Description longer than 75 characters is rejected. If upstream's banner is a GitHub **user-attachment** (`github.com/user-attachments/assets/…`) it is not a stable URL: curl gets 403 without a browser user agent and the redirect target is a signed S3 URL that expires in minutes. Download it with a browser UA, commit it to the template repo as `docs/cover.jpg`, and pass that raw URL (EverOS).
4. One-click verification: create a short-named scratch project, confirm its exact linked ID, then run `railway deploy -t <code>`. If the template has empty required defaults the command prompts and dies without a TTY; pass `-v service.KEY=value`. Wait for the intended entry point to be ready and run §3.7 through the public domain for web apps or managed SSH for a private workspace. Check generated secrets' lengths without logging their values, then delete only the verified scratch project. Probe APIs with the **exact request bodies from your README**, not hand-typed ones: an EverOS `add` written from memory returned 422 "Field required: session_id" and then "messages.0.sender_id", which looks like a broken deploy but is only a wrong payload. Expect indexing lag: a keyword search right after a flush was empty for one to two seconds until the cascade worker had upserted the Markdown.
5. Republishing later (new readme/description/image) is the same command; the code stays. Use the CLI for this rather than a hand-rolled `templatePublish` mutation — the raw endpoint can report a publishing block that is no longer in force (§4).

### 3.9.1 CLI/API authoring verified on OpenPencil and mise

Browser automation is not required. Use the authenticated account's existing access without copying tokens into scripts, repositories, screenshots or logs. Public operations use `https://backboard.railway.com/graphql/v2`; the template editor uses `https://backboard.railway.com/graphql/internal`.

1. Read `template(code: ...) { id code name status readme serializedConfig }`. The real service definition is **`serializedConfig`**, not the legacy `config` field. Resolve the template's own service/volume IDs; they are not the reference project's IDs.
2. Check `templateChangeSets(templateId: ..., includeInactive: false, first: 10)` for active edits. Do not merge over someone else's unfinished changes.
3. Stage a narrow `TemplatePatch` with `templateChangeSetStage(templateId: ..., patch: ..., merge: true) { id status }`. A variable edit is `config.services[templateServiceId].variables.KEY = { defaultValue, description, isOptional }`; naming and overview edits are `metadata.name` and `metadata.readme`. For an overview-only update, send only `metadata.readme`.
4. Apply the returned change-set ID with `templateChangeSetApply(changeSetId: ...) { id status }`. If a response says it was already applied, read the current state before retrying.
5. Set initial volume size explicitly with the public `templateVolumeUpdate(templateId, serviceId, volumeId, sizeMB)` mutation. OpenPencil uses **1024 MB**; mise uses **5000 MB**. Without this explicit field, a new deployment can receive its plan's much larger default volume size.
6. Read back metadata and `serializedConfig`, comparing fields rather than JSON key order. The generator omitted mise's resource caps, sleep/replica settings and daily backups; these were explicitly restored in the template. `volumeMounts[templateVolumeId].backupSchedules = ["DAILY"]` produced a real daily schedule in the fresh mise copy. Do not infer success just because an authoring file declared a setting.
7. Publish, then verify a fresh copy. Use a short valid project name, abort if `railway init` fails, and assert the exact linked project ID and empty contents before `railway deploy --template <code>`; that command has no `--project` flag. A failed init must never fall back to a parent directory's project link.

An overview update can be saved correctly while the public page still serves a cached older copy. Verify through unauthenticated template readback and, if necessary, a query-string cache-busted page. During the mise update the frontend advertised a one-hour cache. Keep the repository's `TEMPLATE_OVERVIEW.md` synchronized with the saved overview; verify links and distinguish local commands from commands run inside the service.

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
- Volumes mount at runtime, not during the Docker build or pre-deploy phase. Keep first-boot seed assets outside the mount; test Docker named volumes with `volume-nocopy` so automatic Docker population does not hide a Railway cold-start bug.
- `RAILWAY_VOLUME_MOUNT_PATH` is available inside the container.
- Deleting a volume: `volumeDelete`; the data is restorable for 48 h through the dashboard.

**Variables**
- References: `${{Service.VAR}}`, `${{RAILWAY_PRIVATE_DOMAIN}}`, `${{RAILWAY_PUBLIC_DOMAIN}}`, `${{secret(N)}}` (N hex chars). Referenced values resolve only after the referenced service exists/deployed.
- Empty **optional** variables are omitted at deploy. A single space is not empty (Multica).
- Variables are also available as Docker **build args** (`ARG` in the Dockerfile).
- `RAILWAY_DOCKERFILE_PATH` selects the Dockerfile; `RAILWAY_RUN_UID=0` forces root for images that set `USER`.
- `railway variables --service X -e production --set K=V --skip-deploys`; `--json` to read (from the linked dir).

**Builds and deploys**
- Connecting a repo triggers a build. Unfiltered sources can rebuild on any push; explicit per-service watch patterns prevent documentation-only updates from restarting unrelated workloads. Verify that those patterns survive template generation.
- Restart policy default `ON_FAILURE`, 10 retries. A service that crashes while a database is still initialising can exhaust them; add a wait loop or accept the retries.
- Healthcheck: HTTP path only, on the private network; set `healthcheckTimeout` high (600 s) when migrations run before the port opens. gRPC-only services get no healthcheck.
- Rollback starts the old image on the new schema; forward-only migrations then log errors (Multica). Don't promise rollbacks.

**Templates**
- Generation captures structure and references, but literal defaults and some deployment settings need explicit completion. Values, descriptions and the template name can be edited through authenticated change sets (§3.9.1) or the dashboard.
- The published **code** is the name slug. Finalize the name before publishing through a change set or the dashboard. An abandoned unpublished template can retain a random code (`mVtlXT-…`, `tS2HIy`, `shcTAS`); verify the final deploy URL instead of reusing its draft code.
- `railway deploy -t <code>` uses the template's defaults; missing defaults → interactive prompts → failure without a TTY.
- Marketplace description ≤ 75 characters. `--image` is a URL. The overview readme has an expected structure (§6).
- Deployers are notified of updates when the template repo's branch changes.
- **One "hidden" template blocks publishing for the entire workspace.** Railway staff can mark a template hidden by an administrative action. While any template in a workspace carries that status, every publish for that workspace is refused with "You have been blocked from publishing templates. Please reach out to the team for more information." — including a readme-only edit to an unrelated template. Nothing in the API identifies which one: status stays `PUBLISHED`, it still appears in `templateSearch`, `health`/`activeProjects`/`projects`/`totalPayout` show no outlier, and there is no `isHidden` field. Only Railway can clear it. Ask on Central Station, which is the support channel for every plan below Enterprise (email is reserved for sales, security, abuse and privacy, and is documented as possibly unanswered otherwise).
- **Diagnose publishing state with the CLI, never a hand-rolled mutation.** `railway templates publish|update` succeeded while a `templatePublish` mutation posted to `backboard.railway.com/graphql/v2` with `user.accessToken` kept returning the block error — eight minutes after the CLI had worked, and after Railway had actually lifted the block. Token expiry, `User-Agent` and template identity were each ruled out. The raw endpoint produces **false negatives**: it reported a block that no longer existed, and nearly sent a bug report contradicting the engineer who had just fixed it. Treat the CLI as the source of truth before escalating anything.

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

### 5.11 A one-service, multi-provider browser voice starter (Pipecat)

[Pipecat](https://github.com/pipecat-ai/pipecat) is a framework, so the [template](https://github.com/RockinPaul/pipecat-railway-template) supplies the small session API, bot lifecycle and bundled browser client. It pins Pipecat **1.8.1** on Python **3.12.14**, runs as an unprivileged app user, and exposes one HTTP service with `/health`. There is no database, persistent volume, queue or separate frontend service; Daily is the external WebRTC transport, not another Railway container.

- **Provider-dependent requirements.** `DAILY_API_KEY` and the generated `ACCESS_PASSWORD` are always required. `AI_PROVIDER` selects OpenAI Realtime, Gemini Live, Grok Voice, OpenRouter or an existing Ollama server. The first three use native voice APIs; OpenRouter/Ollama compose STT → LLM → TTS. Their `SPEECH_PROVIDER` can select OpenAI or OpenRouter, so the required speech key may differ from the LLM key. Unused provider keys stay empty; the app validates the selected combination before creating a paid Daily room.
- **Ollama is an endpoint, not included compute.** The server/model must already exist and be reachable from Railway. `localhost` means the Pipecat container, not the deployer's laptop. Hosted speech is still needed in that mode. Never imply that choosing Ollama makes the entire stack free or provisions a GPU/model server.
- **Contain the session lifecycle.** One replica, one worker, one active conversation. Request microphone permission before starting a paid session; allocate a private two-participant room and short-lived browser token, wait for the bot to join, and clean up on disconnect, timeout, bot exit or startup failure. The default duration is 600 seconds, with a 30–1800-second accepted range. Room/token expiry remains a backstop after an abrupt container stop.
- **Separate readiness from paid-provider verification.** `/health` makes no provider calls and proves neither valid credit nor working audio. Offline adapter tests cover all five modes; the README records live synthetic-browser-audio verification for the OpenAI reference route, not live verification of Gemini, Grok, OpenRouter or a real Ollama endpoint. Do not label mocked adapter construction as end-to-end voice validation.

Provider keys stay server-side; the browser receives a scoped Daily token. There is no app recording/transcript persistence or multi-user account system. Railway, Daily and the selected AI/speech providers have separate usage charges. The [README](https://github.com/RockinPaul/pipecat-railway-template#choose-a-provider) records the full conditional variable matrix and verification boundaries.

### 5.12 An explicitly saved shared design workspace behind Caddy (OpenPencil)

The [OpenPencil template](https://github.com/RockinPaul/openpencil-railway-template) pins the upstream **v0.8.4 prerelease** Rust web-host image by digest. A public Caddy `gateway` authenticates the UI, assets, APIs and streams; `openpencil` stays private on port 3100. The app healthcheck is `/`; the gateway exposes a sanitized `/healthz`. There is one **1024 MB** initial volume at `/data`, no database, and no inference server/GPU provisioned by the template.

- **Document the actual save operation.** The shared startup file is `/data/workspace.op`. **File → Save** persists it through the native save endpoint; **Save As** downloads an export. Unsaved edits do not survive replacement. Initialize an empty document only if it is missing, never overwrite an existing file at boot, and test an actual edited shape after save/redeploy rather than checking file presence alone.
- **One password is not user isolation.** Users sign in as `admin` with the deployment-generated `ACCESS_PASSWORD`; everyone with it sees the same document. This is for an individual or mutually trusted group, not separate private accounts or a hosted collaboration hub. Keep the backend's control/MCP endpoints off the public internet.
- **Origin checks and credential storage are distinct controls.** Retain the app's native origin checks and the gateway's exact public-origin policy. Updating a custom domain requires updating the configured origin. Browser-entered AI keys remain in same-origin local storage and accompany relevant AI requests; the wrapper disables shared server persistence of browser credential snapshots and refuses to enable it. This is not a per-user secret vault.
- **Health responses must not leak the private backend.** The unauthenticated gateway healthcheck reports sanitized upstream health, not arbitrary backend content. AI endpoint exceptions require an explicit exact-origin allowlist; no inference server is installed by filling that field.

Recorded validation: **80 tests**, independent security review, real browser editing, explicit saves surviving Railway redeployment, and a fresh published-template copy. Live AI-provider calls were not part of that validation. Basic editing requires no AI key. See the [usage and save semantics](https://github.com/RockinPaul/openpencil-railway-template#save-versus-export).

### 5.13 A private persistent developer home with no web server (mise)

[Persistent mise Workspace](https://github.com/RockinPaul/mise-railway-template) is one SSH-only service: pinned Debian 13 slim for **Linux/amd64**, mise **2026.9.3**, Node **24.21.0**, Python **3.13.15**, uv **0.12.11**, Bash/Git/tmux/build utilities and Tini. One `/root` volume starts at **5000 MB**; one replica, sleeping off, **2 vCPU / 2 GiB** limits and daily backups. These are caps, not a price or utilization promise. There are no public domains, SSH daemon, app secrets, database or browser IDE. Managed Railway SSH was verified as UID 0 with `/root` home: trusted container owner, not host root or multi-tenant isolation.

- **Prebuild tools at their final prefix.** Install the locked starter runtimes under `/root/.local/share/mise` during the Docker build, then copy the complete tree—including hidden backend metadata—to `/opt/workspace-seed` outside the runtime mount. Restore to the same absolute prefix; Python sysconfig and virtual environments can contain absolute paths. Generate the global lock with `mise lock --global --platform linux-x64`; its filename is `.config/mise/mise.lock`, not `config.lock`. Use conservative precompiled x86_64 Python instead of accidental CPU-specific/source builds.
- **Seed once without running owner content.** Require a real writable `/root` mount, use image-only startup PATH, publish a missing store through a temporary sibling plus atomic rename, and write the completion marker last. Preserve existing configs/dotfiles/symlinks and installed versions. Later boots neither execute profiles/project hooks nor reinstall removed tools. Reject unsafe/conflicting initialization paths instead of recursive repair. Image-managed mise and system utilities remain outside the volume for recovery.
- **Verify trust behavior, not just settings.** The pinned mise release's `CI=true` forces automatic trust confirmation even with `paranoid=true`; setting `MISE_PARANOID=1` or `MISE_YES=0` did not counter it. A small launcher refuses inherited CI auto-approval, preserves shim argv[0], and sets `__MISE_BIN` so activation/regenerated shims use the guard. For a deliberately trusted project needing CI behavior, run `env -u CI mise exec -- env CI=true COMMAND`. Revalidate/remove the workaround when upgrading mise. Automatic command/exec installation is disabled, install jobs are 2, and Python compilation is disabled.
- **Preserve arbitrary owner variables when applying IaC.** SDK 3.11.0 / CLI 5.49.3 treated omitted variables as deletions. The native authoring helper reads variable names with explicit project/environment/service IDs, maps them to `preserve()` (including sealed/null values), and fails closed with redacted errors. A real preview and apply retained a test variable. Keep the native destructive confirmation guard enabled; do not pass `--confirm-destructive` to bypass unexpected deletion plans.
- **Prove the persistence boundary.** Twenty-seven automated tests covered offline first boot/replacement and safety cases. Actual Railway reference and fresh-template redeploys preserved a project, venv and explicitly installed uv 0.12.10; `/tmp` and tmux processes disappeared. A snapshot restore recovered a changed file and the deliberately uninstalled extra uv version, which executed successfully without a reinstall. CLI/API restore created a detached volume; it had to be attached at `/root`, followed by waiting for the resulting deployment/SSH readiness. Retain the original volume until recovery checks pass, then recheck backup scheduling on the replacement.

Initial IaC provisioning did not establish the reference's intended daily schedule; it was enabled and read back via the public backup API. The published template's explicit `backupSchedules: ["DAILY"]` did create a schedule in the fresh copy. Likewise, the running manifest did not expose `requiredMountPath`, so the entrypoint's mount check is the actual guard. Files under `/root` persist; manual OS package changes outside it and running processes do not. The [verification report](https://github.com/RockinPaul/mise-railway-template/blob/main/docs/VERIFICATION.md) and [linked usage overview](https://github.com/RockinPaul/mise-railway-template/blob/main/TEMPLATE_OVERVIEW.md) preserve the evidence and recovery instructions.

### 5.14 A Spring Boot app: environment beats the packaged config, and a zero-input template (HertzBeat)

[Apache HertzBeat](https://railway.com/deploy/apache-hertzbeat) published with **zero composer fields** — `railway deploy -t` did not prompt at all, unlike a template whose optional fields are merely empty. Three services (the app, PostgreSQL, VictoriaMetrics), each built from its own `rootDirectory`. What got it there:

- **Do not bake a config file; override the packaged one with environment variables.** Spring's relaxed binding turns `warehouse.store.victoria-metrics.url` into `WAREHOUSE_STORE_VICTORIA_METRICS_URL`, and environment wins over the `application.yml` inside the image. Upstream's own compose setting only the datasource credentials that way is the hint it works for everything. This avoids owning a file that drifts on every version bump.
- **Read the defaults out of the PINNED IMAGE, not the repository.** `docker run --rm --entrypoint sh <image> -c 'cat /opt/.../application.yml'`. HertzBeat's repo compose is Hibernate-flavoured while the published 1.8.0 image runs **EclipseLink**, and that compose pins an image tag that is not on Docker Hub yet. Configuring from the repo would have produced a wrong, confidently-written template.
- **Swap embedded stores for real ones by environment, and check the driver already ships.** The image defaults to embedded H2 for metadata and embedded DuckDB for metrics; `SPRING_DATASOURCE_DRIVER_CLASS_NAME` plus `SPRING_JPA_DATABASE` and `SPRING_JPA_DATABASE_PLATFORM` move it to Postgres, and `WAREHOUSE_STORE_DUCKDB_ENABLED=false` with `..._VICTORIA_METRICS_ENABLED=true` move the metrics. The Postgres driver, `flyway-database-postgresql` and `db/migration/{h2,mysql,postgresql}` were already inside, and Flyway's packaged `locations: classpath:db/migration/{vendor}` then resolved to postgresql by itself — no migration path to set.
- **`SERVER_PORT=8080`** so Spring listens on Railway's injected `PORT`, with the domain pointed at 8080 (§5.10).
- **Expect the actuator to sit behind the app's own auth.** `/actuator/health` answered **401** because HertzBeat's sureness layer covers `/actuator/**`. The entrypoint appends one line to the shipped `sureness.yml` excluded list so only health is open; metrics and prometheus stay protected, and Spring hides health details by default, so it emits `{"status":"UP"}` and nothing more. Look for an already-open route first — `/===get` was there, making the UI root a usable fallback.
- **Credentials read from a file rather than a database are a template problem.** Accounts come from sureness's `DocumentAccountProvider` reading `config/sureness.yml`, default `admin`/`hertzbeat`. Generate a password and have the entrypoint rewrite **only** the credential under `- appId: admin` (awk over that block, not a global sed, so comments and any other account survive), patching the shipped file in place so a version bump keeps upstream's. Then say plainly in the README that an in-app password change will not survive a redeploy.

Verify through the product's own API and assert on bodies, not status codes: HertzBeat answers bad credentials with **HTTP 200** and `{"msg":"Incorrect Account or Password","code":5}`. Creating a monitor uses **`paramValue`**, not `value`; the wrong key returns "Params field host is required.". To prove the metric write path when the time-series database is private, either read it back through the app — its history endpoint refuses when the store is unreachable, so a success code is itself proof — or attach a domain to the store for a single query and delete it again.

### 5.15 An IPv4-only server, a login page with no password, and five platform traps (Laminar)

[Laminar](https://railway.com/deploy/laminar) published as five services (Next.js frontend, Rust
app-server, ClickHouse, Quickwit, PostgreSQL) with **two required composer fields** and everything
else wired. The interesting parts:

- **Find the project's own "lite" switch before adding services.** `ENVIRONMENT=LITE` turns
  RabbitMQ, Redis and Quickwit from dependencies into no-ops, each gated purely on its URL
  variable being present: the API server falls back to an in-memory cache and in-process span
  processing. Four services instead of seven, at the cost of one feature (Quickwit-backed
  full-text search). Read the feature-flag file, not the compose file, to find these.
- **A server that binds `0.0.0.0` is invisible on Railway's private network, and no amount of
  configuration fixes it.** Laminar's app-server hardcodes `.bind(("0.0.0.0", port))` on all three
  listeners. The wrapper installs `socat` and runs two self-restarting bridges,
  `TCP6-LISTEN:8100 -> TCP4:127.0.0.1:$PORT` and `:8102 -> :$CONSUMER_PORT`, and the frontend
  points at those ports on the private domain. **Public** routing to an IPv4-only bind is fine, so
  only service-to-service traffic needs the bridge. During a local rehearsal socat's `TCP6-LISTEN`
  also accepts IPv4-mapped connections, so the same port exercises the real code path on a plain
  docker network.
- **A Node app's bind address cannot be set in the Dockerfile.** Next.js binds whatever `HOSTNAME`
  says, and both Docker and Railway inject `HOSTNAME` with the container's own hostname at run
  time, overriding the image's `ENV`. `export HOSTNAME=::` has to happen inside the entrypoint.
- **Check how the project authenticates when nothing is configured.** Laminar's self-hosted
  sign-in accepts **any email address with no password** when no identity provider is set, which
  on a public URL is an open door. Configuring GitHub OAuth unmounts that plugin automatically, so
  the template makes the two GitHub values required and the entrypoint refuses to boot without
  them unless an explicit `ALLOW_PASSWORDLESS_SIGNIN=true` opts in. Deploy order for the operator
  is documented, because the OAuth callback needs a domain that does not exist until after the
  first deploy: create the app with a placeholder, deploy, then fix the callback.
- **Look for endpoints that trust a proxy in front of them.** The realtime stream carries an
  upstream comment saying auth "is handled by middleware on Next.js", so it authenticates nothing
  itself and must never get a public domain. Meanwhile OTLP ingest over **HTTP** is the SDK
  default, so the gRPC port needs no domain either.
- **Let the app do its own migrations, and wait for its stores.** The frontend applies the
  Postgres migrations, ~61 ClickHouse migrations, two dictionaries and the seed data on boot, and
  rethrows if ClickHouse is missing. Its entrypoint waits for Postgres on TCP and ClickHouse on
  HTTP `/ping`, which turns a first-boot crash loop into a slower first boot (~4.5 minutes;
  seconds thereafter).
- **`secret()` takes an alphabet.** `${{secret(64, "abcdef0123456789")}}` produces the 64
  hex characters an AEAD key needs. No derivation trick required, and the generated call survives
  into the template.

#### Five platform traps this template walked into

Each one presents as an application bug. Check these before debugging the app:

1. **`healthcheckPath` accepts only letters, digits, `_` and `/`.** A `.` or `-` returns
   "Error in healthcheckPath - Invalid input", so `/sign-in` and `/robots.txt` are both unusable.
2. **`healthcheckPath: null` is silently ignored; `""` clears it.** The mutation returns `true`
   either way, so a stale failing path keeps breaking deploys after you think you removed it.
3. **A failing healthcheck holds the deploy in DEPLOYING for the whole timeout, then FAILED, and
   the public domain 404s throughout** — so you cannot probe for a working path until it is already
   healthy. Clear the healthcheck, deploy, probe the live domain, then set what you verified. A
   **redirect counts as a failure**: a root that 307s to a login page is not a healthcheck.
4. **`serviceInstanceDeployV2(serviceId, environmentId)` rebuilds the commit the service already
   has**, not the branch head. Push a fix, redeploy, and you rebuild the old code with no warning.
   Pass `commitSha:` and verify with `deployments { meta }`, which carries `commitHash`.
5. **`serviceCreate(input: {source: {repo}})` is not enough to generate a template.**
   `railway templates create` fails with "Service <name> does not have a source that can be used to
   generate a template" and names only the first service alphabetically even when all are affected.
   Call `serviceConnect(id, input: {repo, branch: "main"})` on each; it preserves `rootDirectory`.

#### Three traps in the feature that was nearly shipped broken

The first version of this template shipped without Quickwit, and the review question "how useful
is this actually?" is what surfaced the problem. **A missing search backend is invisible**: the
frontend catches the failed lookup and returns an empty array, so the UI reads "no matches" rather
than "search is off". Ask what each omitted service was load-bearing for, then exercise the feature
rather than the service.

Adding it back turned up two more:

- **The published frontend image omits the index definitions its own startup code reads.** Its
  Dockerfile copies two migration directories into the standalone output but not
  `lib/quickwit/indexes`, so the app logged "Quickwit indexes directory not found" and created
  nothing. The three files are vendored at the image's tag with a README saying to re-fetch on a
  bump. Check that an image contains the data files its code expects, not just its binaries.
- **A one-shot connection at boot turns a startup race into a permanent outage.** The API server
  tries Quickwit exactly once, logs "Quickwit not available - skipping spans indexer workers" on
  failure, and never retries. On Railway it lost the DNS race against its sibling and search stayed
  dead for the life of the container. The entrypoint's wait list is the fix, and the lesson is to
  wait for every dependency an app connects to **without retrying**, not just its databases.

Also worth copying: the sign-in guard accepts **any** of the five identity providers Laminar
supports, with the variable groups mirroring upstream's own checks, and a partial group counting as
unconfigured. The first version demanded GitHub specifically, which would have forced a team on
Google or Okta to set `ALLOW_PASSWORDLESS_SIGNIN=true` and read as opting into an open deployment
when they had a perfectly good provider. When you gate on a provider, gate on all of them.

Two more, found at publish time. **The marketplace readme pipeline strips `<angle-bracket>`
placeholders as HTML, even inside backticks**, so `https://<frontend-domain>/api/auth/callback/github`
was published as `https:///api/auth/callback/github`. Use a bracket-free placeholder and read the
stored `readme` back after publishing. And on authentication to the API itself:
`~/.railway/config.json` keeps a four-character stub in `user.token` and the real bearer token in
`user.accessToken`. Queries for **published** templates succeed unauthenticated, which hides a bad
token until your first mutation returns "Not Authorized".

### 5.16 Building from source with no upstream image, without carrying a fork (Fabric)

Fabric publishes **no container image** — nothing on GHCR, and Docker Hub's `fabric` is
Hyperledger's — so building from source is unavoidable. Forking is not, and the two get conflated.

The template's repo started as a full fork of `danielmiessler/fabric`: ~190 MB of upstream history,
pinned at v1.4.451, with the Railway changes made as direct edits to upstream's Go files. That shape
has one failure mode and it arrived on schedule: because a bump means rebasing your edits across
upstream's history, it did not happen, and the template sat **27 releases and four months** behind.
Re-cut as a thin repo it is 228 KB and ten files — `Dockerfile`, `entrypoint.sh`, one patch,
`railway.json` and docs — and a version bump is two `ARG` lines.

- **Clone a pinned tag in the Dockerfile and assert its commit.** `git clone --depth 1 --branch
  v1.4.478`, then compare `git rev-parse HEAD` with a pinned `FABRIC_COMMIT` and fail the build on a
  mismatch. A tag is mutable; this makes a moved tag a build error instead of a silent substitution.
- **Carry your changes as patches, not edits.** `patches/*.patch` applied with `git apply --verbose`
  keeps the boundary between upstream's code and yours legible, and each hunk stays independently
  upstreamable. Fabric's patch is 84 lines and holds three things upstream has not taken: an
  unauthenticated `/health` route with its middleware exemption; `text/readystream` →
  **`text/event-stream`** on the streaming chat endpoint, which is a plain bug since upstream's own
  swagger annotation already says event-stream; and the CORS origin, hardcoded upstream to
  `http://localhost:5173`, moved to `FABRIC_ALLOWED_ORIGIN` (default `*`, safe here because auth is
  a header rather than a cookie).
- **Staying current is a security argument, not housekeeping.** The v1.4.451 → v1.4.478 bump picked
  up two upstream fixes for free: `requireAPIKeyForBind` refuses a non-loopback bind with an empty
  API key, and `APIKeyMiddleware` compares SHA-256 digests in constant time. It also required Go
  **1.26** per upstream's `go.mod`, so the previously hand-pinned Go 1.25.9 could not build it —
  pin the toolchain image as an `ARG` too, and expect to move it.
- **Some apps ignore the process environment.** Fabric reads providers and defaults from
  `~/.config/fabric/.env`, so the entrypoint has to write that file rather than pass variables
  through. Consequence worth documenting for deployers: provider keys then sit in plain text on the
  volume. Rewrite the file by grep-filter-and-append rather than `sed -i`, or a key containing a
  slash or backslash corrupts it.
- **Put deploy settings in `railway.json` at the repo root.** The reference project's service had
  `healthcheckPath` null while the published template carried `/health`; regenerating from that
  project would have silently dropped the healthcheck. A committed `railway.json` survives
  regeneration and keeps the two in step.
- **A reference project can disappear from under you.** Fabric's (`12b93c4f`) was gone when needed
  for a regeneration — not deleted by this session. Nothing warns you; the template keeps working
  because it is an independent object, but you cannot regenerate it. Treat the reference project as
  reproducible infrastructure, and keep enough in the repo (`railway.json`, documented variables) to
  rebuild it.
- Archive the history before re-cutting: `git bundle create ~/fabric_fork_archive.bundle --all`
  (148 MB, verified complete) restores the old fork with `git fetch <bundle> main` and a force-push.

---

### 5.17 A zero-input two-service template, and what nulls on generation (LongMemory)

LongMemory is a persistent memory store for LLM apps: a Node server exposing `/v1/*` and an MCP
endpoint, plus a separate Next.js dashboard. SQLite on one volume, no external database. It is the
lightest shape here that still ships a UI, and it published with **zero composer fields**.

- **Pin a commit, not a tag, when the tags lie.** Upstream's newest release was `v1.3.0` while
  `main` sat 155 commits ahead *and* `package.json` on `main` reported `1.0.0` — a lower version
  than the tag. There is nothing coherent to pin, so both Dockerfiles fetch one commit and assert
  its SHA. The GHCR image referenced by upstream's own compose file answers `DENIED`, so building
  from source was unavoidable; that is now the second template in this guide where "no published
  image" did **not** mean "fork it" (§5.16).
- **`LONGMEMORY_HOST=::` was the entire IPv6 fix.** The server reads a host variable and calls
  `.listen(port, host)`, and Node gives `::` a dual-stack socket, so no `socat` bridge was needed —
  worth checking before reaching for Laminar's pattern (§5.15). An IPv4-only default is reachable
  from the public edge and invisible to a sibling service.
- **The Next.js `HOSTNAME` trap, confirmed a second time.** Docker injects `HOSTNAME` exactly as
  Railway does, so the local rehearsal reproduced it for free: the container received
  `HOSTNAME=4d61371ef814`, the entrypoint's `export HOSTNAME=::` won, and `netstat` showed
  `:::3000`. Test this locally rather than discovering it in a failed deploy.
- **Literal defaults are nulled by generation; references survive.** `PORT=8080` and
  `OPENAI_API_KEY=""` both came back as `defaultValue: null, isOptional: false` — that is, REQUIRED
  fields with no value, which would have made a one-click deploy ask the user for a port number.
  `${{secret(48)}}` and `${{service.VAR}}` came through untouched. **The route to zero composer
  fields is therefore to bake constants into the image and delete the service variable**, then keep
  only generated secrets and cross-service references in the reference project.
- **`healthcheckPath` in `railway.json` is not captured by generation.** railway.json applies at
  deploy time; generation reads the service instance, which was still null. Set it explicitly with
  `serviceInstanceUpdate` before generating, and keep railway.json too so deployers get it either
  way (§5.16 records the same trap from the opposite direction).
- **A degraded feature that never errors needs measuring, not assuming.** Without an embedding
  credential the store falls back to a `synthetic` provider that sums SHA-256 digests of tokens into
  a vector. It never raises. Measured: a query repeating a memory's own words scored 0.656 and
  ranked first, unrelated memories all clustered at ~0.265, and in `strict` mode the confidence
  floor then hid the matching memory completely — "Rust borrow checker" returned an unrelated
  sentence about a cat and not the Rust one, while `associative` mode did return it. The entrypoint
  now selects a provider from whichever key is present and logs a loud warning otherwise, and the
  listing says plainly that recall is lexical until a key is set.
- **Check whether an app's `user_id` is a tenancy boundary before implying it is.** Here it is not:
  a recall issued as one `user_id` returned memories ingested under two others, all in one store
  with `worlds: 1`. That belongs in the listing's security notes, not discovered by a deployer.

### 5.18 Two authentication paths behind one gateway, for browsers and for agents (OpenKnowledge)

OpenKnowledge is an AI-native Markdown knowledge base: a collaborative browser editor over a real
git repository, plus an MCP endpoint that agents connect to directly. The server ships **no login of
its own**, so putting a public domain on it hands anyone who finds the URL full read and write. The
template therefore follows upstream's own documented recipe — three services, one of them public.

- **Caddy is the only public service, and it splits by path.** `/mcp*` is gated on a
  `Bearer {env.MCP_TOKEN}` header and proxied straight to the app; everything else goes through
  oauth2-proxy → Google → the app. Two paths because the two clients cannot share one: an agent
  cannot complete an interactive Google login, and a browser cannot reliably attach a static bearer
  to a WebSocket handshake. The editor's `/collab` socket is cookie-gated, which is precisely why
  upstream reaches for a cookie-issuing proxy rather than Basic auth. A two-service Basic-auth
  simplification looks obviously cheaper and quietly breaks collaborative editing — the UI survives,
  the socket does not.
- **A silent start/stop cycle with clean logs is a port mismatch, not a crash.** oauth2-proxy
  listens on `[::]:4180` by default, which upstream's recipe keeps. On Railway that deployed, logged
  a normal startup, then "Starting Container / Stopping Container" with **no error line anywhere**,
  because the healthcheck was probing the port the platform expected. Standardising every service on
  `:8080` fixed it. Read that failure shape as "nothing is listening where the probe looks".
- **An app-level Host allowlist makes a service unprobeable from the edge.** `OK_EXTERNAL_URL`
  doubles as an allowed-Host check, so a direct request to the app's own domain returns
  `403 urn:ok:error:host-not-allowed`. Spoofing `Host:` does not help — Railway's edge routes by
  Host, so the request simply arrives at the gateway instead. Reach the service from inside:
  `railway ssh -s <svc> -- node -e "fetch('http://localhost:8080/', {headers:{Host:'<gateway>'}})"`.
- **`${{secret(N)}}` takes an alphabet argument, and sometimes you need it.** oauth2-proxy requires
  a cookie secret of exactly 16, 24 or 32 **bytes** and refuses to boot otherwise, so the default
  alphabet is wrong here: `${{secret(32, "abcdef0123456789")}}`.
- **MCP over streamable HTTP is session-based, and a naive probe misreports it.** `tools/list` sent
  straight after `initialize` answers `-32000 Server not initialized`; parsing that with
  `result.get("tools", [])` yields **"0 tools"** and reads as a broken deployment. The real sequence
  is `initialize` → read the `Mcp-Session-Id` **response header** → `notifications/initialized` →
  then calls, every one carrying that header. Caddy passes it through untouched. Verify a tool
  actually runs, not just that the endpoint answers: a `write` followed by a `search` proves the
  store initialised on the fresh volume, which a successful `initialize` does not.
- **Bake a consent interlock rather than exposing it.** Upstream gates external binding behind an
  `OK_ALLOW_EXTERNAL` opt-in. Left as a service variable it would be nulled by generation into a
  required field asking the deployer to type `1` — so it is set in the image, the same manoeuvre as
  §5.17's constants, and the entrypoint still validates it.
- **Small things that stop a build or a boot**: a `VOLUME` instruction is rejected by Railway's
  managed builders; `git` is a hard boot requirement (the server runs a git preflight and the
  history subsystems shell out to the binary), which `node:24-slim` does not carry; and a first-boot
  `init` must be fed `< /dev/null` or it prompts and hangs the deploy.
- **Watch patterns belong in `railway.json`, not in the template.** A documentation-only commit
  rebuilt all three services and restarted the collaboration server, which drops live editing
  sessions. Patching `deploy.watchPatterns` into the published template through a change-set
  **applied cleanly and read back null** — the template config does not carry the field. Setting
  `build.watchPatterns` in each service's `railway.json` does work and is what deployers get.
  Measured: patterns are **relative to the repository root**, so a service whose `rootDirectory`
  is `/ok` needs `["ok/**"]`, not `["**"]`; a later docs-only push then reported **SKIPPED** on
  all three services instead of rebuilding them. Worth testing with a throwaway docs commit,
  because guessing the wrong base would leave a service that never redeploys at all.
- **Verify the one-click copy generates its own secrets.** Calling `/mcp` on the fresh deployment
  with the *reference project's* token returned 401 and with its own returned 200 — a two-request
  check that proves `${{secret(48)}}` regenerates per deployment rather than shipping a shared one.

---

### 5.19 An HTTPS-only upstream, and a same-origin check that a proxy quietly breaks (tlbx)

tlbx is a browser terminal multiplexer: persistent shells and coding-agent sessions, reached from a
phone or a laptop. It is the first template here whose value is a *workstation* rather than an app,
and the first whose upstream ships no container story at all — no Dockerfile, no compose, not one
occurrence of "docker" in its README or its 3234-line installer.

- **An app with no HTTP mode forces a gateway.** `app.Urls.Add($"https://{bind}:{port}")` is
  hardcoded. Railway's edge speaks plain HTTP to a container, so Caddy fronts it and dials
  `https://svc.railway.internal:8080` with `tls_insecure_skip_verify` — the certificate is
  self-signed and the hop never leaves the private network. The platform health check must be
  answered by Caddy itself (`/up`), because the probe is plain HTTP and would meet a TLS handshake.
  For the same reason the app service gets **no** `healthcheckPath` at all.
- **A reverse proxy can break a same-origin check without touching a header you wrote.** tlbx
  compares the browser's `Origin` against `request.Host` on scheme, host **and** port. Caddy's
  `transport http { tls }` rewrites `Host` to the upstream address, so every WebSocket the interface
  opens returned 403 while the page itself loaded perfectly — the editor appears and never connects.
  The fix is one line, `header_up Host {http.request.hostport}`; `{host}` is not enough because it
  drops the port. Caddy preserves `Host` by default over a *plain* upstream — verified with a
  header-echo container — so this is specific to the TLS transport, and it is invisible until you
  test a real WebSocket.
- **Read the status codes as a ladder.** What made that diagnosable was noticing three distinct
  answers: **401 unauthenticated, 403 origin refused, 400 origin accepted but handshake incomplete**.
  Chasing "403" alone produced two wrong theories (a stripped port, then a scheme mismatch). The
  moment 403 turned into 400, the cause was proven without reading another line of source.
- **Verify a WebSocket with a real client, not curl.** curl can reach 101 on a bare upgrade but
  cannot complete these handshakes, so it reports 400 where a browser succeeds. A ~25-line Node
  script using `https.request` and its `upgrade` event gives a true 101 plus the first server frame.
- **A workstation needs its writable paths on the volume, or it is a toy.** Anything `apt-get`
  installs at runtime is gone on the next redeploy; only the volume survives. So `HOME`,
  the npm global prefix and mise's data directory all live there, which is what makes
  `npm i -g` and `mise use -g python@3.13` persist. mise earns its place precisely because the image
  would otherwise be one-language-forever: it installs precompiled toolchains onto the volume with
  no compiler present (verified — Python 3.13 with working pip, no gcc in the image).
- **`/etc/profile` rebuilds `PATH` and discards the image `ENV`.** A login shell therefore lost the
  shims that every other shell had. The app spawns plain bash so its terminals were unaffected,
  which is exactly the kind of gap that ships unnoticed; a `/etc/profile.d` snippet closes it.
- **Some upstream features cannot survive the platform, and the listing must say so.** App preview
  serves from `PORT + 1` and hands the browser `scheme://<your host>:8081`, which Railway's edge
  never answers, with no setting to override the origin. Containers are not privileged, so nothing
  inside can run Docker. Both were verified rather than assumed — the container really does listen
  on 8081 — and both are written into the overview.
- **Generate the password instead of asking for it.** `${{secret(48)}}` made this a zero-input
  template *and* removed its worst failure mode: a required password field on a service that is a
  shell invites someone to type something short. The one-click copy proved it, refusing the
  reference project's password and accepting its own.

---

### 5.20 The easy case, and what makes it easy (codeg)

codeg is a multi-agent coding workspace: a Rust server and a Next.js UI in one image, SQLite on a
volume. It took a fraction of the effort of §5.18 or §5.19, and the reasons are worth naming,
because they are the checklist that predicts an easy template.

- **Upstream published multi-arch images with version tags**, so the template is `FROM
  xintaofei/codeg:0.30.7` and nothing else. No source build, no release-tarball unpacking, no
  per-arch case statement. Check Docker Hub and GHCR before assuming you must build (§3.1).
- **It speaks plain HTTP on a configurable port**, which is exactly what Railway's edge wants, so
  there is no gateway service at all. Compare §5.19, where an HTTPS-only upstream forced a Caddy
  front end and a same-origin trap, and the Electron app in the same family that could not run
  under Railway's seccomp profile at all.
- **It is fail-closed without being fussy.** With no `CODEG_TOKEN` it generates one at first boot
  and logs it rather than serving open; pinning `${{secret(32, "abcdef0123456789")}}` makes the
  value stable and readable from the service's variables, giving a zero-input template whose
  credential is still strong. That is the better half of §5.17's lesson: the deployer types nothing
  *and* gets no weak default.
- **`VOLUME` inherited from a base image is fine.** The catalogue's rule that Railway's managed
  builders reject `VOLUME` applies to the instruction in *your* Dockerfile. `xintaofei/codeg`
  declares `VOLUME /data` and a thin `FROM` of it built and deployed without complaint, with
  Railway's own volume mounted at the same path.
- **Not every app needs the `lost+found` dance.** codeg writes `codeg.db` straight into a
  root-owned mount root that already contains `lost+found`, with no chown, no subdirectory and no
  entrypoint. Test it rather than assuming the §4 workaround is always required.
- **Find the real health endpoint before setting one.** Every unknown `/api/*` path returns a
  plausible-looking `501 not_implemented`, which makes endpoint guessing useless, and `/api/health`
  answers **405 to GET**. It is `POST /api/health` → `{"status":"ok","version":"0.30.7"}`. The
  health check used here is `/`, which serves the static shell unauthenticated and returns 200.
- **Say plainly what does not survive a redeploy.** Agent CLIs installed at runtime live in the
  container layer, and codeg's in-place *Software Update* rewrites binaries there too — upstream's
  own compose file carries that warning. Both are in the listing, with "bump the pinned tag" as the
  durable path.

---

### 5.21 A desktop application run headless, and a volume that mounted but went unused (Orca)

Orca is an Electron agent development environment. Upstream ships desktop packages and a documented
`orca serve` mode, no server image, and scopes Remote Orca Servers to a private network. The
template installs the published `.deb` against a per-arch digest and runs that serve mode. Two
lessons here cost more than the rest of the build combined.

- **`gosu` and `su` reset `HOME` from `/etc/passwd`, which silently voids a volume.** `ENV
  HOME=/data/home` followed by `exec gosu orca ...` does not give the process that home: the target
  user's passwd entry wins, so it became `/home/orca`, inside the image layer. The volume mounted
  and stayed empty, and **every redeploy minted a fresh device token, server keypair and paired
  device id**, so the pairing URL people had saved stopped working. Fix: `useradd -u 1000
  -d /data/home -M`, so passwd itself points at the volume, and seed `/etc/skel` from the entrypoint
  on first boot, because `-M` skips it and `/data` does not exist at build time. Generalises to any
  entrypoint that drops privileges: **the volume-backed home must be set in passwd, not only in the
  environment.** The catalogue's older advice to export `HOME` is not enough (§7).
- **A local `docker restart` hides this completely.** Restarting keeps the container filesystem, so
  the state written into the image layer is still there and everything looks persistent. Only a
  fresh container — a real redeploy — exposes it. **Verify persistence by replacing the container,
  never by restarting it.**

Running Electron headless on Railway needs four things, and three of them fail in ways that do not
name the cause:

- **xvfb**: "headless" means no window, not no display. It is one of the package's own declared
  dependencies, so apt pulls it, but `xvfb-run` additionally needs **xauth**, which is only a
  *recommends* — with `--no-install-recommends` every launch dies with `xvfb-run: error: xauth
  command not found`.
- **libasound2 is required but not declared**: `error while loading shared libraries: libasound.so.2`.
- **`ELECTRON_DISABLE_SANDBOX=1` plus a non-root user.** Electron refuses to run as root without
  `--no-sandbox`, and Orca's CLI rejects unknown flags so that switch cannot be passed through. As
  non-root, Chromium's namespace sandbox needs `CLONE_NEWUSER`, which Railway's seccomp profile
  denies — proven by contrast, since the identical run succeeds under
  `docker run --security-opt seccomp=unconfined`.
- **The advertised address must be a `wss://` URL.** Orca passes `--pairing-address` through
  verbatim, so a bare hostname produces `ws://host:8080`, which Railway's 443-only edge can never
  serve. `wss://$RAILWAY_PUBLIC_DOMAIN` drops the port and uses TLS.

**Read upstream's own CI and reference docs before writing the Dockerfile.** Orca's repository
carries `config/docker/headless-pairing/Dockerfile` — a test harness, not a shippable image — and it
installs exactly `xvfb`, `xauth` and `libasound2t64`. `docs/reference/headless-linux-server.md`
lists the full Electron library set and notes that current builds start Xvfb themselves when
`DISPLAY` is unset. Both would have saved two failed boots. "Upstream ships no container image" is
not the same as "upstream has no Dockerfile"; grep for one before claiming either in a listing.

**A WebSocket 101 proves nothing about authorization.** The upgrade succeeds for anyone, which looks
alarming and is not: the transport is anonymous by necessity and the credential is presented inside
the encrypted channel (`e2ee_hello` → `e2ee_ready` → encrypted `e2ee_auth`, TweetNaCl box, framed as
`base64(nonce(24) || box.after(json, nonce, box.before(serverPub, mySecret)))`). Measured against
the live deployment with a hand-written client: a real device token answers `e2ee_authenticated`, a
tampered one `e2ee_error{code:"unauthorized"}`, and plaintext auth is closed with `4001`. **Speak
the protocol before concluding a server is open — or that it is closed.**

**Publish the reservation, do not bury it.** Upstream marks Remote Orca Servers beta and tells you
to keep server and client on a private path such as a tailnet or LAN; a Railway domain is public.
The token holds, and that is measured rather than assumed, but the listing says plainly that this
runs the feature further out than upstream intends, that the pairing URL is the whole credential and
is only readable from the deploy log, and that sessions end on redeploy.

---

### 5.22 A loopback-only web surface, a trust fence, and an agent that remembers (DSH + LongMemory)

DSH is DeepSeek Harness, DeepSeek's coding agent, with a browser UI. Three templates for it already
sat on the marketplace; all three were pinned to `0.1.0-rc.6`/`rc.7` from mid-August, and all three
wrapped it in Caddy basic auth on the premise that the app had no authentication. That premise was
true when they were built and false a week later: upstream shipped browser-session authentication,
a Host/Origin trust fence and a `--trusted-host` flag on 2026-08-24. This template pins npm
`latest` (`0.1.5-rc.1`), relies on the app's own auth, and pairs it with a LongMemory MCP server
from the §5.17 template so the agent has memory across sessions. Zero required composer fields.

- **Read the upstream's security model before deciding it has none.** `packages/client/connection`
  documents it precisely: every Host RPC method and WebSocket needs a browser session; each process
  mints a launch token; `GET /?token=` exchanges it once for a 30-day signed cookie bound to the
  request authority; the signing secret is a credential record in `$DSH_HOME/.credentials.yaml`.
  Before authentication, a trust fence on `/api` requires the request `Host` to be loopback or a
  trusted host, and any `Origin` to equal it — 403 on failure, 401 for trusted-but-unauthenticated.
  Measured through Railway's edge: `/` 401 without a cookie, token → 303 + `Set-Cookie`, then 200;
  WebSocket upgrade 101 with the cookie, 401 without, **403 with a foreign `Host` or `Origin`**.
- **"Refuses to bind 0.0.0.0" means the proxy lives in the same container.** `dsh --profile web`
  binds loopback only by design and rejects `--host 0.0.0.0`; a sibling Caddy service cannot reach
  it over the private network. So Caddy's static binary is copied from `caddy:2.11-alpine` into the
  Node image, runs as the same unprivileged user in the background under tini, answers `/up` for the
  health check, and proxies `127.0.0.1:7000`. If Caddy dies the health check fails and Railway
  restarts the container.
- **Pass `Host` through and register the public domain as trusted — this is the tlbx trap (§5.19)
  with a documented reason and a documented escape hatch.** The entrypoint appends
  `--trusted-host "$RAILWAY_PUBLIC_DOMAIN"` (plus `DSH_TRUSTED_HOSTS` for custom domains); Caddy's
  HTTP transport leaves `Host` alone by default. Note the fence applies to `/api`, not to the index
  route: probing `/` with a bad `Host` returns 401 (cookie authority mismatch), which looks like the
  fence is missing. **Probe the fenced surface, `/api/remote.mux`, or you will conclude wrongly in
  either direction.**
- **The launch token rotates per process; the cookie does not.** Because the signing secret lives
  on the volume, a cookie issued by container A authenticated on container B — HTTP 200 and a 101
  upgrade — while A's and B's launch tokens differed. Redeploys therefore do not sign users out.
  Proven by *replacing* the container, not restarting it (§5.21).
- **npm `latest` is an rc, and the built frontend is a separate package.** `@deepseek-ai/dsh` is
  49 KB; the servable GUI exists only because `dsh-web-app` hard-depends on
  `@deepseek-ai/dsh-web-frontend` (4.7 MB, 91 files). Check the dependency graph before assuming a
  CLI package ships its UI. Upstream has never published a stable release; tags are `dsh-v<semver>`
  with `-alpha.N`/`-rc.N`, so the manifest's `tag_pattern` must admit prereleases and the notes must
  say the pin moves every few days.
- **`node:22` already owns uid 1000.** `useradd -u 1000` failed with exit 4 (`node` exists). And the
  first build's "OK" was `docker build … | tail -4 && echo OK` — `tail`'s exit status masked the
  failure. **Never pipe a build into `tail`; redirect to a log and test the build's own status.**
  `userdel -r node` also left `/home/node` behind; remove the directory explicitly.
- **The MCP client's reconnect budget is shorter than a sibling's source build.** Defaults: 10
  attempts, 500 ms doubling to a 30 s ceiling — about 151 s, then it gives up until the next
  process start. `dsh` (npm install) reaches SUCCESS in ~2 min; LongMemory builds from source in
  2–5 min. On a one-click deploy the memory tools would silently never appear. The overlay sets
  `reconnect.maxAttempts: 240`; measured locally, tools were discovered after the server appeared
  200 s late, with the harness serving throughout. **Whenever a client dials a sibling at boot,
  compare its give-up time with the sibling's build time.**
- **Streamable-HTTP MCP with a bearer header is a config row.** `@deepseek-ai/dsh-mcp-client` with
  `transport: streamable-http`, `url`, `headers.Authorization` (both via `!!js process.env.*`),
  applied by the entrypoint as `--patch /etc/dsh/longmemory.cordis.patch.yml` only when
  `LONGMEMORY_MCP_URL` is set. The launcher flag must precede `--profile`. The client connects at
  boot and logs nothing on success, and LongMemory logs nothing per request — the handshake was
  made visible with a tiny logging forwarder between them: `initialize → 200`,
  `notifications/initialized → 202`, `tools/list → 200` (13 tools). **When two silent services
  talk, put a logging proxy between them rather than inferring from the absence of errors.**
- **Trademark hygiene is part of the listing.** Upstream's `BRAND_GUIDELINES.md` asks that project
  names avoid the full "DeepSeek Harness" mark and use "DSH"; two of the three existing templates
  use the full mark in their names, and the card image for this one deliberately uses no DeepSeek
  brand asset. The overview states "built on DeepSeek Harness; not affiliated with or endorsed by
  DeepSeek", which the guidelines explicitly permit.
- **Optional-but-empty is the right shape for a key the UI can take.** `DEEPSEEK_API_KEY` is
  `isOptional: true` with an empty default and a description pointing at *Settings → Models*; the
  harness resolves the key through its credentials store first and the environment second, and
  persists a UI-entered key on the volume. The entrypoint warns when it is empty rather than
  refusing to boot.
- **A one-click deploy orders services by variable references.** In the fresh copy both services
  were created in the same second, but `dsh` sat `QUEUED` while `longmemory` was `DEPLOYING` and
  went live 11 s after it — `dsh` references `${{longmemory.*}}`. So the memory server is already up
  when the client first dials on a one-click deploy; the raised reconnect budget covers redeploys
  and the reference project, where the ordering is not guaranteed.
- Platform notes: `variableUpsert` on a service triggers a redeploy (the rotated LongMemory key
  superseded an in-flight build); `serviceInstanceUpdate` of `rootDirectory` does too; and
  `caddy hash-password` needs a newline-terminated line on stdin or fails with `EOF`.

---

### 5.23 A daemon that exits when idle, binds one address family, and fences Host before health (Mirage)

Mirage is a virtual terminal for AI agents: a FastAPI daemon that owns "workspaces" — a virtual
filesystem over mounted resources (S3/R2/GCS, Postgres/Mongo/Redis, Notion/Slack/Gmail…) with an
in-process bash-like shell and git-style versioning — and exposes them over `/v1/*`. Upstream
publishes it as a PyPI package (`mirage-ai`), documents a shared-daemon `token` auth mode, and
leaves `/v1/health` open for probes. That passes the screening heuristic's third clause; it is
also the first candidate in this guide that is a *backend agents call* rather than a workstation.
One service, zero required fields. Four findings, each of which would have shipped a broken
template if assumed rather than measured.

- **Railway's health probe sends `Host: healthcheck.railway.app`.** The daemon validates `Host`
  *before* authentication, health check included, and an explicit `MIRAGE_ALLOWED_HOSTS` list
  *replaces* the loopback defaults. The first deployment built, started cleanly, and FAILED at the
  deploy stage with a log full of `rejecting request from 127.0.0.1: Host='healthcheck.railway.app'
  not in allowlist […]` and `GET /v1/health 400`. The entrypoint now trusts loopback,
  `healthcheck.railway.app`, `RAILWAY_PUBLIC_DOMAIN`, `RAILWAY_PRIVATE_DOMAIN` and
  `MIRAGE_EXTRA_HOSTS`. **Any app with a Host allowlist needs that probe hostname on it** — this is
  the platform fact behind every "healthy logs, failed health check" on such apps (§7).
- **uvicorn cannot bind dual-stack.** `--host ::` gave a listener that answered `[::1]` and
  refused `127.0.0.1` inside the same container: asyncio sets `IPV6_V6ONLY` on every AF_INET6
  socket it creates. `--host 0.0.0.0` is the mirror image and is invisible on Railway's IPv6-only
  private network (§5.17). Fix, measured three ways: uvicorn on `127.0.0.1:7000`, `socat
  TCP6-LISTEN:8080,ipv6only=0,fork,reuseaddr TCP4:127.0.0.1:7000` in front — v4 200, v6 200, and
  because socat relays TCP the `Host` header reaches the daemon untouched, so the allowlist still
  answered 400 to a foreign host through the relay. Laminar's pattern (§5.15), now with the reason
  it is needed for Python servers specifically.
- **The daemon exits when idle, and that cannot be turned off.** `MIRAGE_IDLE_GRACE_SECONDS`
  defaults to 30: when the last workspace is removed the registry arms a timer and the app
  SIGTERMs itself — measured: exit **143**, 8 s after `DELETE`. `0` means "exit immediately", not
  "never". The template bakes ten years (`315360000`; asyncio accepted it and a delete left the
  daemon serving) and sets `restartPolicyType: ALWAYS` so even that eventual exit is a restart,
  not an outage. **Read a daemon's idle/exit semantics before hosting it as a service.**
- **Live workspaces are not reloaded after a restart; commits are.** The registry is an in-memory
  dict; `state/`, `repos/` and `snapshots/` under `MIRAGE_HOME` persist on the volume. After
  replacing the container `GET /v1/workspaces` was `[]`, but recreating the workspace by id and
  `POST …/checkout {ref}` on the committed version brought `/hi.txt` back. The listing says
  "commit before you redeploy; uncommitted RAM state is gone" rather than "persistent
  workspaces".
- **Script runtimes need an `exec`-mode mount, and JavaScript needs a module the package does not
  ship.** On a `mode: write` mount `python -c` and `node -e` both fail with `root mount '/' is not
  in EXEC mode` (exit 126). On `mode: exec`, Python runs in-process on Monty; `node` fails with
  `the quickjs runtime needs a qjs-wasi.wasm module` until the quickjs-ng WASI build is present and
  `MIRAGE_QUICKJS_HOME` points at its directory. The image pins `qjs-wasi.wasm` v0.16.2 by digest
  under `/opt/quickjs` — `node -e "console.log(6*7)"` → `42`. **Test each advertised runtime, not
  the first one.**
- **Fail closed on the token.** Upstream's default `local` mode mints a token into a file a
  deployer can never read, and an empty token in `token` mode would be an open remote shell over
  mounted resources on a public URL. The entrypoint refuses to start on an empty or <16-char
  `MIRAGE_AUTH_TOKEN`; the template supplies `${{secret(48)}}`. Verified: 401 without or with a
  wrong bearer, 200 with the right one, 400 for a foreign Host, `/v1/health` 200 unauthenticated.
- **Not wrapped: the FUSE sandbox.** Upstream's only Dockerfile is a FUSE host needing `SYS_ADMIN`
  and `/dev/fuse`, which Railway does not grant; the daemon does not need it (the shell is
  virtual — no `uname`, no `/etc/passwd`, but `curl` and `git` are Mirage's own commands over the
  VFS). MCP is stdio-only, so no network pairing with DSH (§5.22) — a stdio child beside the agent
  would be the route.
- **Two harness slips worth recording.** A stale Python process on the Mac was already listening
  on the host port I mapped, answering every probe with `401 unauthorized` and `server: uvicorn` —
  indistinguishable from the daemon until `lsof -iTCP:<port>` named it; probe inside the
  container (`docker exec … curl [::1]:PORT`) rather than through a host mapping. And `${V:0:12}`
  is bash, not `sh` — "Bad substitution" inside `docker exec sh -c`; use `cut -c1-12`.

---

### 5.24 A published image with a constant root password, and an app whose own .env beats yours (Yao Agents)

Yao is a self-hosted hub for AI agents — workspaces, a task board, dashboard, Open API, built-in
MCP tools, desktop and Android clients. Upstream publishes multi-arch images (`yaoapp/yao`) and a
production Dockerfile, and `yao start` on an empty directory installs its own application,
migrates 28 tables into SQLite and creates a root user. The easy case (§5.20) — until the boot log
prints `Email: root@yaoagents.com / Password: Yao123++`.

- **A first-boot root password that is a constant is the template's problem, not the deployer's.**
  The bundled `scripts/setup.ts` has `ROOT_USER_PASSWORD = "Yao123++"` hard-coded; every
  installation gets it, and on a public URL that is an open admin until someone logs in and changes
  it. The fix is upstream's own model layer: `yao run models.__yao.user.UpdateWhere
  '::{"wheres":[{"column":"email","value":"root@yaoagents.com"}]}' '::{"password_hash":"<secret>"}'`
  bcrypt-hashes on save. The entrypoint runs `yao init` (installs, migrates, runs the setup hook,
  **exits** — 6 s), then rotates root from `YAO_ROOT_PASSWORD` before `yao start`, and re-applies it
  on every boot so the Railway variable is the source of truth. **Prove the rotation at the layer the
  login uses**: the browser login sits behind an OAuth guard *and an image captcha*, so it cannot be
  scripted; `bcrypt.checkpw` against the stored hash — upstream default False, ours True, and after a
  redeploy with a new secret the old one False — is the equivalent evidence. Say in the listing
  that the check stopped there.
- **The app's `.env` overrides your variables.** `config.go` loads `<app>/.env` with
  `godotenv.Overload`, so a Railway variable for any key in that file is silently ignored — measured:
  `YAO_PORT=8080 YAO_HOST=::` at first boot produced a `.env` with `5099`/`0.0.0.0` and the engine
  listened there. The entrypoint rewrites `YAO_ENV`, `YAO_HOST`, `YAO_PORT` (and `DEFAULT_LLM` when
  set) in the file on every boot; keys absent from the file — the provider API keys — pass through.
  **When an app generates its own env file, find out which side wins before promising that
  variables work.**
- **Yao binds one IPv4 address, and dual-stack attempts crash it.** `YAO_HOST` of `::`, `[::]` and
  `""` each logged `Listening :::8080` and then exited 1 with `Host :: not found` (a later component
  resolves the host string). `0.0.0.0` is invisible on the private network (§5.17). Same fix as
  Mirage (§5.23): engine on `127.0.0.1:5099`, socat dual-stack on the service port.
- **The mount root is not an empty directory.** `yao init` refuses a non-empty directory, and a
  Railway volume root holds `lost+found` — so the app lives in `YAO_ROOT=/data/yao`, a subdirectory.
  Not `/data/app`: that is the base image's `VOLUME` path, and a local `docker run` shadows it with
  an anonymous volume, which hides the bug in rehearsal (cf. §5.20's inherited-VOLUME note).
- **Read the licence before the Dockerfile.** Yao's is a *modified* Apache-2.0: branding and the
  certificate-verification logic must stay intact, and organisations with 50+ employees or over
  USD 1M revenue need a commercial licence. A template changes neither, but the listing must carry
  the clause verbatim rather than say "Apache-2.0".
- **Agents run commands in the container by default.** `YAO_HOST_EXEC` defaults to true with
  allow-lists (`FullAccess=false`); documented, with the switch to turn it off.
- Upstream's docs site returned 404 throughout (`yaoagents.com/docs`, and the homepage). The
  image, `yao --help`, the installed app tree and the Go source were the references — and were
  enough. Release cadence: `1.0.0-rc18`→`rc22` in one week, all tagged as releases.

---

---

**Published 2026-09-15 and one-click verified.** Template id `3b36bf75-e190-452b-9375-349092dee359`,
code `yao-agents`, category AI/ML, card image = the app's own 512×512 icon (`yao/data/icons/icon.png` at
the pinned tag's commit), readme `TEMPLATE_OVERVIEW.md`. Generation captured the source repo, healthcheck
`/`, ON_FAILURE, the domain and the `/data` mount (no size) and nulled `YAO_ROOT_PASSWORD` into a required
field; a change set renamed the template and restored `${{secret(24)}}` with a description, then
`templateVolumeUpdate` set 5120 MB. `railway deploy -t yao-agents` into a scratch project asked for
**nothing**, reached SUCCESS in about 90 s, reproduced healthcheck, restart policy, domain, the 5 GB volume
and a 24-character password; `/`, `/dashboard/auth/entry` and `/v1/user/entry` answered 200 through the
edge, the private IPv6 name answered 200 on 8080, and the bcrypt check on the scratch's root row was
`Yao123++` → False, its own `YAO_ROOT_PASSWORD` → True. Two things to know: upstream hashes passwords at
**bcrypt cost 4** (`$2a$04$`, the library minimum), so the random secret — not the hash — is what protects
a leaked SQLite file; and the interactive login was never exercised because `verify` is captcha-gated, so
the rotation is proven at the hash layer only.

### 5.25 A Compose-shaped image, a durable root that refuses symlinks, and a first-run land grab (HolyClaude Workstation)

[HolyClaude](https://github.com/CoderLuii/HolyClaude) (MIT, ~2.6k stars) is an AI coding
workstation in one container: Claude Code plus a browser UI (CloudCLI, upstream
`siteboon/claudecodeui`, **AGPL-3.0**), a headless Chromium with Playwright, eight AI CLIs and
around fifty dev tools, supervised by s6-overlay. Upstream publishes multi-arch images
(`coderluii/holyclaude:1.6.1`, ~13 GB unpacked) and sha-pins every build input, so the template is
a thin `FROM` plus one entrypoint. It is written for Docker Compose on a laptop or NAS — bind
mounts, a port on `127.0.0.1`, and a human who creates the account in the browser — and each of
those three assumptions needs an answer on Railway.

**There was already a `holyclaude` template on the marketplace**, published by someone else in March
2026: pinned to 1.1.3 (about thirty releases stale), **no volume at all** — so the Anthropic session,
the workspace and the account were lost on every redeploy — seven required literal prompts and no
healthcheck. Same situation as DSH: publishing a second one is justified when the difference is
persistence and a closed front door, not branding. The slug `holyclaude` was taken; the new one is
`holyclaude-workstation`.

**The volume mounts at the application's state directory, not at `/data`.** This inverts the usual
layout and it is not a preference. Upstream's persistence preflight fails closed with "Claude
durable state must be a real directory" when `~/.claude` is a symbolic link, and mounting over
`/home/claude` would hide the image-owned Claude Code binary at `~/.local/bin/claude` (upstream
checks for that too and exits with a message about it). So the mount point *is* `~/.claude`, and
everything else moves inside it: `DATABASE_PATH=/home/claude/.claude/.cloudcli/auth.db` puts the
account database there, and `/workspace` — whose name is fixed, because upstream's s6 service
script sets `WORKSPACES_ROOT=/workspace` literally — becomes a **symlink into the volume**. That is
safe because CloudCLI `realpath`s the workspace root before comparing project paths against it, so
both sides resolve to the same real path. **Read the upstream preflight before choosing a mount
point**: an app that validates its own state directory dictates the layout.

**THE EXPENSIVE FIVE MINUTES — the image's `WORKDIR` was the directory being replaced.** The
entrypoint swaps `/workspace` for a symlink, and the image declares `WORKDIR /workspace`, so the
entrypoint's own current directory was deleted underneath it. The boot then died with
`shell-init: error retrieving current directory: getcwd: cannot access parent directories`,
repeated by every later process, and finally `fatal: Unable to read current working directory` from
git — nothing pointing at the symlink swap, and the container exited 128 three minutes after the
actual mistake. Fix is one line, `cd /` at the top. **Generalises: before an entrypoint replaces,
renames or unmounts a directory, step out of it — an inherited `WORKDIR` counts as being in it.**

**A browser-registration first run is a land grab on a public domain — and the obvious fix loses a
race.** CloudCLI is single-user: the first `POST /api/auth/register` creates the account and every
later one returns **403**. Bound to `127.0.0.1` behind a Compose file that is a fine design. On a
Railway domain it means the first person to reach the URL — a scanner, anyone the link is forwarded
to — owns a machine holding the deployer's Anthropic session. So the template has to claim the
account itself.

The first implementation did that in a background loop started by the entrypoint: wait for
`/api/auth/status` to answer, then register. It worked locally, every time. **On Railway it lost.**
The first probe of the live deployment found `needsSetup: true`, and a deliberate `intruder`
registration against the public domain returned **200** — the verification created the very account
it was checking for. Two reasons, both structural: Railway's healthcheck passes the moment the
server listens, which is also the moment the edge starts routing, so "reachable" and "registerable"
begin together; and the deployment logs showed the background job never ran at all on Railway (no
`[railway] Created…` line, no warning either), so it did not survive the `exec /init` handoff to
s6-overlay the way it did under a plain `docker run`.

The shipped design removes the race instead of narrowing it: the entrypoint starts **its own
loopback-only instance** of the server on a throwaway port, registers `admin` with a
`${{secret(24)}}` password against that, stops it, and only then hands off to upstream's entrypoint
and s6. The public server therefore cannot exist in an unclaimed state, and the phase **fails
closed** — if the setup instance will not start or will not register, the boot aborts rather than
exposing an open one. Proven by polling the public port from the first moment it answers: its first
response is already `needsSetup:false`, and registration is 403. The body is piped into
`curl --data-binary @-` rather than passed as an argument, so the password never enters the process
table.

**Generalises twice over. Whenever an upstream's onboarding is "open the page and claim it", the
template must claim it first — and "first" has to mean before the listener exists, not merely
before a human gets there. A background task racing your own service is not a security control.**
It also generalises to verification: probing with a request that *would create state* is how this
was caught, which is worth doing deliberately — a read-only probe would have reported
`needsSetup: true` and left it ambiguous.

**An API-key variable that locks out the browser.** CloudCLI honours `API_KEY`, and it is tempting
to offer it as a second lock. It is applied as `app.use('/api', validateApiKey)` — *every* `/api`
request needs an `x-api-key` header, including the ones the web UI itself makes, so setting it
makes the product unusable. The template documents it as "do not set". **Check where a middleware
is mounted before advertising the variable that turns it on.**

**A useful accident: the JWT secret rides the same volume.** CloudCLI auto-generates its signing
secret into the database when `JWT_SECRET` is unset. With the database on the volume this needs no
variable and gives a property worth advertising — a session issued before a redeploy still
authenticates after it, measured with a token captured from the first container and replayed
against a replacement.

**Upstream's Compose hardening was not needed, and checking that mattered.** The quick-start Compose
file asks for `cap_add: [SYS_ADMIN, SYS_PTRACE]`, `security_opt: [seccomp=unconfined]` and
`shm_size: 2g` — none of which Railway grants. The image already bakes
`CHROMIUM_FLAGS=--no-sandbox --disable-gpu --disable-dev-shm-usage`, so a Playwright screenshot
succeeds in a container started with none of them, which is what was actually run rather than
assumed. Contrast §5.21, where Orca genuinely needed `ELECTRON_DISABLE_SANDBOX` because Railway's
seccomp denies `CLONE_NEWUSER`: same class of problem, opposite conclusion, and only a measurement
tells them apart. **Note the local-test limit: Docker Desktop's `/dev/shm` default is 4 GB, not
Docker's 64 MB, so the small-shm case cannot be reproduced on a Mac — it has to be read off the
real deployment, where it turned out to be 62 MB.**

**Two platform facts learned at publish time, both new since the previous template an hour
earlier.** First, `railway templates publish` now **rejects a marketplace readme that lacks a fixed
section skeleton**: `# Deploy and Host`, `## About Hosting`, `## Why Deploy`, `## Common Use Cases`,
`## Dependencies for` and `### Deployment Dependencies`. The error names the missing headings and
the publish does nothing, so the fix is mechanical — but a `TEMPLATE_OVERVIEW.md` written in the
older free-form style (every earlier template in this repository) will be refused on its next
publish. Second, **a service created through the GraphQL API with `source: {repo}` is not enough for
template generation**: `railway templates create` answers "Service holyclaude does not have a source
that can be used to generate a template", and pushing to the repo does not trigger a redeploy
either. `serviceConnect(id, input:{repo, branch})` wires the actual GitHub connection and both work
afterwards. Prefer the CLI's own `railway add --repo`, or run `serviceConnect` straight after
`serviceCreate`.

**THE FAILURE THE ONE-CLICK CAUGHT — Railway's healthcheck probes `PORT`, not the domain's target
port.** The first one-click deploy of the published template **failed**: eighteen attempts over the
full ten-minute window, every one "service unavailable", while the container log showed CloudCLI
ready four seconds after start and `ss` would have shown `*:3001`. The service had a domain with
`targetPort: 3001` and a `healthcheckPath` of `/`, and that is not what the probe uses — it uses the
port named by the **`PORT` service variable**, and with none set it finds nothing. Setting
`PORT=3001` on the service made the same deployment pass in about a minute.

Two things made this survive all the way to publish. The image sets `ENV PORT=3001`, which the
container sees but **Railway's control plane does not** — a Dockerfile `ENV` is not a service
variable. And the reference project had never caught it, because **`railway.json` was only applied
after `serviceConnect`**: before that its build logs contain no healthcheck section at all, so the
"SUCCESS" that had been treated as verification was a deploy with no healthcheck in it. A green
reference is not evidence that the healthcheck works — **grep the build log for `Starting
Healthcheck` before believing it**.

The fix has two halves, because declaring the variable alone leaves a trap: upstream's s6 service
script runs `cloudcli --port 3001` literally, so a deployer who changed `PORT` would move the probe
and not the server. The template therefore declares `PORT=3001` (a prefilled, non-blocking field)
**and** ships upstream's service script with `--port "${PORT:-3001}"`, verified by booting with
`PORT=8080` and watching the server listen on 8080.

**And the `/dev/shm` question, answered on the real deployment: Railway gives 62 MB**, close to
Docker's 64 MB default and nothing like Docker Desktop's 4 GB. A Playwright screenshot still
succeeded there, because the image bakes `--disable-dev-shm-usage` — so the conclusion above holds,
but only because it was checked on Railway rather than on the Mac.

Other notes. Upstream's image ends as `root` on purpose and drops to `claude` itself through
`s6-setuidgid`, so the wrapper must not add a `USER` line — the entrypoint needs root to chown the
volume. Exported variables reach the s6 services because their run scripts use
`#!/command/with-contenv`, which reads the environment s6-overlay snapshots from `/init`. The port
is fixed at 3001 (`cloudcli --port 3001` is literal in the service script), so the domain is created
with `targetPort: 3001`. And the size is real: ~13 GB unpacked is fine on Hobby and Pro but exceeds
the **4 GB image limit on Free and Trial**, which belongs in the listing rather than in a support
thread.

### 5.26 A published wheel that beats the upstream Dockerfile, and a resolver that cannot install it (Octop)

[Octop](https://github.com/TencentCloud/Octop) (MIT, Tencent Cloud, ~2.5k stars, **v1.0.0 GA on
2026-09-14** after roughly weekly releases) is a self-hosted multi-user AI assistant: one
FastAPI/uvicorn process serving a React dashboard, an HTTP/SSE/WebSocket API, IM channels (Feishu,
DingTalk, QQ, Discord, WeCom), cron, a RAG knowledge base, MCP connectors and ACP. Everything it
owns lives under `~/.octop` — SQLite control plane by default — so it is a genuine one-service, one-
volume template.

**Upstream publishes no image, and that turned out not to matter.** Docker Hub and GHCR have
nothing; upstream's `docker/Dockerfile` is a two-stage build that compiles the React dashboard with
npm and then installs the Python app. But **the PyPI wheel already contains the built dashboard** —
328 files, `index.html`, 212 JS/CSS assets, checked by listing the wheel — so the template installs
the released package and skips the npm stage entirely. **Check the published artefact before
reproducing an upstream build**: a wheel, a `.deb` or an npm package often carries the compiled
front end that the Dockerfile builds from source.

**THE INSTALL THAT CANNOT BE DONE WITH pip.** `pip install octop==1.0.0` fails after about nine
minutes with `pip._vendor.resolvelib.resolvers.ResolutionTooDeep: 200000` — the dependency graph is
past what pip's backtracking resolver will explore. Upstream never hits this because it only ever
installs with **uv** and a frozen lockfile (`uv sync --frozen`). `uv pip install octop==1.0.0`
resolves the same requirement in seconds. Then it fails again, differently: `evdev`, pulled in for
the remote-desktop input feature, is a C extension with no wheel, so the build needs
`build-essential` and `linux-libc-dev` (purged afterwards; the finished image is 996 MB).
**Generalises: when a Python project ships a lockfile and a `uv sync` in its own Dockerfile, that is
a statement that pip cannot resolve it — do not treat the resolver as interchangeable.**

**The IPv4/IPv6 split, for the third time.** Octop is uvicorn on asyncio, so it cannot bind both
families: measured in a container, `--host ::` answers on `[::1]:8088` and **not** on
`127.0.0.1:8088`, while `--host 0.0.0.0` is invisible on Railway's IPv6-only private network. Same
resolution as §5.23 and §5.24 — the server runs on `127.0.0.1` and
`socat TCP6-LISTEN:${PORT},ipv6only=0,fork,reuseaddr TCP4:127.0.0.1:${OCTOP_PORT}` fronts it, which
serves both families and passes `Host` through untouched. This is now a standing pattern for any
Python web app on Railway, not a per-project discovery.

**An upstream that already solved the admin-password problem, and a reason to override it anyway.**
Unlike §5.24's Yao, Octop's own container entrypoint generates a random password when
`OCTOP_DEFAULT_PASSWORD` is unset and writes it to `~/.octop/credential.txt` (their issue #502).
That is safe, but on Railway the only way to read that file is an SSH session, so the template hands
Octop a `${{secret(24)}}` instead and refuses to start without one — the password then lives in the
service variables where the deploy UI shows it. A Railway-style secret passes Octop's policy
(≥8 chars, letters and digits, plus a weak-password blacklist), verified by running `octop init`
with one before building anything.

**Deliberately *not* re-applied on every boot**, which is the opposite of the Yao decision and for a
good reason: Yao's image shipped a *known constant* password, so re-applying was the only safe
option; Octop's is unique per deployment and changeable in the web console, and upstream's own
credential file says "if you changed the password inside the Web console, that password wins".
Re-applying would silently revert a user's password change on the next redeploy. **Match the
upstream's own semantics when it has them; only override when the default is unsafe.**

**`railway.json`'s healthcheck was never applied — measured, not suspected.** The reference
deployed SUCCESS with **zero** healthcheck lines in its build log. The deployment's
`meta.propertyFileMapping` *does* list `deploy.healthcheckPath` → `$.deploy.healthcheckPath`, so the
file was found and parsed, yet `meta.serviceManifest.deploy.healthcheckPath` and the service
instance's `healthcheckPath` both read **null**. Setting it with `serviceInstanceUpdate` fixed it,
and the next deployment logged `Starting Healthcheck` → `Path: /api/health` → five
"service unavailable" retries → **`[1/1] Healthcheck succeeded!`** in 33 s (the app needs ~15 s to
boot). This is the same trap as §5.25 seen from the other side, and the rule is now unconditional:
**set `healthcheckPath` on the service instance, and grep the build log for `Starting Healthcheck`
before believing a green deployment exercised one.** It also makes generation capture the value.

Security posture is better than most of this portfolio: unauthenticated `/api/agents` returns
**401**, a wrong password on `/api/auth/login` returns **401** and the generated one returns **200**
with a JWT, and upstream ships real **login rate limiting** (`login_max_attempts: 5`,
`login_lockout_seconds: 900`). The JWT signing secret is 32 random bytes generated on first boot
under `~/.octop`, so it rides the volume — a token minted before the container was replaced still
authenticated against the replacement. The standing caveat applies unchanged: agents run shell
commands, so the blast radius of the URL is a shell.

### 5.27 A scratch image with no shell, and the first Go service that needed no relay (Coddy)

[Coddy](https://github.com/coddy-project/coddy-agent) (MIT, ~145 stars but several releases a day —
1.1.32 landed during the build) is a general-purpose agent in one static Go binary: a web UI, an
OpenAI-compatible `/v1/*` API, a `/coddy` REST surface, a cron scheduler, a swarm relay and a remote
mode, all sharing one set of sessions. Upstream publishes `ghcr.io/coddy-project/coddy-agent`, and
the finished template image is **57 MB** — the smallest in this portfolio by an order of magnitude.

**The published image is `scratch`, which is a problem to solve rather than a base to inherit.**
A `FROM scratch` image has no shell, so there is nowhere to validate credentials, create the volume
subdirectories, or do anything else before the server starts. The resolution is the reverse of the
usual thin wrapper: instead of `FROM <upstream>` plus an entrypoint, copy the binary *out* —
`COPY --from=ghcr.io/coddy-project/coddy-agent:1.1.32 /bin/coddy /usr/local/bin/coddy` onto
`alpine:3.22`. Nothing upstream builds is rebuilt, and the wrapper gets a shell. **Generalises: a
distroless or scratch upstream is still usable; take the binary rather than the image.**

**The first Go service here that did not need socat.** Three templates in a row (§5.23, §5.24,
§5.26) had to put a relay in front because their runtime could not bind both address families.
Coddy is Go, and `coddy serve -H ::` logs `addr=[::]:8080` and answers on **both** `127.0.0.1` and
`[::1]` — measured from a sidecar sharing the container's network namespace, because a `scratch`
image has no shell to curl from. Go's `net.Listen` does not set `IPV6_V6ONLY`; Python's asyncio
does. **Test the bind before reaching for the relay: the pattern is a property of the runtime, not
of Railway.**

**Authentication is off by default, and the template makes it mandatory.** Upstream is explicit —
"Authentication is off by default (historical behavior)" — and logs a "reachable without
authentication" warning when binding a non-loopback address without a token. On a public domain that
warning describes an agent with shell tools open to whoever finds the URL, so the entrypoint fails
closed on both credentials rather than passing the warning along:
`CODDY_HTTP_TOKEN` (bearer, gates `/v1/*` and `/coddy/*`) and `CODDY_HTTP_PASSWORD` (the browser's
credential for the same gate, since the SPA has no field to type a token into). Both are
`${{secret(...)}}`, so the deploy form still asks for nothing.

Upstream's own handling of the password is worth copying elsewhere: it is hashed with **argon2id**
as the server starts, never written into `config.yaml`, and there is **deliberately no
command-line flag** for it — the docs say "a password on a command line is visible in `ps`". The
template passes it as an environment variable for the same reason, and pipes nothing through `ps`.

Measured through Railway's edge, not read from the docs: `/v1/models` **401** with no token and
**401** with a wrong one, **200** with the right one; browser sign-in **401** on a wrong password and
**200** on the right one with an `HttpOnly; SameSite=Strict` cookie; and a sign-in attempt without a
matching `Origin` refused **403 `cross-site sign-in refused`** — CSRF is handled upstream.

**`ALWAYS`, not `ON_FAILURE`.** Killing the server in rehearsal produced **exit 0** — a clean
SIGTERM shutdown — and `ON_FAILURE` does not restart a zero exit. For a process that should never
end on its own, `ALWAYS` is the correct policy, and the difference only shows up if you actually
kill the thing and read the exit code. **Check what your service does on SIGTERM before choosing a
restart policy.**

Smaller findings. `coddy serve` starts with **no `config.yaml` at all** (verified on an empty
volume) and writes none; a provider key in the environment is enough for a real model to appear in
`/v1/models` — so the template ships no config file and no seeding logic. The healthcheck is `/`,
which stays public by design "so a client can load and prompt for the token", while `/docs` and
`/openapi.json` are gated with everything else. And the published image is built **without** the
`gateway` build tag, so the Telegram gateway is absent from this template — stated in the listing
rather than worked around, since fixing it would mean building the Go project instead of pinning
upstream's artefact.

## 6. Documentation set

**Check every documented default against the generated template config, not against your intent.**
OpenKnowledge's overview and README both stated that `OAUTH2_PROXY_EMAIL_DOMAINS` defaults to `*`.
Nothing set it: the image did not, and generation had turned the reference project's placeholder
into a required composer field with no value. A placeholder you typed into the reference project
reads like a default while you are building and is a blank box to the deployer. The variables
table in the docs should be generated from, or diffed against, `serializedConfig`.

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

Build / verification discipline
- **An app that generates its own `.env` may load it with `Overload`** — the file then silently beats every Railway variable for the keys it contains (Yao). Measure which side wins before documenting variables; patch the file from the entrypoint when the file wins (§5.24).
- **A constant first-boot admin password is the template's problem.** Rotate it from a generated secret before the server accepts connections, and prove it at the hash layer when the login is captcha-gated (§5.24).
- **Railway's health probe sends `Host: healthcheck.railway.app`.** An app with a Host allowlist (Mirage, DSH-style trust fences) must include it or every deployment fails its health check with 400s in an otherwise healthy log (§5.23).
- **Python servers cannot bind dual-stack under asyncio** (`IPV6_V6ONLY` is forced on `::`; `0.0.0.0` is invisible on the private network). Bind loopback and put `socat TCP6-LISTEN:PORT,ipv6only=0,fork,reuseaddr TCP4:127.0.0.1:INNER` in front; `Host` survives the relay (§5.23).
- **Read a daemon's idle/exit semantics before hosting it.** Mirage SIGTERMs itself 30 s after its last workspace by default and `0` means exit-now; bake a huge grace and `restartPolicyType: ALWAYS` (§5.23).
- **Never pipe a build into `tail`** (`docker build … | tail -4 && echo OK`): the pipeline's status is `tail`'s, so a failed build prints OK. Redirect to a log and test the build command's own exit status (§5.22).
- **Probe the surface a control actually guards.** DSH's Host/Origin fence covers `/api`, not `/`; a bad-Host probe of `/` returns 401 and looks like the fence is absent. Read which routes a check applies to before declaring it present or missing (§5.22).
- **When two silent services talk, put a logging forwarder between them.** Neither DSH's MCP client nor LongMemory logs a successful handshake; a 30-line HTTP forwarder made `initialize → 200`, `initialized → 202`, `tools/list → 200` visible (§5.22).
- **Compare a boot-time client's give-up time with its sibling's build time.** DSH's MCP client retried for ~151 s by default while LongMemory's source build takes 2–5 min; on a one-click deploy the tools would silently never appear. Raise the budget in the client's config (§5.22).

Volumes / processes
- `lost+found` at the mount root: `PGDATA` subdirectory; Redis 8.2 skips permission fixes (rmdir it); apps as non-root can't write (chown as root, then drop).
- Stale pid file with pid 1 on a volume blocks the next start.
- `setpriv`/`gosu`/`su` set `HOME` from the **target user's `/etc/passwd` entry**, overriding an
  inherited `ENV HOME`. Exporting `HOME` is not enough: give the user that home with
  `useradd -d <path>`, or the process writes to the image layer while the volume sits empty (§5.21).
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
- Generated templates omit literal defaults; fill them through authenticated template change sets (§3.9.1) or a one-per-line manual composer handoff.
- `${{secret(N)}}` with N = the exact length the app requires (32 for AES-256 keys).
- Read the definition back: aliases vs. real keys, leading spaces, single-space defaults, empty vs optional.
- Referenced values are empty until the referenced service deployed once.
- Empty optional variables are omitted at deploy; `" "` is not empty.
- Read a config default out of the **pinned image**, not the repo: a project's compose and packaged config can be ahead of the published tag (a different JPA provider, an image tag that does not exist yet).
- A Spring Boot app needs no baked config: relaxed binding maps `a.b.c-d` to `A_B_C_D` and environment beats the packaged `application.yml`. Point `SERVER_PORT` at Railway's port.
- An app's `/actuator/**` or equivalent admin path may sit behind its own auth and answer the platform healthcheck with 401. Open just the health path, or use a route the app already leaves unauthenticated.
- Credentials read from a file inside the image, rather than a database, must be rewritten by the entrypoint from a generated variable, or every deployment ships a known password. Warn that in-app password changes then do not survive a redeploy.
- Assert on response bodies: some APIs answer a failed login with HTTP 200 and an error code in the payload.
- `healthcheckPath` allows only `[A-Za-z0-9_/]`: a dot or hyphen is rejected outright, and `null` does not clear the field while `""` does. A redirect fails the check, so never point it at a root that bounces to a login page.
- A failing healthcheck keeps the domain 404ing for the whole timeout, so clear it, deploy, probe, then set the path you verified.
- `serviceInstanceDeployV2` rebuilds the service's existing commit; pass `commitSha` or you silently redeploy the old code. Confirm with `deployments { meta }`.
- After `serviceCreate` with a repo source, call `serviceConnect(id, input:{repo, branch})` or template generation refuses the project.
- Check what an app's login does with **no** identity provider configured. More than one accepts any email with no password in "self-hosted" mode, which is unacceptable on a public URL: require OAuth and have the entrypoint refuse to boot without it.
- A server hardcoded to `0.0.0.0` is unreachable over the private network. A `socat` `TCP6-LISTEN` bridge in the wrapper fixes it without patching the app. For Node, `HOSTNAME` is injected at run time and must be exported in the entrypoint, not set in the Dockerfile.
- `${{secret(N, "alphabet")}}` takes a second argument, which is how you generate hex or base64-safe keys.
- The marketplace readme strips `<angle-bracket>` placeholders as HTML even inside backticks, silently mangling example URLs. Use `YOUR-DOMAIN`-style placeholders and read the stored `readme` back.
- The API access token in `~/.railway/config.json` expires after about an hour and mutations then fail with "Not Authorized", exactly like a scope problem. Run any `railway` CLI command to refresh it first.
- There is **no** way to refresh a published template's config in place: `templateGenerate` takes only a projectId. To change a template's services and keep its marketplace code, delete the old template then publish the regenerated one, which slugs the same name back to the same code. Check the deployment count first.
- Wait for every dependency an app connects to **once at boot without retrying**, not just its databases: losing that race disables the feature for the life of the container, usually silently.
- Verify a feature, not the presence of its service. A missing search backend can return an empty result set that reads as "no matches".
- Template generation **preserves** `${{...}}` references and `${{secret(N)}}` but nulls literal defaults. Wire inter-service hosts/URLs/shared secrets as references. Bake fixed constants into wrapper images or restore them as defaults through change sets/the composer before publishing; deployers should not have to type internal wiring or Dockerfile paths.
- Build a repo service from a subdirectory with `serviceInstanceUpdate(input:{rootDirectory:"<svc>"})` (preserved in the template) instead of a `RAILWAY_DOCKERFILE_PATH` variable (nulled into a required composer field).
- Finalize a template name through `metadata.name` in a template change set or the dashboard before publication. Renaming the reference project and regenerating was an older workaround, not a requirement. Publish slugs the name into the code.
- Template code = name slug; rename before publishing; publish description ≤ 75 chars; `--image` must be a URL.
- `railway deploy -t` prompts for missing defaults and fails without a TTY, even for OPTIONAL fields (`-v svc.KEY=val`).
- `serviceInstanceUpdate` and `serviceDomainUpdate` return a **Boolean**, not an object — a `{ id }` selection set fails to parse. `serviceDomainUpdate` needs the whole input (`serviceDomainId`, `serviceId`, `environmentId`, `domain`, `targetPort`), not just the changed field.
- Deep-nested `railway api` GraphQL is easy to mis-brace on the shell; write the query to a file and check `{` vs `}` counts before sending.
- GitHub user-attachment banners are not stable image URLs (403 for curl, expiring signed redirect); commit a copy to the template repo and use its raw URL.
- Reference values such as `${{EVEROS_LLM__API_KEY}}` survive template generation; every literal default does not. Verify with the read-back which of your composer entries actually stuck.
- `serializedConfig` holds the real modern template service definition; legacy `config` can be just `{plugins:[]}`.
- Set volume size explicitly. Also verify resource caps, replicas, sleeping, watch patterns and backup schedules in the actual fresh deployment, not only the reference project or authoring file.
- Omitted native-IaC variables can mean **delete**, not preserve. Use explicit `preserve()` markers; for arbitrary owner names, fail closed on scoped discovery errors and retain the native destructive-apply guard.
- API/CLI success may precede provisioning, volume attachment or SSH readiness. Poll the workflow and resource state before retrying mutations; do not create duplicates because one readback is stale.
- **Literal variable defaults are nulled by template generation; `${{secret()}}` and `${{service.VAR}}` survive.** A literal becomes `defaultValue: null, isOptional: false`, i.e. a required composer field with no value. Bake constants into the image and delete the variable to reach zero-input templates.
- `healthcheckPath` set only in `railway.json` is not captured by generation; set it on the service instance too.
- `--image` rejects `avatars.githubusercontent.com` with "Invalid image URL", with or without `?v=4`; `raw.githubusercontent.com` is accepted.
- Build GraphQL bodies in Python, never by shell interpolation. A `"$SVC:PORT"` loop corrupted a serviceId into a filesystem path and the API answered **"Not Authorized"**, which reads as a permissions failure and is not; the same mutation with a Python-built body succeeded.
- Template **name** editing lives only on `backboard.railway.com/graphql/internal` (`templateChangeSetStage`/`Apply`); `/graphql/v2` does not know `TemplatePatch`.
- That internal endpoint **403s a bare scripting user-agent**. `templateChangeSetStage` needs `Origin: https://railway.com`, a `Referer` and a browser `User-Agent`, or it returns HTTP 403 — which reads as an auth failure and is not.
- `templateVolumeUpdate(templateId, serviceId, volumeId, sizeMB)` returns `Template!`, so it needs a selection set, the same trap as `templatePublish`. The template's volume id lives in the template's own id namespace, not the project's.
- **Read `serializedConfig` at the right depth.** Healthcheck is `services[id].deploy.healthcheckPath` and the public domain is `services[id].networking.serviceDomains`; a flat read of `services[id].healthcheckPath` returns null for a perfectly good template and looks like generation dropped them.
- Daily volume backups: `volumeInstanceBackupScheduleUpdate(volumeInstanceId, kinds:[DAILY])`. The enum is **`VolumeInstanceBackupScheduleKind`** (DAILY/WEEKLY/MONTHLY), it takes the **volumeInstanceId** not the volumeId, `VolumeInstanceUpdateInput` carries no backup field, and nothing on `VolumeInstance` reads the schedule back.
- `builder: "DOCKERFILE"` is **not** a valid `ServiceInstanceUpdateInput` value and returns "Problem processing request". Omit it; railway.json declares the builder and Railway detects the Dockerfile. Isolate one field at a time when a whole input is rejected.
- A reference volume provisions at the **plan default** (50 GB observed on Pro). Always set the template's size with `templateVolumeUpdate`.
- **That 50 GB is a ceiling, not a bill.** `sizeMB` is the cap; `currentSizeMB` is real consumption, and the billed metric is `DISK_USAGE_GB` — so an over-large allocation costs nothing by itself.
- **Volumes grow, they never shrink.** Railway supports **live resize with zero downtime** on paid plans, self-serve up to 1 TB on Pro: the storage expands under a running service and the filesystem extends itself (a volume at 100% capacity gets an offline resize instead, for integrity checks). **Down-sizing is not supported.** So pick template volume sizes *small*: a deployer who needs more drags a slider, while one who is over-provisioned can never claw it back. There is no resize field in the public API (`VolumeUpdateInput` carries only `name`) — it is a dashboard action; `templateVolumeUpdate` only sets what a fresh deploy provisions.
- **`gosu`/`su` reset `HOME` from `/etc/passwd`, which can silently void a volume.** `ENV HOME=/data/home` + `exec gosu app ...` gives the process the *passwd* home instead, so state lands in the image layer and every redeploy discards it. Set the home in `useradd -d`, not only in ENV. **A local `docker restart` hides this** — it keeps the container filesystem, so only a fresh container (a real redeploy) exposes it.
- **A WebSocket 101 proves nothing about authorization.** A server may upgrade anyone and then demand a credential *inside* the channel — Orca does exactly that, and a bogus token is dropped with close code `4001`. Speak the protocol before calling a service open or closed.
- **`VOLUME` is only rejected in your own Dockerfile.** A base image that declares `VOLUME /data` builds and deploys fine through a thin `FROM`, with Railway's volume mounted at the same path.
- **`railway.json` / `railway.toml` config-as-code is deprecated.** The CLI now prints a migration notice pointing at Infrastructure-as-Code (`.railway/railway.ts`, `railway config migrate`). Every template in this repository still ships railway.json; plan the migration rather than discovering it when support ends.
- `${{secret(N, "alphabet")}}` takes a second argument. Needed when a consumer validates byte length against a fixed set, e.g. oauth2-proxy's 16/24/32-byte cookie secret: `${{secret(32, "abcdef0123456789")}}`.
- "You have been blocked from publishing templates" is **workspace-wide**, triggered by one template Railway has hidden administratively, and blocks readme edits to every other template. Nothing in the API says which template or that it is hidden. Only Railway lifts it; ask on Central Station.
- Publish through `railway templates publish|update`. A hand-rolled `templatePublish` against the public GraphQL endpoint kept reporting that block after it had been lifted; expiry, User-Agent and template identity were all ruled out.
- **Check your own notes before deciding a problem is new.** Three earlier templates had been published with the CLI and the exact command was recorded; reaching for schema introspection and a raw mutation instead is what produced the false signal above.

Repos / CLI
- `railway add -r` "You do not have access" → `serviceConnect`; new repos may be "Not Authorized" for ~40 min.
- `railway variables/logs/ssh` need the linked directory.
- Without watch filters, every push can rebuild connected services. Copy explicit per-service watch patterns into the published template to avoid restarting user sessions for README edits.
- Watch patterns cannot be carried by the template: a `deploy.watchPatterns` change-set applies and reads back null, but `deploy.healthcheckPath` through the same change-set **does** stick — so a silent no-op is per-field, not a rule about change-sets. Read back after every patch. Put `build.watchPatterns` in each service's `railway.json` instead, **relative to the repository root** (`["ok/**"]` for a service rooted at `/ok`), and prove it with a docs-only push — the deployments should report `SKIPPED`.
- Keep project names short and abort after any failed `railway init`; confirm the exact intended linked project ID before template provisioning so a parent directory's link cannot receive the deployment.
- Sub-agent reports can get lost; have them write files.
- **Before `git add` in a new folder, `git rev-parse --show-toplevel` must print that folder.** A parent directory that is itself a git repo swallows the add: `git init` guarded by `--is-inside-work-tree` did not run, and `git add -A` pushed 455 workspace files (agent config, memory notes, other projects) to a brand-new public repo. `git init` unconditionally in the new directory; prefer explicit paths over `-A` in directories you did not create this session.
- **An inherited `WORKDIR` counts as being in the directory you are about to replace.** An entrypoint that swapped `/workspace` for a symlink deleted its own cwd; the boot then failed with `getcwd: cannot access parent directories` from every later process and exited 128 three minutes later, nothing pointing at the cause. `cd /` first.
- **When an upstream's first run is "open the page and create an account", the template must create it first — before the public listener exists.** Single-user apps hand ownership to whoever reaches the URL first. A background job that registers while the real server starts is not enough: Railway's healthcheck passes the moment the server listens, which is the moment the edge starts routing, and on Railway that job did not survive the `exec /init` handoff to s6-overlay at all. Start a loopback-only instance during the entrypoint, register against it, stop it, then start the real one — and fail closed if that does not work. Pipe the body into `curl --data-binary @-` so the password never enters the process table.
- **An app that validates its own state directory dictates the mount point.** HolyClaude refuses to start when `~/.claude` is a symlink, so the volume mounts *there* and the workspace symlinks into it — the reverse of the usual `/data` layout. Read the upstream preflight before choosing where the volume goes.
- **`railway templates publish` now requires a fixed readme skeleton.** Missing `# Deploy and Host`, `## About Hosting`, `## Why Deploy`, `## Common Use Cases`, `## Dependencies for` or `### Deployment Dependencies` fails the publish with the list of missing headings. Free-form overviews that published fine earlier will be refused on their next publish.
- **The healthcheck probes the port in the `PORT` service variable — not the domain's target port, and not the image's `ENV PORT`.** A Dockerfile `ENV PORT=3001` is invisible to Railway's control plane. Without a `PORT` variable the probe finds nothing and the deploy fails with "service unavailable" on every attempt while the app is listening correctly. Declare `PORT`, and make sure the server actually honours it.
- **`pip` cannot install every Python project.** Octop's dependency graph exceeds pip's backtracking resolver — `ResolutionTooDeep: 200000` after ~9 minutes — while `uv` resolves it in seconds. When an upstream Dockerfile uses `uv sync --frozen`, treat that as a statement that pip will not work, not a style choice.
- **A `scratch` or distroless upstream image is still usable — copy the binary out, do not inherit the image.** With no shell there is nowhere to validate credentials or prepare the volume before the server starts. `COPY --from=<upstream> /bin/app /usr/local/bin/app` onto a small base keeps upstream's artefact and gains an entrypoint.
- **Check what the process does on SIGTERM before choosing a restart policy.** A clean shutdown exits `0`, and `ON_FAILURE` does not restart a zero exit — a server that should never end on its own wants `ALWAYS`. Kill it in rehearsal and read the exit code rather than assuming.
- **Not every runtime needs the socat relay — test the bind.** Go's `net.Listen` on `::` is dual-stack (both `127.0.0.1` and `[::1]` answer); Python's asyncio sets `IPV6_V6ONLY` and is not. The relay is a property of the runtime, not of Railway.
- **Check the published artefact before reproducing an upstream build.** Octop's PyPI wheel already contains the compiled React dashboard, so the two-stage npm build in upstream's Dockerfile was unnecessary — a wheel, `.deb` or npm package often ships the front end pre-built.
- **A green reference deployment does not prove the healthcheck works — and `railway.json`'s healthcheck may never be applied at all.** Measured on two separate templates: the deployment's `meta.propertyFileMapping` lists `deploy.healthcheckPath`, so the file was parsed, yet `serviceManifest.deploy.healthcheckPath` and the service instance's `healthcheckPath` both come back **null** and no healthcheck runs — the build log has no `Starting Healthcheck` section. Set the healthcheck on the service instance with `serviceInstanceUpdate` (which also makes template generation capture it), and grep the build log for `Starting Healthcheck` before treating a SUCCESS as healthcheck evidence.
- **A service created with `serviceCreate(source:{repo})` over the API is not connected to GitHub.** It builds once, but pushes do not redeploy it and `railway templates create` refuses with "does not have a source that can be used to generate a template". Run `serviceConnect(id, input:{repo, branch})` afterwards, or use `railway add --repo`.
- A force-push does **not** remove a leaked commit from GitHub: it stays fetchable by hash (`repos/<r>/commits/<sha>`, full tree) and the repo activity feed lists the old hash next to the new one. Make the repo private at once, then delete and recreate it (`gh auth refresh -s delete_repo`, `gh repo delete`, `gh repo create`, push) and verify the old hashes return 404.
- cognee `remember` may hang after finishing server-side; retry is a 1-second dedup.

Docs
- Copy nothing from upstream READMEs without checking the CLI: flags drift (`--patch` vs `patch`).
- Claims about auto-behaviour (demo cleanup, migrations, trusted proxies) must be tested, not inferred from one code path.
- A model adapter, healthy app or valid template does not prove live provider credit/audio/AI generation. Record what was actually exercised and which provider paths remain unverified.
- Distinguish Save from export, file persistence from process persistence, and local CLI commands from remote shell commands. Link the included software and explain first use rather than supplying only an architecture summary.
- A saved published overview may be hidden by a cached public page. Compare public API readback and a cache-busted page before treating it as a failed metadata update.

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
| Pipecat | `pipecat` | one Python 3.12.14 / Pipecat 1.8.1 service: session API, bot processes and bundled browser client; external Daily WebRTC; no database or volume | Password-protected, one active session/worker/replica; OpenAI/Gemini/Grok native voice, OpenRouter/Ollama STT–LLM–TTS; conditional key validation before paid room creation; short-lived room tokens and cleanup; Ollama endpoint is not bundled inference; readiness and mocked adapters are not live-provider proof (§5.11) |
| OpenPencil | `openpencil` | public Caddy gateway, private digest-pinned v0.8.4 prerelease Rust web host, 1024 MB volume at `/data` | `admin` + generated password; one shared `/data/workspace.op`; File → Save persists, Save As exports, no autosave; exact origins and private control endpoints; browser-local AI keys, shared server credential snapshots disabled; 80 tests and real save/redeploy checks, no live AI-provider validation (§5.12) |
| Laminar | `laminar` | frontend + app-server (`ghcr.io/lmnr-ai/*:v0.2.4`), clickhouse-server 26.5, quickwit v0.8.2, postgres 16 — 5 services, each from its own `rootDirectory` | **Two required fields, both GitHub OAuth.** `ENVIRONMENT=LITE` cut seven services to four; the Rust app-server binds IPv4 only so the wrapper bridges it onto the IPv6 private network with `socat`; Next.js needed `HOSTNAME=::` exported in the entrypoint because Railway injects that variable at run time; the self-hosted login accepts any email with no password until OAuth is set, so the entrypoint refuses to boot without it; `healthcheckPath` rejected `/sign-in` and `/robots.txt` and the root 307s, leaving `/api/auth/ok` (§5.15) |
| HertzBeat | `apache-hertzbeat` | hertzbeat (`apache/hertzbeat:1.8.0`), postgres 15, victoria-metrics — 3 services, each from its own `rootDirectory` | **Zero composer fields; `deploy -t` never prompted.** Environment-only overrides moved it off embedded H2 + DuckDB onto Postgres + VictoriaMetrics, with defaults read from the pinned image because the repo compose is EclipseLink-mismatched and pins an unpublished tag; Flyway's `{vendor}` resolved itself; `/actuator/health` was 401 until the entrypoint opened it in `sureness.yml`; the same entrypoint rewrites the file-based `admin` credential from `${{secret(24)}}`; bad logins return HTTP 200 with an error body; monitor params use `paramValue` (§5.14) |
| Fabric | `fabric` | one Go service built from pinned upstream source (no upstream image), volume at `/home/appuser/.config/fabric` | **Built from source without forking.** A 190 MB fork pinned 27 releases behind became a 228 KB repo whose Dockerfile clones a pinned tag, asserts that tag's commit so a moved tag fails the build, and applies an 84-line patch — `/health` plus its auth exemption, `text/readystream` → `text/event-stream` (upstream's own swagger already said so), and CORS moved off hardcoded `localhost:5173` to `FABRIC_ALLOWED_ORIGIN`; the bump to v1.4.478 brought two upstream security fixes and forced Go 1.26; Fabric reads providers from `~/.config/fabric/.env` not the process env, so keys land in plaintext on the volume; `railway.json` supplies the healthcheck the reference project lacked, and that reference project later vanished (§5.16) |
| Persistent mise Workspace | `persistent-mise-workspace` | one private Debian 13 Linux/amd64 SSH workspace with mise 2026.9.3, Node 24.21.0, Python 3.13.15, uv 0.12.11 and Tini; `/root` volume 5000 MB | No public listener or app keys; offline first-boot seed at identical install prefix; preserve owner files/tools and never run volume content at boot; CI trust guard; native IaC variable preservation; 2 vCPU/2 GiB, sleep off, daily backups verified in fresh copy; 27 tests plus real redeploy and deleted-runtime backup recovery (§5.13) |
| LongMemory | `longmemory` | longmemory (Node server, `/v1/*` + `/mcp`, SQLite on a `/data` volume) and dashboard (Next.js) — 2 services, each from its own `rootDirectory` | **Zero composer fields; `deploy -t` prompted for nothing.** Pinned by commit SHA because the newest tag is 155 commits behind a `main` that reports a lower version, and the GHCR image is not public; `LONGMEMORY_HOST=::` gave Node a dual-stack socket with no socat needed; the dashboard entrypoint exports `HOSTNAME=::` because Railway injects it at run time; the server ships an EMPTY api key so the entrypoint refuses to boot without one; db in a `/data/db` subdirectory with a root chown then `gosu` drop; literal defaults (`PORT=8080`, `""`) were nulled into required fields by generation so constants were baked into the images instead; without an embedding key recall is lexical and `strict` mode can hide the matching memory (§5.17) |
| OpenKnowledge | `openknowledge` | ok (`@inkeep/open-knowledge@0.71.13` on `node:24-slim`, editor + `/mcp`, git repo on a 1 GB `/data` volume), oauth2-proxy v7.13.0 and caddy 2.10 — 3 services, each from its own `rootDirectory`, **only caddy public** | **Three required fields, all Google OAuth.** The server has no login of its own, so Caddy fronts everything and splits by path: `/mcp*` on a bearer token straight to the app, the rest through oauth2-proxy to Google — two paths because agents cannot do an interactive login and browsers cannot attach a bearer to the `/collab` WebSocket; oauth2-proxy's default `[::]:4180` made Railway start and stop the container with no error until every service was moved to `:8080`; `OK_EXTERNAL_URL` doubles as a Host allowlist so the app 403s any direct probe and must be reached over `railway ssh`; the cookie secret needs `${{secret(32, "abcdef0123456789")}}` because oauth2-proxy validates byte length; `git` is a hard boot requirement and `ok init` must be fed `< /dev/null`; MCP over streamable HTTP is session-based, so a probe that skips the `Mcp-Session-Id` handshake reports zero tools (§5.18) |
| tlbx | `tlbx` | tlbx (`mt` release binary v10.16.2 + mise on `node:22-bookworm-slim`, shells and coding agents, 5 GB `/data` volume) and caddy 2.11 — 2 services, each from its own `rootDirectory`, **only caddy public** | **Zero composer fields.** The app serves HTTPS only and has no HTTP mode, so Caddy fronts it over TLS with verification skipped and answers the health probe itself; the app service gets no healthcheck at all. Its same-origin check compares the browser `Origin` to `request.Host` on scheme, host and port, and Caddy's TLS transport rewrites `Host`, so every WebSocket 403'd while the page loaded — fixed with `header_up Host {http.request.hostport}`. `HOME`, the npm prefix and mise's data live on the volume so installs survive redeploys; `${{secret(48)}}` generates the password. Sessions end on redeploy, app preview cannot work (`PORT+1` origin), and there is no Docker — all three stated in the listing (§5.19) |
| codeg | `codeg` | codeg (`xintaofei/codeg:0.30.7`, Rust server + Next.js UI, SQLite on a 5 GB `/data` volume) — **1 service, no gateway** | **Zero composer fields.** Upstream publishes multi-arch images, so the template is a thin `FROM` pin; the app speaks plain HTTP on a configurable port, so Railway's edge reaches it directly. `CODEG_TOKEN=${{secret(32, hex)}}`; with no token the app generates and logs one rather than serving open. `VOLUME` inherited from the base image built fine, and the app writes its database straight into a root-owned mount root beside `lost+found` with no entrypoint. Health is `/` (200 unauthenticated) — every unknown `/api/*` path answers a misleading `501`, and `/api/health` is POST-only. Agent CLIs and in-place updates do not survive a redeploy; both stated in the listing (§5.20) |
| Orca | `orca` | orca (`orca-ide` 1.4.203 `.deb` on `debian:13-slim`, an Electron agent IDE run headless, home on a 5 GB `/data` volume) — **1 service, no gateway** | **Zero composer fields and no password**: Orca mints a pairing identity on first boot and logs the pairing URL. Four things are needed to run Electron headless here — xvfb, `xauth` (a *recommends* that `--no-install-recommends` drops), the undeclared `libasound2`, and `ELECTRON_DISABLE_SANDBOX=1` with a non-root user, because Railway's seccomp denies `CLONE_NEWUSER`. `gosu` reset `HOME` from `/etc/passwd`, so the volume mounted but went unused and every redeploy rotated the pairing identity; fixed with `useradd -d /data/home -M`. The advertised address must be `wss://$RAILWAY_PUBLIC_DOMAIN`, with no port. Upstream scopes the feature to private networks and marks it beta — stated in the listing (§5.21) |
| DSH + LongMemory | `dsh-longmemory` | dsh (`@deepseek-ai/dsh` 0.1.5-rc.1 on `node:22`, Caddy 2.11 binary in the same container, mise, 5 GB `/data`) and longmemory (built from the §5.17 template repo, 1 GB `/data`) — 2 services, **only dsh public** | **Zero required fields**; `DEEPSEEK_API_KEY` optional because the UI takes it. The app binds loopback only and refuses 0.0.0.0, so Caddy runs beside it; its `/api` fence needs `Host` passed through and the public domain registered with `--trusted-host`, else every WebSocket is 403. Auth is the app's own (launch token → 30-day cookie, secret on the volume, so redeploys keep sessions). LongMemory joins as a streamable-http MCP row applied by `--patch`, with the reconnect budget raised because the default gives up before a sibling's source build finishes. Three older marketplace templates for the same app pin a pre-auth rc and two use the full trademark in their names (§5.22) |
| Mirage Daemon | `mirage-daemon` | mirage (`mirage-ai` 0.0.6 on `python:3.12-slim` with storage/data backends + Monty + quickjs-ng 0.16.2 wasm, socat dual-stack relay, `/data` volume) — **1 service** | **Zero required fields**; `MIRAGE_AUTH_TOKEN=${{secret(48)}}`, entrypoint refuses empty/short tokens. Railway's health probe sends `Host: healthcheck.railway.app` and the daemon fences Host before auth → first deploy failed until it was trusted; uvicorn is single-family under asyncio so socat fronts a loopback bind; the daemon SIGTERMs itself 30 s after its last workspace (0 = now) so the grace is ten years plus restart `ALWAYS`; live workspaces do not reload after a restart but commits do (recreate + `checkout`); script runtimes need `mode: exec` and JS needs the pinned `qjs-wasi.wasm` (§5.23) |
| Yao Agents | `yao-agents` | yao (`yaoapp/yao:1.0.0-rc22` upstream multi-arch image + su-exec/socat/tini, app + SQLite in `/data/yao` on a 5 GB volume) — **1 service** | **Zero required fields**; `YAO_ROOT_PASSWORD=${{secret(24)}}`. The bundled app creates root with a hard-coded `Yao123++`, so the entrypoint runs `yao init`, re-hashes root from the secret via `models.__yao.user.UpdateWhere` on every boot, and refuses to start without one (proven at the bcrypt layer; the login itself is captcha-gated). The app's `.env` is loaded with `godotenv.Overload` and beats Railway variables, so production/loopback/port are written into it; `YAO_HOST=::` crashes with `Host not found` so socat fronts the IPv4 engine; app in a subdirectory because `yao init` refuses the `lost+found` mount root. Modified Apache-2.0 with a 50-employee/USD 1M commercial clause, stated verbatim (§5.24) |
| HolyClaude Workstation | `holyclaude-workstation` | holyclaude (`coderluii/holyclaude:1.6.1` upstream multi-arch image + one entrypoint, ~13 GB, state on a 5 GB volume at `/home/claude/.claude`) — **1 service** | `CLOUDCLI_PASSWORD=${{secret(24)}}` plus a prefilled `PORT=3001` (nothing to type). Volume mounts at the app's own state directory because upstream refuses a symlinked durable root, and `/workspace` symlinks into it; the entrypoint registers the single CloudCLI account against a loopback-only instance before the public server starts (a background registration raced the healthcheck and lost), since upstream's first run is browser registration; `cd /` before replacing the inherited `WORKDIR`. Replaces a stale third-party `holyclaude` template that had no volume. AGPL-3.0 web UI over MIT glue (§5.25) |
| Octop | `octop` | octop (`python:3.12-slim` + the released PyPI wheel installed with uv, socat relay, 996 MB, state on a 5 GB volume at `/data`) — **1 service** | `OCTOP_DEFAULT_PASSWORD=${{secret(24)}}` plus a prefilled `PORT=8080`. The published wheel already carries the built React dashboard, so upstream's npm stage is skipped; **pip cannot install it at all** (`ResolutionTooDeep`) so uv is mandatory, and `evdev` needs a compiler at build time. uvicorn cannot bind dual-stack → socat. Upstream already randomises the admin password into `credential.txt`; the template supplies a secret instead so it shows in the Railway UI, and deliberately does **not** re-apply it on later boots. `railway.json`'s healthcheck read back null — set on the service instance instead (§5.26) |
| Coddy | `coddy` | coddy (`alpine:3.22` + the static Go binary copied out of upstream's published `scratch` image, **57 MB**, state on a 5 GB volume at `/data`) — **1 service** | `CODDY_HTTP_PASSWORD=${{secret(24)}}` and `CODDY_HTTP_TOKEN=${{secret(32)}}` plus a prefilled `PORT=8080`. A scratch upstream has no shell, so the binary is copied out rather than the image inherited. Authentication is off by default upstream, so both gates are made mandatory and fail closed. **First Go service that needed no socat** — `-H ::` is genuinely dual-stack. `ALWAYS` restart because a clean SIGTERM exits 0. Telegram gateway absent: the published image is built without that tag (§5.27) |

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

- [ ] Images/artifacts pinned and verified for every advertised architecture; do not claim arm64 support for an amd64-only template.
- [ ] Local smoke: health, dual-stack listen, user journey through the proxy, websocket 101, second start idempotent, uid/ownership.
- [ ] Cold start on an empty root-owned volume works (init, chown); killing a sidecar takes the container down.
- [ ] `git rev-parse --show-toplevel` prints the template repo before every add/push; nothing but the template files is tracked.
- [ ] Reference project: services ready and verified through the intended entry point (public HTTP or managed SSH); inspect logs and client IP handling where applicable.
- [ ] Review report read and triaged; fixes committed; redeploy verified.
- [ ] README/OVERVIEW/CHANGELOG consistent with each other and with upstream's CLI.
- [ ] Template definition read back: no empty non-optional values, no whitespace, secrets on the right keys, name final.
- [ ] Explicit volume size, deployment limits, sleeping/replicas, watch patterns and backup schedules checked in the fresh copy; native IaC preview contains no unexpected variable deletion.
- [ ] For persistent apps/workspaces: actual content and executable paths survive replacement; snapshot restore tested on disposable data, not merely snapshot creation.
- [ ] Published with category, ≤ 75-char description, overview readme, image URL — via the `railway templates` CLI, and confirmed by reading the saved overview back.
- [ ] One-click deploy into a scratch project verified, then deleted.
- [ ] Memory notes, plan file, cognee dataset updated; repo README button points at the real code.
