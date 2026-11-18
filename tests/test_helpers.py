"""Tests for the test helpers' Fyrn-tree resolution: the suite
must find real Fyrn code on any machine where Fyrn is installed,
not only in the sandbox where the canonical tree lives."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from helpers import resolve_fyrn_tree


class ResolveFyrnTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-resolve-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _tree_with_code(self, name):
        tree = self.tmp / name
        (tree / "fyrn").mkdir(parents=True)
        (tree / "fyrn" / "core.py").write_text("# fake core\n",
                                               encoding="utf-8")
        return tree

    def _env_without_override(self):
        env = dict(os.environ)
        env.pop("FYRN_INSTALL_DIR", None)
        return env

    def test_env_override_wins(self):
        tree = self._tree_with_code("override")
        with mock.patch.dict(os.environ,
                             {"FYRN_INSTALL_DIR": str(tree)}):
            self.assertEqual(resolve_fyrn_tree(), tree)

    def test_env_override_without_code_is_ignored(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        with mock.patch.dict(os.environ,
                             {"FYRN_INSTALL_DIR": str(empty)}):
            with mock.patch.object(helpers, "CANONICAL_FYRN",
                                   self.tmp / "no-such-tree"):
                with mock.patch.object(
                        helpers, "find_fyrn_install",
                        side_effect=Exception("no install")):
                    self.assertIsNone(resolve_fyrn_tree())

    def test_canonical_fallback(self):
        tree = self._tree_with_code("canonical")
        with mock.patch.dict(os.environ,
                             self._env_without_override(),
                             clear=True):
            with mock.patch.object(helpers, "CANONICAL_FYRN", tree):
                self.assertEqual(resolve_fyrn_tree(), tree)

    def test_finder_fallback(self):
        tree = self._tree_with_code("found")
        install = mock.Mock()
        install.folder = tree
        with mock.patch.dict(os.environ,
                             self._env_without_override(),
                             clear=True):
            with mock.patch.object(helpers, "CANONICAL_FYRN",
                                   self.tmp / "no-such-tree"):
                with mock.patch.object(helpers, "find_fyrn_install",
                                       return_value=install):
                    self.assertEqual(resolve_fyrn_tree(), tree)

    def test_missing_everything_returns_none(self):
        with mock.patch.dict(os.environ,
                             self._env_without_override(),
                             clear=True):
            with mock.patch.object(helpers, "CANONICAL_FYRN",
                                   self.tmp / "no-such-tree"):
                with mock.patch.object(
                        helpers, "find_fyrn_install",
                        side_effect=Exception("no install")):
                    self.assertIsNone(resolve_fyrn_tree())


if __name__ == "__main__":
    unittest.main()
