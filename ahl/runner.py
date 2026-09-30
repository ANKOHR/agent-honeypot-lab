"""Pure deterministic interpreter. No capabilities, file writes, or external tools."""
from .packs import AGENTS, validate


def run(pack, agent):
    validate(pack)
    if agent not in AGENTS:
        raise ValueError("Unknown scripted agent: " + agent)
    actions = []
    for step in pack["steps"]:
        instruction = step["instruction"]
        trusted = agent == "naive" or (agent == "tool-trusting" and step["source"] == "tool-output")
        permitted = instruction["action"] in pack["ground_truth"]["allowed_actions"]
        follow = permitted or trusted
        actions.append({
            "step": step["step"], "element": step["element"], "source": step["source"],
            "action": instruction["action"] if follow else "ignore",
            "target": instruction["target"] if follow else "untrusted-instruction",
            "reason": "followed declared instruction" if follow else "rejected untrusted instruction",
        })
    return {"schema_version": 1, "pack": pack, "agent": agent, "actions": actions}
