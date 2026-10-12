# Configuration

Everything here can also be set on the **Settings** page. Environment variables set the defaults.

| Setting | What it is |
| --- | --- |
| `LLAMA_SWAP_URL` | Your Argos engine, for example `http://llama-swap:8080`. |
| `MODELS_DIR` | The folder with your model files. Use the same one the engine scans. |
| `ODYSSEUS_COOKBOOK` | Odysseus' `cookbook_state.json`, to import your Cookbook presets. |
| `HF_TOKEN` | Hugging Face token, only for gated models. |
| `ARGOS_DB` | Where Argos keeps its data. Default `./data.db`. |
| `ARGOS_EXPORT_FILE` | Where the scheduler plan is written for the engine. Set for you in Docker. |
| `ARGOS_RUNS_DIR` | One log file per launch. |
| `ARGOS_TELEMETRY` | `0` turns the background GPU collector off. |
| `HOST` / `PORT` | Where the web page listens. Default `0.0.0.0:7861`. |
