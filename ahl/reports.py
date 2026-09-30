"""Canonical reports with a mandatory honesty boundary."""
from .packs import canonical
from .scorer import score

BANNER = "HONESTY BOUNDARY: Agents are deterministic scripted stand-ins, not language models or real agents. Content is synthetic. The lab is isolated by construction: fixture-only, no network, no real tools. Results do not establish real-world safety."


def report(traces, format="md"):
    verdicts = sorted([score(t) for t in traces], key=lambda v: (v["pack"], v["version"], v["agent"]))
    if format == "json":
        return canonical({"honesty_banner": BANNER, "verdicts": verdicts})
    if format != "md":
        raise ValueError("Unknown report format")
    lines = [BANNER, "", "# Agent Honeypot Lab", "", "| Pack | Agent | Attack succeeded | Break step | Causing element |", "| --- | --- | --- | --- | --- |"]
    for v in verdicts:
        lines.append(f"| {v['pack']} v{v['version']} | {v['agent']} | {str(v['attack_succeeded']).lower()} | {v['break_step'] or '-'} | {v['causing_element'] or '-'} |")
    for v in verdicts:
        lines.extend(["", f"## {v['pack']} / {v['agent']}", "", BANNER, "", "Ground truth and exact step evidence:", "", "```json", canonical(v).rstrip(), "```"])
    return "\n".join(lines) + "\n"
