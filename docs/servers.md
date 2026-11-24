# Servers

The **Servers** page lists the machines your models run on. **Local** is the Argos container itself. Add the others over SSH, the same way you do in Odysseus.

Profiles launch on the **default** server (★). **Local** runs the `llama-server` path set on it, or `llama-server` on the `PATH` when it is blank.

## Added by setup.sh

`setup/setup.sh` records where llama.cpp runs (`ARGOS_SETUP_MODE` in `.env`), and each time Argos starts it adds one server per backend you chose: **llama.cpp ROCm** and/or **llama.cpp CUDA**. The first one becomes the default while the default is still the plain Local.

| Mode | Each server runs… |
| --- | --- |
| **1** llama.cpp inside Argos | that backend's `llama-server` in the image (the official images keep it at `/app/llama-server`). |
| **2** llama.cpp next to Argos | `/app/bin/docker-llama rocm` (or `cuda`): a container per model from the image you picked. |
| **3** your own llama.cpp containers | over SSH, with the mounted key as its key. Added only once the key is mounted at `/run/secrets/argos_ssh_key`; until then the Logs page says so. |

Run setup again with fewer backends and the server it no longer has is removed. **Local** stays as it is: this container with `llama-server` on the `PATH`.
Edit a setup server on this page and your edit wins: setup no longer changes or removes it.

## Add a server

1. Click **Add server**. Enter a name, `user@host` and the SSH port. Pick a colour if you like.
2. Optional: an environment that is activated first on that machine (**venv** with its path, or **conda** with the env name), the path to `llama-server` if it is not on the `PATH`, and the **Model Directory** folders. The ◉ marks the folder downloads go to.
3. **Save**, then click **Key** and **Generate key**. Argos makes a key pair for this server and shows one command. Run it once in a terminal that can already log in to the server. It adds Argos' public key to that server's `authorized_keys`.
4. Click **Check**.

Check connects, runs a short script that only reads, and shows:

- the time it took to connect
- where `llama-server` is, and whether vLLM, SGLang and Docker are installed
- the GPU stack (`rocm` or `cuda`) and card names
- which KV cache pairs (`-ctk` / `-ctv`) have a FlashAttention decode kernel in that `llama-server` build, read from its CUDA/ROCm library (**KV pairs** on the card; hover for the list)
- whether the server **sees the same model files Argos sees**: it lists the `.gguf` files in the server's Model Directory folders and compares them with Argos' own models folder by file name and size. It tells you which files are missing there, which differ in size, and which folders do not exist. If the llama-server container does not mount the same models folder, this is where you find out.

A pair missing from that list still runs, but every generated token first converts the whole KV cache to f16, which gets slow at long context (the server log says `no FlashAttention vector kernel compiled for K/V types`). Profiles launch on the default server, so the Builder shows **KV cache type not compiled** next to Launch, and Launch asks before starting such a command. The Builder's KV cache choices use the same list: **Show all** in the KV cache field and on the VRAM card's table lists the pairs it found. Run **Check** again after rebuilding llama.cpp. A build Argos cannot read (stripped, CPU-only, or a launcher script) lists no pairs and is never flagged.

If a Check fails, the reason is shown on the card, for example `Connection refused` or `Permission denied (publickey)`. Permission denied means the key command has not been run on that server yet.

Argos keeps each private key in its own `ssh` folder (`ARGOS_SSH_DIR`). It never leaves the container. **New key** in the key window replaces it.

## Set servers up before starting

Put the servers in a file and point Argos at it:

```yaml
# servers.yaml
servers:
  - name: llamacpp
    host: root@mx-llamacpp-ssh
    port: 22
    llama_server: /app/llama-server
    gpu: rocm
    model_dirs: [/app/models]
    default: true
  - name: cuda-box
    host: root@llama-cuda
    gpu: cuda
```

```yaml
# docker-compose.override.yaml
services:
  argos:
    environment:
      - ARGOS_SERVERS_FILE=/app/config.d/servers.yaml
```

`ARGOS_SERVERS` takes the same list as JSON.

- The file adds servers when Argos starts. You still make each key on the page.
- A server from the file follows the file on every start.
- Once you edit a server on the page, it belongs to you and the file no longer changes it.

## Import from Odysseus

**Import from Odysseus** copies the servers your Odysseus Cookbook already knows. It needs the Odysseus URL and token in **Settings**. Entries without an SSH host, such as Odysseus' own Local, are skipped.

Odysseus' keys stay in Odysseus, so each imported server needs its own key from Argos. Click **Make key** and run the command, then **Check**.

