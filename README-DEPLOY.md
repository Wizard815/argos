# Deploying this fork on Unraid

This folder (`/mnt/user/OnePiece/HomeLab/llamaswap` on the box) is a full
source checkout of llama-swap with the `modelscan` fork changes
(`internal/modelscan/`, `internal/config/modelscan.go`,
`internal/server/modelscan_api.go`, plus the wiring in `api.go`/`server.go`).

## `deploy/config.yaml` is now gitignored (one-time migration)

`deploy/config.yaml` used to be tracked in git as if it were sample
content, but on a live box it's actually your real, live config (real
paths, `globalTTL`, etc.) — every `git pull` collided with it. It's now
gitignored; `deploy/config.yaml.example` is the tracked template instead.

**If you already have a real `deploy/config.yaml` on this box from before
this change** (i.e. you're pulling this update onto an existing
deployment), do this once:

```bash
cd /mnt/user/OnePiece/HomeLab/llamaswap
cp deploy/config.yaml deploy/config.yaml.mine   # back up your real config
git checkout -- deploy/config.yaml               # drop local edits so pull doesn't conflict
git pull wizard <branch>                         # picks up the config.yaml -> config.yaml.example rename
cp deploy/config.yaml.example deploy/config.yaml # recreate it (now gitignored, untracked)
```

Then manually copy your real values from `deploy/config.yaml.mine` back
into the new `deploy/config.yaml` (dirs, any custom macros, `globalTTL`,
etc.) — the `.example` already has the `modelScan.groups` embedding-model
support built in, so you mainly just need to re-apply your own path/setting
tweaks on top of it, not redo the whole file. Once confirmed working,
`rm deploy/config.yaml.mine`.

**On a fresh deployment**, just `cp deploy/config.yaml.example
deploy/config.yaml` and edit the copy — `git pull` will never touch it
again.

## Confirmed from RaidLab (2026-09-13)

- Base image: `mx-llamacpp-mx-llamacpp-ssh:latest` (18.5GB, built 4 days ago)
  — already wired into the Containerfile's `BASE_IMAGE`/`BASE_TAG` defaults.
- GPU device passthrough: `/dev/kfd /dev/dri` (via `docker inspect`) — used
  in the run command below.
- Models mount: host `/mnt/user/OnePiece/HomeLab/LLMBin` → container
  `/app/models`, confirmed as the *only* models mount — `deploy/config.yaml`
  already updated to scan just that one path (recursive, so subfolders are
  covered).

## All placeholders filled in

`deploy/config.yaml`'s macro now uses your real confirmed flags (`-ngl 99 -c
262144 --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --fit off
--split-mode layer --jinja`), pulled from the live Cookbook-launched
`llama-server` process. One deliberate omission: `--spec-type draft-mtp
--spec-draft-n-max 3` were left out of the shared macro since they're
specific to the one MTP-variant model and would likely break the other ~24
models if applied to all of them — see the comment in `deploy/config.yaml`
for how to add them back for just that model if you want.

## Build

```bash
cd /mnt/user/OnePiece/HomeLab/llamaswap
docker build \
  -f docker/llama-swap-source.Containerfile \
  -t llama-swap-rescan:local \
  .
```

Watch this output closely — it's the first real compile check of the Go
changes (nothing here has been compiled yet, only reviewed by hand).
`make linux-amd64` runs inside the build; any Go error will show up as a
build failure with a file:line pointing at the problem.

## Run

```bash
docker run -d \
  --name llama-swap \
  -p 8642:8080 \
  --device=/dev/kfd --device=/dev/dri \
  --group-add=video --group-add=render \
  -v /mnt/user/OnePiece/HomeLab/llamaswap/deploy/config.yaml:/app/config.yaml:ro \
  -v /mnt/user/OnePiece/HomeLab/llamaswap/deploy/config.d:/app/config.d \
  -v /mnt/user/OnePiece/HomeLab/LLMBin:/app/models \
  --restart unless-stopped \
  llama-swap-rescan:local
```

Notes:
- `deploy/config.d` is mounted **read-write** (not `:ro`) — that's where
  `modelscan` writes `models.generated.yaml` on every scan, and it needs to
  persist across container restarts so a fresh scan isn't required every
  time.
- `deploy/config.yaml:ro` — edit it on the host and `docker restart
  llama-swap` to pick up changes (it's not baked into the image).
- Map `-p 8642:8080` to whatever port you want Studio's custom provider to
  point at (llama-swap listens on 8080 inside the container by default,
  same as the healthcheck).

## Verify

```bash
docker logs -f llama-swap
```

Look for the normal llama-swap startup lines, then confirm the scan
actually finds your models:

```bash
curl -X POST http://localhost:8642/api/models/rescan
```

Should return `{"ok":true,"models":<count>,"changed":true}` on first run.
Then:

```bash
curl http://localhost:8642/v1/models
```

should list them. Point Hermes Studio's `local-llamacpp` custom provider's
Base URL at `http://<unraid-ip>:8642/v1` and click "Refresh models" — that
now also triggers a scan in the background, per the earlier change to
`handleListModels`.

