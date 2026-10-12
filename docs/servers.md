# Servers

The **Servers** page lists the machines your models run on. **Local** is the Argos container itself. Add the others over SSH, the same way you do in Odysseus.

This page only connects to servers and reports what they have. It does not launch anything on them yet.

## Add a server

1. Click **Add server**. Enter a name, `user@host` and the SSH port. Pick a colour if you like.
2. Optional: an environment that is activated first on that machine (**venv** with its path, or **conda** with the env name), the path to `llama-server` if it is not on the `PATH`, and the **Model Directory** folders. The ◉ marks the folder downloads go to.
3. **Save**, then click **Key** and **Generate key**. Argos makes a key pair for this server and shows one command. Run it once in a terminal that can already log in to the server. It adds Argos' public key to that server's `authorized_keys`.
4. Click **Check**.

Check connects, runs a short script that only reads, and shows:

- the time it took to connect
- where `llama-server` is, and whether vLLM, SGLang and Docker are installed
- the GPU stack (`rocm` or `cuda`) and card names
- whether the server **sees the same model files Argos sees**: it lists the `.gguf` files in the server's Model Directory folders and compares them with Argos' own models folder by file name and size. It tells you which files are missing there, which differ in size, and which folders do not exist. If the llama-server container does not mount the same models folder, this is where you find out.

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
