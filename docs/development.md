# Development

## Tests

Engine:

```sh
make test-dev
```

Builder:

```sh
cd builder
pip install -r requirements-dev.txt
pytest
```

The tests use stand-ins instead of real services: `tests/fake_llama_server.py` (prints llama-server's log format and serves `/health`, `/completion` and `/v1/chat/completions`), plus in-process fakes of the Hugging Face Hub and of the engine's API in `tests/fakes.py`. The full launch → benchmark → record path, downloads with resume, telemetry and the assistant all run without a GPU or network. Exported presets are checked against the same rules the engine's `ParseCommand` applies.

## Run without Docker

Engine:

```sh
make clean all
build/argos -config config.yaml -config-dir config.d -watch-config
```

Builder:

```sh
cd builder
pip install -r requirements.txt
python -m src.app
```

Open http://localhost:7861 and set the engine's URL in Settings. (In Docker everything is on port 8080.)
