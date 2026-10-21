# To do

Open items from building and testing Argos on Windows (RTX 3060) and Unraid
(2x MI50). Newest first within each section.

## Test on the Unraid box (MI50s)

- [ ] Rebuild the image and redeploy: the engine changes (`cwd`, sticky
      ports, sysfs card numbering, placement across reloads) only arrive
      with a new image, and a restart clears stuck threads.
- [ ] Base the Argos image on a ROCm image instead of the llama.cpp server
      image (`BASE_IMAGE` / `BASE_TAG` in `.env`); Mode 3 does not need
      llama.cpp inside Argos.
- [ ] Give the Argos container the GPUs (`/dev/kfd`, `/dev/dri`, video and
      render groups) so local runners can use them.
- [ ] whisper on ROCm: build the gfx906 fork once in its SDK container,
      copy `build/bin/whisper-server` into `/data/runners/whisper`, and set
      Servers -> Local -> whisper.cpp -> "Use a whisper-server you already
      built". Then transcribe through `/v1/audio/transcriptions`.
- [ ] Custom ROCm: mount it into the container and set Settings -> GPU tools
      -> ROCm location; check the tools line there.
- [ ] Kokoro on ROCm with the ML-gfx906 PyTorch build from the picker
      (Python 3.12 wheels); confirm Kokoro's dependencies accept that torch.
- [ ] From-source runner install inside the container: the automatic
      `apt-get` of git / cmake / build-essential, and the clear error when
      no ROCm compiler is found.
- [ ] ffmpeg add-on on Linux (`apt-get install ffmpeg`), and conversion of
      an m4a upload through the builder's port.
- [ ] Schedule: confirm the cards show as GPU 0 and 1, that models land on
      the card the pool chose, and that no model is stopped when a model is
      added to the folder or a profile is saved.
- [ ] Profiles whose `--model` file is missing (shown as "file missing"):
      fix their paths or delete them.
- [ ] The engine knows fewer profile variants than the builder has
      profiles: check `odysseus.baseURL` points at the builder, and look at
      which profiles are still missing in Schedule -> Plan.
- [ ] GPU processes card: needs the builder to see the host's processes
      (`pid: host`) to show servers other containers started.

## Build next

- [ ] stable-diffusion.cpp: install and run for real (Windows CUDA 12 build
      and a small single-file image model), generate through
      `/v1/images/generations`.
- [ ] Image models made of several files (diffusion model + VAE + text
      encoder): group the parts automatically instead of typing them into
      the model's extra arguments.
- [ ] ComfyUI as a runner (served under `/comfyui/`), using the gfx906
      PyTorch builds on the MI50s.
- [ ] "Served by" choice on the Models page, not only in Servers -> Local.
- [ ] The engine's own GPU readings should use the rocm-smi / nvidia-smi set
      in Settings -> GPU tools (today only the builder does).
- [ ] Kokoro is booked on a GPU card by the pool even on CPU (0.1 GiB);
      CPU runners should not be placed on a card.
- [ ] VRAM estimate: the buffer term is too high for small models, and
      Gemma-4 E2B/E4B come out about 2.7 GiB high (part of them stays on the
      CPU). Measured sizes already replace the estimate after a load.
- [ ] Custom runner Scan: try it on a real repo (e.g. the whisper fork,
      Chatterbox) and tune what it picks up.
- [ ] Text to speech with cloned voices for NPCs (Chatterbox) as a recipe.

## UI ideas, not started

- [ ] Merge Benchmarks into Inventory as a Runs tab.
- [ ] A one-line scheduler status at the top of Schedule -> Live.
- [ ] A landing "Watch" page: GPUs, loaded models, scheduler feed, problems.
- [ ] Assistant questions and answers in Logs, to debug bad answers.
- [ ] Demo mode with fake models and traffic, for trying Argos with no GPU.

## Known test failures

- [ ] About 20 builder tests fail on Windows only (POSIX commands and
      paths); they pass in the container. Make them pass on Windows or mark
      them POSIX-only.
- [ ] `demo/run_demo.py` is broken (UNIQUE constraint on servers.name).
