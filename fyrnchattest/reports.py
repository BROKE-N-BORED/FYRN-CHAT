"""Smoke test reports: one Markdown file and one JSON file per
run, under the tool's own reports folder.

The Markdown report is the one a person reads: a summary line,
a table of results, and the reply excerpts per model. The JSON
report is the same run in structured form, for comparing runs
or sending to someone who asked for the numbers.

Standard library only.
"""

import json
import os
from datetime import datetime

from .smoke import PROMPTS, summarize


def reports_dir(home):
    """The reports folder under the tool's home directory."""
    return os.path.join(str(home), "reports")


def new_run_dir(home, when=None):
    """A fresh folder for one run: reports/<timestamp>/, created
    on demand. The timestamp sorts runs in order."""
    when = when or datetime.now()
    stamp = when.strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(reports_dir(home), stamp)
    os.makedirs(path, exist_ok=True)
    return path


def _fmt_seconds(value):
    """A duration in plain words: 1.2 s, 45 ms, or a dash when
    there is no measurement."""
    if value is None:
        return "-"
    if value < 1:
        return "%d ms" % round(value * 1000)
    return "%.1f s" % value


def _fmt_speed(value):
    """A decode speed in plain words: 12.3 tok/s, or a dash."""
    if value is None:
        return "-"
    return "%.1f tok/s" % value


def format_report_md(results, meta):
    """A run as Markdown: summary, table, per-model details."""
    passed, failed, untested = summarize(results)
    lines = []
    lines.append("# Fyrn Chat Test smoke test")
    lines.append("")
    lines.append("Run on %s." % meta.get("when", "an unknown date"))
    lines.append("")
    if meta.get("fyrn_dir"):
        lines.append("Fyrn install: %s" % meta["fyrn_dir"])
    if meta.get("fyrn_version"):
        lines.append("Fyrn version: %s" % meta["fyrn_version"])
    if meta.get("fyrn_dir") or meta.get("fyrn_version"):
        lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("%d models tested: %d passed, %d failed, %d not "
                 "tested." % (len(results), passed, failed, untested))
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Model | Load | Prompt 1 | Prompt 2 | First "
                 "token | Speed | Verdict |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for result in results:
        prompts = result.get("prompts") or []
        first = prompts[0] if len(prompts) > 0 else None
        second = prompts[1] if len(prompts) > 1 else None
        if result.get("load_ok"):
            load = "ok (%s)" % _fmt_seconds(
                result.get("load_time_s"))
        elif result.get("load_error"):
            load = "failed"
        else:
            # No load was attempted: the run stopped before
            # this model, or it is an embedding model the test
            # skips. Not a failure.
            load = "-"
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            result["name"], load,
            _prompt_cell(first), _prompt_cell(second),
            _fmt_seconds(first.get("ttft_s") if first else None),
            _fmt_speed(first.get("tokens_per_sec")
                       if first else None),
            result["verdict"]))
    lines.append("")
    lines.append("## Details")
    lines.append("")
    for result in results:
        lines.append("### %s (%s)" % (result["name"],
                                      result["verdict"]))
        lines.append("")
        if result.get("load_error"):
            lines.append("Load failed: %s" % result["load_error"])
            lines.append("")
        for entry in result.get("prompts") or []:
            lines.append("Prompt: %s" % entry["prompt"])
            lines.append("")
            if entry.get("error"):
                lines.append("Error: %s" % entry["error"])
            elif entry.get("excerpt"):
                lines.append("Reply (first 200 characters):")
                lines.append("")
                lines.append("```")
                lines.append(entry["excerpt"])
                lines.append("```")
            else:
                lines.append("No reply.")
            lines.append("")
        if not result.get("prompts"):
            lines.append("No prompts were run.")
            if result.get("error") \
                    and not result.get("load_error"):
                # The reason nothing ran, when the run recorded
                # one (an embedding model the test skips, or a
                # run that stopped before this model). A load
                # failure already has its own line above.
                lines.append("")
                lines.append(result["error"])
            lines.append("")
    return "\n".join(lines)


def _prompt_cell(entry):
    """One prompt's cell in the results table."""
    if entry is None:
        return "-"
    if entry.get("error"):
        return "failed"
    if entry.get("replied"):
        return "replied"
    return "no reply"


def format_report_json(results, meta):
    """A run as a JSON string: the same data, structured."""
    passed, failed, untested = summarize(results)
    payload = {
        "app": "fyrn-chat-test",
        "when": meta.get("when"),
        "fyrn_dir": meta.get("fyrn_dir"),
        "fyrn_version": meta.get("fyrn_version"),
        "prompts": list(PROMPTS),
        "summary": {"tested": len(results), "passed": passed,
                    "failed": failed, "not_tested": untested},
        "results": results,
    }
    return json.dumps(payload, indent=2) + "\n"


def write_report(results, run_dir, meta):
    """Write report.md and report.json for a run into run_dir.
    Returns (md_path, json_path)."""
    md_path = os.path.join(run_dir, "report.md")
    json_path = os.path.join(run_dir, "report.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(format_report_md(results, meta))
    with open(json_path, "w", encoding="utf-8") as fh:
        fh.write(format_report_json(results, meta))
    return md_path, json_path
