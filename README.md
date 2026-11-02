# Argos

**Argos replaces llama-swap.** It is one OpenAI-compatible endpoint (`/v1`) for all your local models: ask for a model by name and Argos starts its `llama-server`, shares the GPUs with whatever else is loaded, and unloads what is not needed. Argos started as a fork of llama-swap and grew a web app around it:

- a **launch command builder**, to tune each model's `llama-server` command and keep it as a profile;
- a **GPU scheduler**, with live monitoring and benchmarks;
- an **assistant** that knows your setup.

It still works with **Odysseus**: the launch commands Odysseus's Cookbook builds become Argos profiles you can switch to, and Odysseus's tools open inside Argos. Bring your own llama.cpp: Argos runs it, it does not replace it.

![Schedule](docs/shots/schedule.png)

## What you get

| | |
| --- | --- |
| **Launch command builder** | Build a model's `llama-server` command flag by flag, with each flag explained, test it, and save it as a profile. Keep as many profiles per model as you like. |
| **Profiles** | Each profile is its own model in `/v1/models`; a model's own name can run the profile you pick as its default. |
| **Presets** | Sets of environment variables and flags you apply to any profile, then adjust. |
| **Scheduler** | Models share the GPUs. It places each on the cards with room and unloads only what it must, never a model that is working. |
| **Request control** | Recently used models stay loaded between prompts. Hold, send on or cancel any waiting request. |
| **CPU models** | CPU-only models, or ones that fall back to the CPU when the cards are full, with their own RAM and cores shown. |
| **Live and Usage** | Each model's prompt progress and answer speed as it works; GPU, CPU and RAM over the last hour. |
| **Playground** | Launch, speed-test, and run llama-bench, also on a model that is already running. |
| **Local runners** | whisper.cpp, Kokoro and stable-diffusion.cpp on the same port, with their models a click away. |
| **Assistant** | Answers from Argos's own data; harder questions go to a bigger model on a GPU or the CPU. |
| **API and MCP** | What the GPUs and requests are doing, for a dashboard or another model. |
| **Odysseus** | Argos runs the commands Odysseus's Cookbook builds, as profiles of the same model. Notes, Brain, Calendar, Library, Cookbook, Tasks, Gallery and Tools open inside Argos. |

![Inventory](docs/shots/inventory.png)

_Screenshots use demo data._

## Run it

Argos runs in Docker. The setup script asks a few questions and writes `.env`, `docker-compose.override.yaml` and `config.yaml` (existing ones are backed up):

```sh
git clone https://github.com/Wizard815/Argos
cd Argos
bash setup/setup.sh
docker compose up -d --build
```

Then open **http://localhost:8080**. That one port serves the Argos page, the OpenAI API your apps use (`/v1`) and the engine's endpoints.

### The three modes

The script asks where llama.cpp runs:

| Mode | llama.cpp runs… | Pick it when |
| --- | --- | --- |
| **1** | **inside** the Argos container: Argos is built on a llama.cpp image. | You want one container. |
| **2** | **next to** Argos: a container per model, started through the Docker socket. | Each model should get its own container. The socket gives Argos control of the host. |
| **3** | in llama.cpp container(s) **you already run**: Argos starts models there over SSH (`remote-llama`). | Your llama.cpp already runs in its own container. |

Modes 1 and 2 ask the same thing: an image per backend. Press Enter for the official `ghcr.io/ggml-org/llama.cpp:server-rocm` or `:server-cuda`, or type your own. Mode 3 asks for each container's host, SSH port and `llama-server` path. Its container needs sshd with Argos's key and the **same models folder at `/app/models`**. The script can make the key and prints a checklist.

Whichever mode you pick, Argos adds it to **Servers** when it starts, so Profiles can launch right away. In Mode 3 that happens once the key is mounted.

It also asks for the **backend** (ROCm, CUDA or both), the **scheduler** (the GPU-sharing pool, or fixed groups), your **models folder** and the **ports**. Run it again later and it offers your last answers.

## More

- **Odysseus:** open **Odysseus** in the sidebar, paste its URL and an API token, **Save & connect**.
- **Scheduler rules:** [VRAM pool guide](docs/kb/guides/routing/vram-pool.md).
- **Settings:** [docs/configuration.md](docs/configuration.md). **Servers over SSH:** [docs/servers.md](docs/servers.md). **Without Docker:** [docs/development.md](docs/development.md).
- **Postgres** (optional, for many writers at once): set `POSTGRES_PASSWORD` in `.env`, run `docker compose exec argos argos-migrate`, then set `ARGOS_DATABASE_URL`. Step by step in [docs/postgres.md](docs/postgres.md).

**Security:** there is no login, and the Playground runs whatever command you give it. Only expose Argos to people you trust.

Argos started as a fork of [llama-swap](https://github.com/mostlygeek/llama-swap) by Benson Wong (MIT, see [LICENSE.md](LICENSE.md)).
