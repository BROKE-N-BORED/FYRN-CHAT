"""The smoke test: load each chosen model, ask it two fixed
questions, and record what happened.

One ModelController (from the installed Fyrn) runs the whole
test, one model at a time: load, two prompts, unload, next
model. A model passes when it loads and both prompts get a
non-empty reply. Anything else fails, with the reason recorded.

A prompt that runs past PROMPT_TIMEOUT_S is stopped and fails
the model: a model that cannot answer "Say hello in one
sentence" in three minutes is not usable. Cancel stops the run
cleanly: the current model unloads, and the models not yet
started are recorded as not tested, not failed. An embedding
model is recorded as not tested, never failed: it cannot write
replies, so the smoke test does not apply to it, and it is
never loaded.

This module is Qt-free: the GUI runs run_smoke_test in a worker
thread, and tests call it directly. Progress goes out through
the on_progress callback, so the UI always shows what the test
is doing.
"""

import threading
import time

from fyrn.runner import GenerationCancelled

from .models import is_embedding_model

# The two canned prompts. Fixed, so every run is comparable and
# every report reads the same way: one trivial reply (does the
# model answer at all?) and one small task (does it follow an
# instruction and produce code?).
PROMPTS = (
    "Say hello in one sentence.",
    "Write a Python function that adds two numbers, then call "
    "it with 2 and 3.",
)

# A prompt that runs past this is stopped and fails the model.
PROMPT_TIMEOUT_S = 180

# How long the runner gets to notice a cancel before the test
# gives up waiting on it and aborts the run (a stuck reply would
# otherwise hang the test forever).
_CANCEL_GRACE_S = 10

# How much of each reply the report keeps: enough to see what
# the model said, not the whole answer.
EXCERPT_CHARS = 200


def _excerpt(text):
    """The first EXCERPT_CHARS of a reply, with newlines kept
    (a code reply reads better with its line breaks)."""
    return (text or "")[:EXCERPT_CHARS]


def _timeout_text(seconds):
    """A timeout in plain words: "3 minutes", "1 second"."""
    seconds = int(seconds)
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return "%d minute%s" % (minutes,
                                "" if minutes == 1 else "s")
    return "%d second%s" % (seconds,
                            "" if seconds == 1 else "s")


def _generate_with_timeout(runner, prompt, timeout_s, user_cancel,
                           on_token=None):
    """Run one prompt with a timeout and a user cancel.

    generate() runs in its own thread: the runner checks its
    cancel event between chunks, so the waiting thread can stop
    it for a timeout or a user cancel. Returns (text, stats,
    outcome) where outcome is "ok", "timeout", "cancelled", or
    "error" (with stats carrying the error message).
    """
    prompt_event = threading.Event()
    result = {}
    pieces = []

    def _collect(text):
        pieces.append(text)
        if on_token is not None:
            on_token(text)

    def _run():
        try:
            stats = runner.generate(
                prompt, on_token=_collect,
                cancel_event=prompt_event, history=[])
            result["stats"] = stats
        except GenerationCancelled:
            result["cancelled"] = True
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            result["error"] = str(exc)

    worker = threading.Thread(target=_run, daemon=True)
    started = time.monotonic()
    worker.start()
    while worker.is_alive():
        if user_cancel is not None and user_cancel.is_set():
            prompt_event.set()
            break
        if time.monotonic() - started > timeout_s:
            prompt_event.set()
            break
        time.sleep(0.05)
    worker.join(timeout=_CANCEL_GRACE_S)
    if worker.is_alive():
        # The reply would not stop even after the cancel: the
        # runner is stuck, and the rest of the run cannot be
        # trusted. Abort the whole run.
        return "".join(pieces), {"error": "The reply would not "
                                 "stop after it was cancelled."}, \
            "stuck"
    if "stats" in result:
        # The reply finished, even if the timeout or the cancel
        # fired at the same moment: a finished reply is a reply.
        return "".join(pieces), result["stats"], "ok"
    if "error" in result:
        return "".join(pieces), {"error": result["error"]}, "error"
    if result.get("cancelled"):
        if user_cancel is not None and user_cancel.is_set():
            return "".join(pieces), {}, "cancelled"
        return "".join(pieces), {}, "timeout"
    return "".join(pieces), {
        "error": "The reply ended with no result."}, "error"


