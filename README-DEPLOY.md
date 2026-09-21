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

## Once it's confirmed working

Commit and push from wherever's easiest (locally, or directly on the Unraid
box if you set up git there) — this folder currently has the changes
uncommitted, same as the local checkout on the Windows machine. Let me know
if you want the commit/push done from there instead.
