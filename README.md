# Argos

**Run your local models like a service.** A profile builder, a GPU scheduler that shares your cards between models, live monitoring, benchmarks and an assistant that knows your setup. Bring your own llama.cpp. Integrates with Odysseus.

![Schedule](docs/shots/schedule.png)

## What you get

| | |
| --- | --- |
| **Builder** | Tune a `llama-server` command, save it as a profile, keep as many per model as you like. |
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
| **Odysseus** | Notes, Brain, Calendar, Library, Cookbook, Tasks, Gallery and Tools inside Argos. |

![Inventory](docs/shots/inventory.png)

_Screenshots use demo data._

## Run it

Argos runs in Docker and uses your llama.cpp; it does not replace it. The setup script asks a few questions and writes `.env`, `docker-compose.override.yaml` and `config.yaml` (existing ones are backed up):

```sh
git clone https://github.com/Wizard815/Argos
cd Argos
bash setup/setup.sh
docker compose up -d --build
```

Then open **http://localhost:8080**. That one port serves the Argos page, the OpenAI API your apps use (`/v1`) and the engine's endpoints.

### The three modes

The script asks where your llama.cpp lives:

| Mode | Argos… | Pick it when |
| --- | --- | --- |
| **1** | is built on your llama.cpp image, and runs `llama-server` itself. | You have one llama.cpp image and want one container. |
| **2** | starts a `docker run` per model. | Each model should get its own container. Needs the Docker socket, which gives Argos control of the host. |
| **3** | SSHes into your existing llama.cpp container(s), one per GPU type, and starts models there (`remote-llama`). | Your llama.cpp already runs in its own container. The default. |

In Mode 3 the llama.cpp container needs sshd with Argos's key, and the **same models folder at `/app/models`**. The script prints a checklist.

It also asks for the **backend** (ROCm, CUDA or both), the **scheduler** (the GPU-sharing pool, or fixed groups), your **models folder** and the **ports**. Run it again later and it offers your last answers.

## More

- **Odysseus:** open **Odysseus** in the sidebar, paste its URL and an API token, **Save & connect**.
- **Scheduler rules:** [VRAM pool guide](docs/kb/guides/routing/vram-pool.md).
- **Settings:** [docs/configuration.md](docs/configuration.md). **Servers over SSH:** [docs/servers.md](docs/servers.md). **Without Docker:** [docs/development.md](docs/development.md).
- **Postgres** (optional, for many writers at once): set `POSTGRES_PASSWORD` in `.env`, run `docker compose exec argos argos-migrate`, then set `ARGOS_DATABASE_URL`. Step by step in [docs/postgres.md](docs/postgres.md).

**Security:** there is no login, and the Playground runs whatever command you give it. Only expose Argos to people you trust.

Argos started as a fork of [llama-swap](https://github.com/mostlygeek/llama-swap) by Benson Wong (MIT, see [LICENSE.md](LICENSE.md)).