def run_smoke_test(models, controller, on_progress=None,
                   user_cancel=None, prompt_timeout_s=None,
                   prompts=None, on_model_done=None):
    """Run the smoke test over a list of model dicts.

    controller is a fyrn.core.ModelController (one runner, one
    model at a time). on_progress(kind, index, name, detail)
    reports each step: "model_started", "prompt_started",
    "model_done". on_model_done(index, result) reports each
    model's result as it completes, so a UI can fill its table
    live instead of waiting for the whole run. user_cancel is
    a threading.Event: when set, the run stops after the
    current step and the remaining models are recorded as not
    tested. Returns a list of result dicts, one per model, in
    the order they were tested.
    """
    if prompt_timeout_s is None:
        prompt_timeout_s = PROMPT_TIMEOUT_S
    if prompts is None:
        prompts = PROMPTS
    if user_cancel is None:
        user_cancel = threading.Event()
    results = []

    def _record(index, result):
        results.append(result)
        if on_model_done is not None:
            on_model_done(index, result)

    for index, model in enumerate(models):
        name = model.get("display_name") or model["name"]
        path = model["path"]
        if user_cancel.is_set():
            _record(index, _not_tested(model))
            continue
        if is_embedding_model(model):
            # An embedding model cannot write replies, so the
            # smoke test does not apply: not tested, never
            # failed, and no load is attempted.
            _record(index, _embedding_skipped(model))
            if on_progress is not None:
                on_progress("model_started", index, name,
                            "Embedding only, skipped.")
                on_progress("model_done", index, name,
                            "not tested")
            continue
        if on_progress is not None:
            on_progress("model_started", index, name,
                        "Loading %s..." % name)
        result = {"name": name, "path": str(path),
                  "source": model.get("source", "folder"),
                  "size": model.get("size"),
                  "load_ok": False, "load_time_s": None,
                  "load_error": None, "prompts": [],
                  "verdict": "fail", "error": None}
        try:
            load = controller.load(
                path, display_name=name)
            result["load_ok"] = True
            result["load_time_s"] = load.get("load_time_s")
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            result["load_error"] = str(exc)
            result["error"] = str(exc)
            _record(index, result)
            if on_progress is not None:
                on_progress("model_done", index, name, "Failed to load")
            continue
        stopped = False
        for prompt_index, prompt in enumerate(prompts):
            if user_cancel.is_set():
                stopped = True
                break
            if on_progress is not None:
                on_progress("prompt_started", index, name,
                            "Prompt %d of %d..."
                            % (prompt_index + 1, len(prompts)))
            text, stats, outcome = _generate_with_timeout(
                controller.runner, prompt, prompt_timeout_s,
                user_cancel)
            entry = {"prompt": prompt,
                     "replied": bool(text.strip()),
                     "ttft_s": stats.get("ttft_s"),
                     "tokens_per_sec": stats.get("tokens_per_sec"),
                     "tokens": stats.get("tokens"),
                     "elapsed_s": stats.get("elapsed_s"),
                     "excerpt": _excerpt(text),
                     "error": None}
            if outcome == "cancelled":
                entry["error"] = "Stopped."
                result["prompts"].append(entry)
                stopped = True
                break
            if outcome == "timeout":
                entry["error"] = (
                    "No reply within %s."
                    % _timeout_text(prompt_timeout_s))
                result["prompts"].append(entry)
                result["error"] = entry["error"]
                # A model that cannot answer in time fails: the
                # remaining prompts are not tried.
                for rest in prompts[prompt_index + 1:]:
                    result["prompts"].append(
                        {"prompt": rest, "replied": False,
                         "ttft_s": None, "tokens_per_sec": None,
                         "tokens": None, "elapsed_s": None,
                         "excerpt": "",
                         "error": "Skipped: the earlier prompt "
                                  "timed out."})
                break
            if outcome == "stuck":
                entry["error"] = stats.get("error", "Reply stuck.")
                result["prompts"].append(entry)
                result["error"] = entry["error"]
                break
            if outcome == "error":
                entry["error"] = stats.get("error", "Reply failed.")
                result["prompts"].append(entry)
                result["error"] = entry["error"]
                break
            result["prompts"].append(entry)
        # The model unloads whatever happened: the next model
        # needs the RAM, and a cancelled run leaves nothing
        # loaded behind it.
        try:
            controller.unload()
        except Exception:  # noqa: BLE001 - unload must not fail a run
            pass
        if stopped:
            result["verdict"] = "not tested"
            result["error"] = result["error"] or "Stopped."
        elif (result["load_ok"] and result["prompts"]
                and all(p["replied"] for p in result["prompts"])):
            result["verdict"] = "pass"
        _record(index, result)
        if on_progress is not None:
            on_progress("model_done", index, name,
                        result["verdict"])
        if stopped:
            # The rest of the list is not tested, not failed.
            for rest_index, rest_model in enumerate(
                    models[index + 1:], start=index + 1):
                _record(rest_index, _not_tested(rest_model))
            break
    return results


def _not_tested(model):
    """A result for a model the run never reached."""
    return {"name": model.get("display_name") or model["name"],
            "path": str(model["path"]),
            "source": model.get("source", "folder"),
            "size": model.get("size"),
            "load_ok": False, "load_time_s": None,
            "load_error": None, "prompts": [],
            "verdict": "not tested",
            "error": "Not tested: the run stopped before this model."}


def _embedding_skipped(model):
    """A result for an embedding-only model: not tested, never
    failed. It cannot write replies, so the smoke test does
    not apply to it. No load was attempted."""
    return {"name": model.get("display_name") or model["name"],
            "path": str(model["path"]),
            "source": model.get("source", "folder"),
            "size": model.get("size"),
            "load_ok": False, "load_time_s": None,
            "load_error": None, "prompts": [],
            "verdict": "not tested",
            "error": "Embedding only: this model can't write "
                     "replies, so the smoke test doesn't apply."}


def summarize(results):
    """Counts for a run: (passed, failed, not_tested)."""
    passed = sum(1 for r in results if r["verdict"] == "pass")
    failed = sum(1 for r in results if r["verdict"] == "fail")
    untested = sum(1 for r in results
                   if r["verdict"] == "not tested")
    return passed, failed, untested
