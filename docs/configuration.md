# Configuration

Everything here can also be set on the **Settings** page. Environment variables set the defaults.

| Setting | What it is |
| --- | --- |
| `ARGOS_ENGINE_URL` | The engine in the same container. Docker sets it to `http://127.0.0.1:8080`; outside Docker, set it if you run the engine yourself. |
| `MODELS_DIR` | The folder with your model files. Use the same one the engine scans. |
| `ARGOS_COOKBOOK_TOKEN` | Optional. When set, the engine must send it as `X-Odysseus-Internal-Token` to read your presets. |
| `HF_TOKEN` | Hugging Face token, only for gated models. |
| `ARGOS_DB` | Where Argos keeps its data. Default `./data.db`. |
| `ARGOS_SCHEDULE_POOL` | `1` starts with the pool router on (Schedule page). setup.sh sets it. |
| `ARGOS_SCHEDULE_CARDS` | GiB per card for the Schedule page to start from, e.g. `32,32`. setup.sh sets it. |
| `ARGOS_SCHEDULE_FILE` | The file in the engine's config folder that carries the Schedule plan. Set for you in Docker; leave it alone. |
| `ARGOS_RUNS_DIR` | One log file per launch. |
| `ARGOS_SERVERS_FILE` | A `servers.yaml` that adds servers when Argos starts. See [Servers](servers.md). |
| `ARGOS_SERVERS` | The same list as JSON in the environment. |
| `ARGOS_SSH_DIR` | Where each server's SSH key is kept. Default: an `ssh` folder next to `ARGOS_DB`. |
| `ARGOS_TELEMETRY` | `0` turns the background GPU collector off. |
| `HOST` / `PORT` | Where the web page listens. Default `0.0.0.0:7861`. |
