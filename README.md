# Fyrn Chat Test

Chat with the models on this PC through Fyrn, and smoke test them. No Ollama involved: the tool finds the Fyrn installed on this PC, runs under Fyrn's own venv, and loads models through Fyrn's own engine. It never modifies Fyrn's install, its settings, or its models folder.

## What it does

- **Chat**: pick a model from the list Fyrn sees, load it, and talk to it. Replies stream in, with live speed and free RAM.
- **Smoke test**: tick one or more models and press Run smoke test. Each model loads, answers two fixed questions, and unloads. The table shows PASS or FAIL per model, and every run saves a report (`report.md` and `report.json`) under `~/.fyrn-chat-test/reports/<timestamp>/`.

## Install

Fyrn must be installed first. Then download `fyrn-chat-test-v0.1.zip`, unzip it, and double-click **Install-Fyrn-Chat-Test**. Start it from the Fyrn Chat Test icon, or run: `fyrn-chat-test`

The installer creates no venv and downloads nothing: it runs under Fyrn's venv.

## How it finds Fyrn

1. `FYRN_INSTALL_DIR`, when set.
2. The `~/.local/bin/fyrn` launcher, which names its folder.
3. A search of the home folder (hidden folders pruned, 6 levels deep).

If no Fyrn install is found, it says so plainly and stops. If more than one is found, it says so and stops rather than guessing.

## Run the tests

From the project folder, with Fyrn's venv python:

```
python -m unittest discover -s tests
```

The suite needs the real Fyrn package files to test against: tests copy `fyrn/__init__.py`, `core.py`, `discover.py`, `runner.py`, and `debug_log.py` into a fake install in a temp folder. The source tree is found in this order: `FYRN_INSTALL_DIR` when it holds the files, the sandbox's canonical Fyrn tree when it exists, then the tool's own finder (a real Fyrn install on this PC). No real model is needed: the engine is faked with Fyrn's own `FakeRunner`.
