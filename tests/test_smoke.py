"""Smoke test engine tests: pass, load failure, too big,
timeout, cancel, and the summary counts. The engine is Fyrn's
own FakeRunner with known replies and known timing, so no real
model is ever needed."""

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from helpers import ensure_fake_fyrn, make_fake_gguf

ensure_fake_fyrn()

import fyrn  # noqa: E402
from fyrn.core import ModelController  # noqa: E402
from fyrn.runner import FakeRunner, RunnerError  # noqa: E402
from fyrnchattest.smoke import (PROMPTS, run_smoke_test,  # noqa: E402
                                summarize)


def _models(folder, names):
    """Fake model dicts for files in folder."""
    out = []
    for name in names:
        path = Path(folder) / name
        make_fake_gguf(path, 2048)
        out.append({"name": name, "path": path,
                    "size": 2048, "description": "",
                    "source": "folder", "facts": {}})
    return out


class FailingRunner(FakeRunner):
    """A runner whose load always fails."""

    def load(self, path, progress_cb=None, cancel_event=None):
        raise RunnerError("The model could not be loaded (boom).")


class SilentRunner(FakeRunner):
    """A runner that loads fine but never replies."""

    def __init__(self):
        super().__init__(reply_text="")

    def generate(self, prompt, on_token=None, cancel_event=None,
                 history=None, images=None):
        # An empty reply: generate returns stats but no text.
        return {"tokens": 0, "elapsed_s": 0.0, "ttft_s": 0.0,
                "tokens_per_sec": 0.0}


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-smoke-"))
        self.models_dir = self.tmp / "Models"
        self.models_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _controller(self, runner):
        return ModelController(
            runner, free_ram_fn=lambda: 8 * 1024 ** 3)

    def test_all_pass(self):
        models = _models(self.models_dir, ["a.gguf", "b.gguf"])
        controller = self._controller(FakeRunner(
            reply_text="Hello. def add(a, b): return a + b"))
        results = run_smoke_test(models, controller)
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result["verdict"], "pass")
            self.assertTrue(result["load_ok"])
            self.assertEqual(len(result["prompts"]), 2)
            self.assertTrue(all(p["replied"]
                                for p in result["prompts"]))
            self.assertIn("Hello", result["prompts"][0]["excerpt"])
            self.assertIsNotNone(
                result["prompts"][0]["tokens_per_sec"])
        self.assertEqual(summarize(results), (2, 0, 0))
        self.assertFalse(controller.is_loaded)

    def test_load_failure_fails_the_model(self):
        models = _models(self.models_dir, ["a.gguf", "b.gguf"])
        controller = self._controller(FailingRunner())
        results = run_smoke_test(models, controller)
        self.assertEqual([r["verdict"] for r in results],
                         ["fail", "fail"])
        self.assertIn("boom", results[0]["load_error"])
        self.assertEqual(results[0]["prompts"], [])
        self.assertEqual(summarize(results), (0, 2, 0))

    def test_too_big_fails_before_loading(self):
        models = _models(self.models_dir, ["a.gguf"])
        controller = ModelController(
            FakeRunner(), free_ram_fn=lambda: 1024)
        results = run_smoke_test(models, controller)
        self.assertEqual(results[0]["verdict"], "fail")
        self.assertIn("will not fit", results[0]["load_error"])
        self.assertFalse(controller.is_loaded)

    def test_empty_reply_fails_the_model(self):
        models = _models(self.models_dir, ["a.gguf"])
        controller = self._controller(SilentRunner())
        results = run_smoke_test(models, controller)
        self.assertEqual(results[0]["verdict"], "fail")
        self.assertFalse(results[0]["prompts"][0]["replied"])

    def test_timeout_fails_and_skips_the_second_prompt(self):
        models = _models(self.models_dir, ["a.gguf"])
        slow = FakeRunner(reply_text=" ".join(
            ["word"] * 40), token_delay_s=0.2)
        controller = self._controller(slow)
        results = run_smoke_test(models, controller,
                                 prompt_timeout_s=1)
        self.assertEqual(results[0]["verdict"], "fail")
        self.assertEqual(results[0]["prompts"][0]["error"],
                         "No reply within 1 second.")
        self.assertIn("Skipped",
                      results[0]["prompts"][1]["error"])

    def test_cancel_before_start_marks_all_not_tested(self):
        models = _models(self.models_dir, ["a.gguf", "b.gguf"])
        controller = self._controller(FakeRunner())
        cancel = threading.Event()
        cancel.set()
        results = run_smoke_test(models, controller,
                                 user_cancel=cancel)
        self.assertEqual([r["verdict"] for r in results],
                         ["not tested", "not tested"])
        self.assertEqual(summarize(results), (0, 0, 2))
        self.assertFalse(controller.is_loaded)

    def test_cancel_mid_run_stops_cleanly(self):
        models = _models(self.models_dir,
                         ["a.gguf", "b.gguf", "c.gguf"])
        controller = self._controller(FakeRunner(
            reply_text="Hello.", token_delay_s=0.05))
        cancel = threading.Event()
        seen = []

        def _progress(kind, index, name, detail):
            seen.append((kind, index, name))
            if kind == "prompt_started" and index == 0:
                cancel.set()

        results = run_smoke_test(models, controller,
                                 on_progress=_progress,
                                 user_cancel=cancel)
        self.assertEqual(results[0]["verdict"], "not tested")
        self.assertEqual(results[1]["verdict"], "not tested")
        self.assertEqual(results[2]["verdict"], "not tested")
        self.assertEqual(summarize(results), (0, 0, 3))
        self.assertFalse(controller.is_loaded)
        self.assertTrue(any(k == "model_started"
                            for k, _i, _n in seen))

    def test_progress_reports_each_step(self):
        models = _models(self.models_dir, ["a.gguf"])
        controller = self._controller(FakeRunner())
        seen = []

        def _progress(kind, index, name, detail):
            seen.append(kind)

        run_smoke_test(models, controller, on_progress=_progress)
        self.assertIn("model_started", seen)
        self.assertIn("prompt_started", seen)
        self.assertIn("model_done", seen)

    def test_model_done_callback_fires_per_model(self):
        models = _models(self.models_dir, ["a.gguf", "b.gguf"])
        controller = self._controller(FakeRunner())
        done = []

        def _done(index, result):
            done.append((index, result["verdict"]))

        run_smoke_test(models, controller, on_model_done=_done)
        self.assertEqual(done, [(0, "pass"), (1, "pass")])

    def test_prompts_are_the_canned_two(self):
        self.assertEqual(len(PROMPTS), 2)
        self.assertIn("Say hello", PROMPTS[0])
        self.assertIn("adds two numbers", PROMPTS[1])


if __name__ == "__main__":
    unittest.main()
