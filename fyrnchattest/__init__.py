"""Fyrn Chat Test: a chat window and smoke test that run on the
Fyrn installed on this PC, instead of Ollama.

It finds the Fyrn install, runs under Fyrn's own venv, and
imports the installed fyrn package: the engine under test is
Fyrn itself, not a copy of it. It never modifies Fyrn's
install, its settings, or its models folder.
"""

__version__ = "0.1.1"
__app_name__ = "Fyrn Chat Test"
