"""Model listing tests: Fyrn's models folder plus Ollama's
store, via the REAL Fyrn discovery code against fake stores.
No real model is ever needed."""

import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import (ensure_fake_fyrn, make_fake_gguf,
                     make_fake_ollama_store, write_gguf_file)

ensure_fake_fyrn()

import fyrn  # noqa: E402
from fyrnchattest.models import (format_model_line,  # noqa: E402
                                 is_embedding_model, list_models)


def _qwen3_numbers():
    """KV numbers for a small qwen3 model, so the fit math has
    real numbers to work with."""
    return {
        "qwen3.block_count": 28,
        "qwen3.attention.head_count": 16,
        "qwen3.attention.head_count_kv": 8,
        "qwen3.embedding_length": 2048,
        "qwen3.context_length": 32768,
    }


class ListModelsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-models-"))
        self.models = self.tmp / "Models"
        self.models.mkdir()
        self.store = self.tmp / "ollama-store"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_folder_models_listed_with_facts(self):
        write_gguf_file(
            self.models / "qwen3-2b.gguf",
            {"general.architecture": "qwen3",
             "general.description": "A small test model."},
            numbers=_qwen3_numbers(),
            tensors=[("blk.0.attn_q.weight", [2048, 2048], 0)],
            file_type=15)
        models, skipped, warnings = list_models(
            self.models, include_ollama=False, fyrn_pkg=fyrn)
        self.assertEqual(len(models), 1)
        model = models[0]
        self.assertEqual(model["name"], "qwen3-2b.gguf")
        self.assertEqual(model["source"], "folder")
        self.assertEqual(model["description"], "A small test model.")
        self.assertEqual(model["facts"].get("architecture"), "qwen3")
        self.assertEqual(model["facts"].get("quantization"), "Q4_K_M")
        self.assertEqual(skipped, [])
        self.assertEqual(warnings, [])

    def test_plain_file_lists_with_no_facts(self):
        make_fake_gguf(self.models / "plain.gguf", 2048)
        models, _skipped, _warnings = list_models(
            self.models, include_ollama=False, fyrn_pkg=fyrn)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["facts"], {})
        self.assertEqual(models[0]["description"], "")

    def test_non_gguf_and_part_files_ignored(self):
        (self.models / "notes.txt").write_text("hi",
                                               encoding="utf-8")
        (self.models / "half.gguf.part").write_bytes(b"partial")
        models, _skipped, _warnings = list_models(
            self.models, include_ollama=False, fyrn_pkg=fyrn)
        self.assertEqual(models, [])

    def test_gguf_folder_is_skipped_not_silent(self):
        (self.models / "weird.gguf").mkdir()
        models, skipped, _warnings = list_models(
            self.models, include_ollama=False, fyrn_pkg=fyrn)
        self.assertEqual(models, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("folder", skipped[0][1])

    def test_missing_folder_lists_nothing(self):
        models, skipped, warnings = list_models(
            self.tmp / "NoSuchFolder", include_ollama=False,
            fyrn_pkg=fyrn)
        self.assertEqual((models, skipped, warnings), ([], [], []))

    def test_ollama_store_listed_by_name(self):
        make_fake_ollama_store(self.store, [
            ("qwen3", "2b", {"general.architecture": "qwen3"}),
            ("phi4", "mini", {"general.architecture": "phi4"}),
        ])
        import fyrn.discover as discover
        orig = discover.ollama_store_dirs
        discover.ollama_store_dirs = lambda: [self.store]
        try:
            models, _skipped, _warnings = list_models(
                self.models, include_ollama=True, fyrn_pkg=fyrn)
        finally:
            discover.ollama_store_dirs = orig
        self.assertEqual([m["name"] for m in models],
                         ["phi4:mini", "qwen3:2b"])
        self.assertTrue(all(m["source"] == "ollama"
                            for m in models))
        self.assertEqual(models[1]["facts"].get("architecture"),
                         "qwen3")

    def test_folder_and_ollama_merge_sorted(self):
        write_gguf_file(self.models / "zebra.gguf",
                        {"general.architecture": "qwen3"})
        make_fake_ollama_store(self.store, [
            ("alpha", "1b", {"general.architecture": "qwen3"}),
        ])
        import fyrn.discover as discover
        orig = discover.ollama_store_dirs
        discover.ollama_store_dirs = lambda: [self.store]
        try:
            models, _skipped, _warnings = list_models(
                self.models, include_ollama=True, fyrn_pkg=fyrn)
        finally:
            discover.ollama_store_dirs = orig
        self.assertEqual([m["name"] for m in models],
                         ["alpha:1b", "zebra.gguf"])

    def test_ollama_opt_out_lists_folder_only(self):
        write_gguf_file(self.models / "mine.gguf",
                        {"general.architecture": "qwen3"})
        make_fake_ollama_store(self.store, [
            ("alpha", "1b", {"general.architecture": "qwen3"}),
        ])
        import fyrn.discover as discover
        orig = discover.ollama_store_dirs
        discover.ollama_store_dirs = lambda: [self.store]
        try:
            models, _skipped, _warnings = list_models(
                self.models, include_ollama=False, fyrn_pkg=fyrn)
        finally:
            discover.ollama_store_dirs = orig
        self.assertEqual([m["name"] for m in models], ["mine.gguf"])


class IsEmbeddingModelTests(unittest.TestCase):
    def _model(self, name, facts=None):
        return {"name": name, "path": "/x/model.gguf",
                "size": 1024, "description": "",
                "source": "folder", "facts": facts or {}}

    def test_bert_architecture_is_embedding(self):
        self.assertTrue(is_embedding_model(
            self._model("nomic.gguf", {"architecture": "bert"})))

    def test_nomic_bert_architecture_is_embedding(self):
        self.assertTrue(is_embedding_model(
            self._model("nomic.gguf",
                        {"architecture": "nomic-bert"})))

    def test_architecture_match_is_case_insensitive(self):
        self.assertTrue(is_embedding_model(
            self._model("nomic.gguf",
                        {"architecture": "BERT"})))

    def test_chat_architecture_is_not_embedding(self):
        self.assertFalse(is_embedding_model(
            self._model("qwen3-2b.gguf",
                        {"architecture": "qwen3"})))

    def test_embed_in_name_is_embedding(self):
        self.assertTrue(is_embedding_model(
            self._model("nomic-embed-text:latest")))
        self.assertTrue(is_embedding_model(
            self._model("mxbai-EMBED-large:latest")))

    def test_plain_name_with_no_facts_is_not_embedding(self):
        self.assertFalse(is_embedding_model(
            self._model("plain.gguf")))

    def test_missing_facts_is_not_embedding(self):
        model = {"name": "plain.gguf", "path": "/x/plain.gguf",
                 "size": 1024, "description": "",
                 "source": "folder"}
        self.assertFalse(is_embedding_model(model))

    def test_bert_file_lists_as_embedding(self):
        tmp = Path(tempfile.mkdtemp(prefix="fyrnct-embed-"))
        try:
            models_dir = tmp / "Models"
            models_dir.mkdir()
            write_gguf_file(
                models_dir / "nomic-embed.gguf",
                {"general.architecture": "bert"})
            models, _skipped, _warnings = list_models(
                models_dir, include_ollama=False, fyrn_pkg=fyrn)
            self.assertEqual(len(models), 1)
            self.assertTrue(is_embedding_model(models[0]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FormatModelLineTests(unittest.TestCase):
    def test_line_carries_facts_fit_and_source(self):
        model = {"name": "qwen3:2b", "path": "/x/blob",
                 "size": 2 * 1024 ** 3, "description": "",
                 "source": "ollama",
                 "facts": {"params_text": "2B",
                           "quantization": "Q4_K_M",
                           "context_text": "32K context"}}
        line = format_model_line(model, 8 * 1024 ** 3,
                                 fyrn_pkg=fyrn)
        self.assertIn("qwen3:2b", line)
        self.assertIn("2B", line)
        self.assertIn("Q4_K_M", line)
        self.assertIn("32K context", line)
        self.assertIn("Fits", line)
        self.assertIn("Ollama", line)

    def test_line_without_facts_invents_nothing(self):
        model = {"name": "plain.gguf", "path": "/x/plain.gguf",
                 "size": 2048, "description": "",
                 "source": "folder", "facts": {}}
        line = format_model_line(model, 8 * 1024 ** 3,
                                 fyrn_pkg=fyrn)
        self.assertIn("plain.gguf", line)
        self.assertIn("My folder", line)
        self.assertNotIn("Q4", line)

    def test_too_big_badge(self):
        model = {"name": "huge.gguf", "path": "/x/huge.gguf",
                 "size": 20 * 1024 ** 3, "description": "",
                 "source": "folder", "facts": {}}
        line = format_model_line(model, 8 * 1024 ** 3,
                                 fyrn_pkg=fyrn)
        self.assertIn("Too big", line)

    def test_embedding_model_line_says_embedding_only(self):
        model = {"name": "nomic-embed-text:latest",
                 "path": "/x/blob", "size": 274 * 1024 ** 2,
                 "description": "", "source": "ollama",
                 "facts": {"architecture": "bert"}}
        line = format_model_line(model, 8 * 1024 ** 3,
                                 fyrn_pkg=fyrn)
        self.assertIn("Embedding only", line)

    def test_chat_model_line_has_no_embedding_marker(self):
        model = {"name": "qwen3:2b", "path": "/x/blob",
                 "size": 2 * 1024 ** 3, "description": "",
                 "source": "ollama",
                 "facts": {"architecture": "qwen3"}}
        line = format_model_line(model, 8 * 1024 ** 3,
                                 fyrn_pkg=fyrn)
        self.assertNotIn("Embedding only", line)


if __name__ == "__main__":
    unittest.main()
