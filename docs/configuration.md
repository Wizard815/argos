# Configuration

Everything here can also be set on the **Settings** page. Environment variables set the defaults.

| Setting | What it is |
| --- | --- |
| `ARGOS_ENGINE_URL` | The engine in the same container. Docker sets it to `http://127.0.0.1:8080`; outside Docker, set it if you run the engine yourself. |
| `MODELS_DIR` | The folder with your model files. Use the same one the engine scans. |
| `ARGOS_COOKBOOK_TOKEN` | Optional. When set, the engine must send it as `X-Odysseus-Internal-Token` to read your presets. |
| `HF_TOKEN` | Hugging Face token, only for gated models. |
| `ARGOS_DB` | Where Argos keeps its data. Default `./data.db`. |
| `ARGOS_SQLITE_JOURNAL` | Force the database journal: `WAL`, `TRUNCATE`, `DELETE` or `PERSIST`. By default `WAL` on a local disk and `TRUNCATE` on a FUSE, NFS or SMB share (Unraid's `/mnt/user`). Set it when a bind mount hides the real filesystem. |
| `ARGOS_SQLITE_LOCKING` | With a non-`WAL` journal the builder holds the database file lock for good, so queries skip the per-query file-system round trips that make a FUSE share slow. Only the builder opens that file. Set `normal` to turn it off, for example to read `builder.db` with the `sqlite3` tool while the builder runs. |
| `ARGOS_SCHEDULE_POOL` | `1` starts with the pool router on (Schedule page). setup.sh sets it. |
| `ARGOS_SCHEDULE_CARDS` | GiB per card for the Schedule page to start from, e.g. `32,32`. setup.sh sets it. |
| `ARGOS_SCHEDULE_FILE` | The file in the engine's config folder that carries the Schedule plan. Set for you in Docker; leave it alone. |
| `ARGOS_RUNS_DIR` | One log file per launch. |
| `ARGOS_SERVERS_FILE` | A `servers.yaml` that adds servers when Argos starts. See [Servers](servers.md). |
| `ARGOS_SERVERS` | The same list as JSON in the environment. |
| `ARGOS_SETUP_MODE` / `ARGOS_SETUP_BACKENDS` | Where setup.sh put llama.cpp (`1` inside Argos, `2` next to it, `3` over SSH) and for which GPUs. Argos adds it to Servers on start. See [Servers](servers.md#added-by-setupsh). |
| `ROCM_IMAGE` / `CUDA_IMAGE`, `ROCM_BIN` / `CUDA_BIN` | Modes 1 and 2: the llama.cpp image per backend and the `llama-server` path in it. Mode 2's `docker-llama` starts a container from them per model, with `HOST_MODELS_DIR` (the models folder on the host) at `/app/models`. |
| `ARGOS_SSH_DIR` | Where each server's SSH key is kept. Default: an `ssh` folder next to `ARGOS_DB`. |
| `ARGOS_RAPL_DIR` | Where the CPU's RAPL energy counters are, for the header's CPU watts. Default: `/sys/class/powercap`, else `/host/powercap`, where setup.sh mounts `/sys/devices/virtual/powercap` (Docker 25+ hides it from containers). No counters (a VM): the CPU watts show `—`. |
| `ARGOS_TELEMETRY` | `0` turns the background GPU collector off. |
| `ARGOS_PORT` | The one host port for Argos: web page, OpenAI API and engine endpoints. Default `8080`. |
| `HOST` / `PORT` | Where the builder listens inside the container. Docker sets `8080`; running bare it defaults to `0.0.0.0:7861`. |
