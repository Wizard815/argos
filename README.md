# Argos

**Run your models with a profile builder, performance sheets, a demo playground and a scheduler that handles model requests. Bring your own back-end and this is a one-stop shop. Integrates with Odysseus.**

![Schedule](docs/shots/schedule.png)

## What you get

- **Builder.** Tune a `llama-server` command with simple controls. Save as many presets as you want.
- **Models.** See every model file you have. Download new ones from Hugging Face.
- **Scheduler.** All your models share the GPUs. Argos loads what fits and only unloads a model when it has to.
- **Playground.** Launch a model, run a speed test, and watch speed per request while it works.
- **Perf sheets and Inventory.** Every model and every launch is remembered, with prefill and decode speed for each setup.
- **Logs.** GPU use, speed per request and events, from 30 seconds back to a year.
- **Assistant.** A small model that knows your history and answers questions about it.
- **Servers.** Local plus any machine you can reach over SSH, each with its own key and a Check that shows what is installed there. See [docs/servers.md](docs/servers.md).
- **Odysseus.** Notes, Brain, Calendar, Library, Cookbook, Tasks, Gallery and Tools, right inside Argos.
- **Themes.** The same themes as Odysseus, or make your own.

![Inventory](docs/shots/inventory.png)

![Playground](docs/shots/playground.png)

![Odysseus](docs/shots/odysseus.png)

_Screenshots use demo data._

## Run it

Argos runs in Docker. It needs an image that already has your `llama-server` in it. That is your back-end, and Argos does not replace it.

```sh
git clone https://github.com/Wizard815/Argos
cd Argos
echo "BASE_IMAGE=your-llamacpp-image" > .env
docker compose up -d --build
```

Put your `.gguf` files in `./models` (or set `MODELS_DIR` in `.env`). Then open **http://localhost:8080**. That one port serves the Argos page, the OpenAI API your apps use (`/v1`), and the engine's endpoints. Change it with `ARGOS_PORT` in `.env`.

The compose file passes your AMD GPUs through (`/dev/kfd` and `/dev/dri`). On NVIDIA, change that part for your setup.

## Connect Odysseus

Open **Odysseus** in the sidebar, paste your Odysseus URL and an API token (Odysseus → Settings → API tokens), and press **Save & connect**.

More settings are in [docs/configuration.md](docs/configuration.md). Running without Docker is in [docs/development.md](docs/development.md).

**Security:** the Playground runs whatever command you give it, and there is no login. Only expose the ports to people you trust.

Argos started as a fork of [llama-swap](https://github.com/mostlygeek/llama-swap) by Benson Wong (MIT, see [LICENSE.md](LICENSE.md)). The engine is now part of Argos and is configured and documented here.
