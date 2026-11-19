# Changelog

## Fyrn Chat Test v0.1.1

- Fyrn Chat Test checks for updates now. On launch it quietly asks GitHub for the newest release: up to date, nothing happens. A newer version gets a popup asking whether to download it, never a silent download. The download is checked against the release's checksum before anything is installed, and after the download a popup asks whether to restart now to finish the update. Saying no finishes the update the next time the app starts. Help menu, Check for updates, checks on demand.
- Embedding models are handled honestly now. A model the header marks as a BERT-family embedding model, or one with "embed" in its name, shows "Embedding only" in the list. Load refuses it for a chat in plain words instead of loading it and failing at the first reply, and the smoke test marks it NOT TESTED with the reason instead of failing it. A reply that fails with the engine's "Memory is not initialized" error now says in plain words that the model couldn't start a reply.
- The Load column and the report now say "-" when no load was attempted (a run cancelled before the model started, or an embedding model the test skips), instead of "failed". The detail box and the report now show the reason a model was not tested.

## Fyrn Chat Test v0.1 (2026-11-17)

The first version: a chat window and a smoke test that run on the Fyrn installed on this PC, instead of Ollama.

- Finds the Fyrn install (launcher first, then a search of the home folder) and runs under Fyrn's own venv: no second venv, no duplicate downloads. If no Fyrn install is found, it says so plainly and stops. If more than one is found, it stops rather than guessing.
- Model list: the models Fyrn sees, its models folder plus Ollama's store, with size, facts from the file's own header, a fit badge, and where each model came from. Nothing is selected by default, ever.
- Chat: click a model, load it, talk to it. Replies stream in, with tokens per second and free RAM shown live. Enter sends, Shift and Enter starts a new line, Stop cuts a reply short, Unload frees the RAM. A load failure says which model and what went wrong, and resets to nothing loaded.
- Smoke test: tick models, press Run smoke test. Each model loads, answers two fixed questions ("Say hello in one sentence." and a small Python task), and unloads. The table shows PASS or FAIL per model, with time to first token and decode speed. Cancel stops after the current model, and the rest are marked NOT TESTED, not failed. Every run saves a report (report.md and report.json) under ~/.fyrn-chat-test/reports/<timestamp>/.
- Installer and desktop icon: double-click Install-Fyrn-Chat-Test, start from the icon. The launcher finds the folder again if it moved.
- Single instance: a second launch raises the running window without importing Qt.
- Debug report in the File menu, How to Use in the Help menu, and a note when Fyrn itself is running (two programs loading models share the same RAM).

Not verified with a real model yet: no loadable model exists in the test sandbox (the only model there is LFM3, which Fyrn's pinned engine cannot load). Real-model generation is verified on a PC that has models.