## Requirements

- The Argos image has `ssh` and `ssh-keygen`. The Dockerfile installs `openssh-client` if the base image lacks it.
- The server runs sshd and accepts key login.
- Argos and the server are on a Docker network that can reach each other, if the server is another container.

## Building a runner for ROCm (whisper.cpp, stable-diffusion.cpp)

A runner built from source needs a ROCm **SDK** inside the Argos container: its
compiler (`clang`, `hipcc` or `hipconfig`), not only the runtime libraries an
image that only runs llama.cpp has. Settings -> GPU tools -> **Find ROCm**
lists every ROCm folder Argos can see, whether it has a compiler, rocm-smi or
amd-smi, and which GPUs (gfx906, ...) its libraries were built for.
**Use this** sets it as the ROCm location.

One ROCm SDK on the host can serve every container. Keep it in one folder and
mount it read-only wherever it is needed:

```yaml
services:
  argos:
    volumes:
      - /mnt/user/appdata/rocm-gfx906:/opt/rocm-gfx906:ro
```

Then Settings -> GPU tools -> ROCm location: `/opt/rocm-gfx906`. AMD's packages
keep the compiler in `llvm/bin`, TheRock builds (the gfx906 ones) in
`lib/llvm/bin`; Argos uses either.

## Running whisper, Kokoro or sd-server outside Argos

Each runner card (Servers -> Local) has **Where it runs**. Anything outside
Argos survives an Argos rebuild, and Argos needs no ROCm SDK for it.

| Where it runs | Who starts it | VRAM | For |
| --- | --- | --- | --- |
| here, installed inside Argos | Argos, per model | freed on unload | the default |
| in another container, over SSH | Argos, per model, like llama-server | freed on unload | whisper, sd-server |
| a service that is already running | you (its own container) | always in use | whisper, Kokoro, sd-server |

**Over SSH** works exactly like your llama.cpp container: pick a server from
this page and the program's path there. The easiest place is the llama.cpp
container you already have: it has ROCm and sshd. Build whisper.cpp there once,
on a volume so a recreated container keeps it:

```bash
docker exec -it mx-llamacpp-ssh bash
cd /app/models/.runners   # any folder on a mounted volume
git clone --depth 1 https://github.com/Wizard815/whisper.cpp-rocm10-gfx906 whisper
cd whisper && cmake -B build -DGGML_HIP=1 -DAMDGPU_TARGETS=gfx906 -DCMAKE_BUILD_TYPE=Release
cmake --build build -j --target whisper-server
```

Then on the whisper card: **Where it runs** -> in another container, server
`llamacpp`, program `/app/models/.runners/whisper/build/bin/whisper-server`.
The ggml-*.bin files must be in the models folder both containers mount at the
same path (whisper's own `runners/whisper/models` folder is inside Argos and is
not used). sd-server works the same way.

**A service that is already running** is a URL Argos sends requests to, such
as a Kokoro-FastAPI container on the same Docker network:

```yaml
services:
  kokoro:
    image: ghcr.io/remsky/kokoro-fastapi-cpu:latest   # or your GPU build
    restart: unless-stopped
```

Kokoro card -> **Where it runs** -> a service that is already running,
`http://kokoro:8880`. It answers as `kokoro`, `tts-1` and `tts-1-hd`. A
whisper-server run this way must be started with
`--inference-path /v1/audio/transcriptions`; it answers as `whisper` and
`whisper-1`.

What goes wrong:

- **"server missing"** on the card: the server was removed from this page.
  Pick another.
- **Check fails with "no such file"**: the program path is wrong for that
  container, or the model file is not at the same path there. The server's
  **Check** compares the models folders.
- **Argos's VRAM plan is off with a running service**: Argos does not start or
  stop it, so it counts it as 0 GiB. Leave room for it.

## "Host key verification failed"

The server's SSH host key changed since Argos first saved it. A llama.cpp container that is recreated (an update, a
rebuild) makes new host keys unless they are kept on a volume, and ssh then refuses it, for Check and for model
launches alike. On the server's card, **Forget host key** removes the saved key from both (`known_hosts` in
`ARGOS_SSH_DIR`, and `/data/ssh_known_hosts` that `remote-llama` uses) and checks again, saving the new one.

To stop it coming back, keep the container's host keys on a volume, e.g. `- ./llama-ssh-keys:/etc/ssh/keys` with
sshd's `HostKey` lines pointing there (or mount the whole `/etc/ssh`). A key that changes when nothing was recreated
can mean something else is answering at that address: check before you forget it.