## The tuned config, and the two-step first application

`deploy/config.yaml.example` is now the tuned deploy config: `healthCheckTimeout`
300 (was 120), `logToStdout: both`, `-c 32768` and `-lm dio` in the shared macro
with `--fit off` removed, the per-group `modelScan` targets (12B quick search,
MTP, GPU 0, GPU 1, embeddings) and the `routing` groups (`gpu0`, `gpu1`,
`heavy`, `services`). It is tracked, so a pull updates the template;
`deploy/config.yaml` itself is gitignored and a pull never touches it, so the
live file still has to be re-copied or merged by hand.

Do **not** put any of these keys in a tracked file under `deploy/config.d/`
instead: the config-dir merge refuses a key two sources set differently
(`conflict at "healthCheckTimeout": ... sets a different value than a previous
source`) and the container will not start at all.

**The first application takes two steps**, because the routing groups name models
that only exist after the new `modelScan` groups have run, and a group member
with no model config aborts startup:

```
$ llama-swap -config deploy/config.yaml -config-dir deploy/config.d
ERROR failed to create server error="creating group router: no model config for \"gemma4-coding-q2_k\""
```

1. Comment out `routing:` and restart, then generate the fragments:

   ```bash
   curl -X POST http://192.168.20.5:8642/api/models/rescan
   ```

   One trigger does not always finish every group — check that
   `models.gpu1.generated.yaml` and `models.embeddings.generated.yaml` exist and
   re-run the rescan if they do not. Then confirm no model was claimed twice:

   ```bash
   cd /mnt/user/appdata/llamaswap/deploy/config.d
   grep -hE '^  [a-z0-9]' models.*.generated.yaml | sort | uniq -d
   ```

   (Nothing printed = clean. Duplicates fail the config load.)

2. Uncomment `routing:` and restart. Later restarts need only this step, since
   the generated fragments stay on disk.

The group members were checked against the GGUF files actually present in
`/app/models`: the five `gemma4-coding-*` models an earlier draft of the tuning
listed are gone from the model directory, so they are not group members here. A
member with no backing model blocks startup completely — re-add each one
together with its file.

## Pulling this update onto the box

```bash
cd /mnt/user/OnePiece/HomeLab/llamaswap
git stash push deploy/docker-compose.yml   # the local odysseus-mount edit is committed upstream now
git pull wizard main
git stash pop || git checkout -- deploy/docker-compose.yml
docker build -f docker/llama-swap-source.Containerfile -t llama-swap-rescan:local .
docker compose -f deploy/docker-compose.yml up -d
```

The rebuild is what picks up the Go change below; `-watch-config` reloads a
config change without it.

## What the Go change does

`internal/config/context_size.go` reads the allocated context window (`-c`,
`--ctx-size`, including `--ctx-size=N` and commands prefixed with `VAR=value`)
out of a model's launch command, and the model listing reports it as
`meta.n_ctx` / `context_length` / `context_window` when the model declares no
`capabilities.context`. Under an active profile the pinned variant's command is
used, since that is the command the request will really run.

Before it, a scanned model reported no window at all until it was loaded —
`/props` answers only for a running child — so a client substituted a guess of
its own. Hermes logged

```
WARNING agent.model_metadata: Could not determine context length for model
'ornith-1.0-35b-...-imatrix' (base_url=http://192.168.20.5:8642/v1)
— falling back to 256,000 tokens
```

and then sent prompts the child rejected with `Context size has been exceeded`.

## Odysseus state file permissions

```bash
ls -ld /mnt/user/appdata/odysseus/data   # must be traversable by the container uid
chmod 711 /mnt/user/appdata/odysseus/data
docker logs --since 5m llama-swap | grep odysseus
```

Empty output after a refresh cycle means the integration can read
`cookbook_state.json` again and the `ody` profile will show up in
`GET /api/profiles`.

## Once it's confirmed working

Commit and push from wherever's easiest (locally, or directly on the Unraid
box if you set up git there) — this folder currently has the changes
uncommitted, same as the local checkout on the Windows machine. Let me know
if you want the commit/push done from there instead.
