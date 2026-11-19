"""The models Fyrn sees: its models folder plus Ollama's store.

The listing reuses Fyrn's own code (fyrn.core.scan_models and
fyrn.discover.discover_ollama_models, imported from the
installed Fyrn), so the test tool lists exactly the models
Fyrn itself lists. It never writes to either store.

Standard library only, apart from the imported fyrn package.
The fyrn package is injected (list_models takes it as an
argument) so tests can point at a known copy.
"""

import importlib
from pathlib import Path


def _submodules(fyrn_pkg):
    """Fyrn's core and discover submodules, imported on demand:
    `import fyrn` alone does not bind its submodules, so
    fyrn_pkg.core would fail until something imports them."""
    core = importlib.import_module(".core", fyrn_pkg.__name__)
    discover = importlib.import_module(".discover",
                                       fyrn_pkg.__name__)
    return core, discover


def list_models(models_folder, include_ollama=True, fyrn_pkg=None):
    """List the models Fyrn sees.

    Returns (models, skipped, warnings). models is a list of
    dicts with name, path, size, description, source ("folder"
    or "ollama") and facts, sorted by name. skipped is the list
    of (name, reason) for .gguf entries that could not be read,
    and warnings is the list of (name, reason) for Ollama stores
    that could not be read: nothing is silently dropped.
    """
    if fyrn_pkg is None:
        import fyrn as fyrn_pkg
    core, discover = _submodules(fyrn_pkg)
    models, skipped = core.scan_models(models_folder)
    warnings = []
    if include_ollama:
        discovered, warnings = discover.discover_ollama_models()
        seen = {str(Path(m["path"]).resolve())
                for m in models
                if _resolvable(m["path"])}
        seen |= {str(m["path"]) for m in models}
        for model in discovered:
            if str(model["path"]) in seen:
                continue
            seen.add(str(model["path"]))
            models.append(model)
    models.sort(key=lambda m: m["name"].lower())
    return models, skipped, warnings


def _resolvable(path):
    """True when a path resolves without error (a missing file
    still has a string form, which the caller uses instead)."""
    try:
        Path(path).resolve()
        return True
    except OSError:
        return False


# GGUF architectures that are embedding-only models (encoder
# only, no decoder): they load fine but cannot write replies,
# so llama-cpp-python's generate fails on them with "Memory is
# not initialized". Fyrn's core lists vision architectures but
# no embedding ones, so the list lives here.
EMBEDDING_ARCHITECTURES = frozenset(("bert", "nomic-bert"))


def is_embedding_model(model):
    """True when a model looks like an embedding-only model.

    A heuristic, not a proof: the header architecture is a
    BERT-family embedding arch, or the name says "embed"
    (Ollama names like nomic-embed-text:latest). An embedding
    model turns text into numbers for search; it cannot write
    replies, so it cannot chat and the smoke test does not
    apply to it. A model the header says nothing about, with
    no "embed" in its name, is treated as a chat model: the
    check never blocks a model it cannot identify.
    """
    facts = model.get("facts") or {}
    arch = facts.get("architecture")
    if isinstance(arch, str) \
            and arch.lower() in EMBEDDING_ARCHITECTURES:
        return True
    return "embed" in (model.get("name") or "").lower()


def format_model_line(model, free_bytes, fyrn_pkg=None):
    """One model as the two lines the list shows: the name, then
    size, facts, fit badge, and where it came from. A model the
    header says nothing about shows no facts: nothing is ever
    invented. An embedding model carries an "Embedding only"
    marker: it cannot chat."""
    if fyrn_pkg is None:
        import fyrn as fyrn_pkg
    core, _discover = _submodules(fyrn_pkg)
    facts = core.format_facts(model.get("facts") or {})
    status = core.fit_status(model["size"], free_bytes)
    badge = {"fits": "Fits", "tight": "Tight",
             "too big": "Too big", "unknown": "Fit unknown"
             }.get(status, "Fit unknown")
    source = "Ollama" if model.get("source") == "ollama" \
        else "My folder"
    parts = [core.format_size(model["size"])]
    if facts:
        parts.append(facts)
    if is_embedding_model(model):
        parts.append("Embedding only")
    parts.append(badge)
    parts.append(source)
    return "%s\n%s" % (model["name"], "  ·  ".join(parts))
