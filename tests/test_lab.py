import ast
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys

import pytest
from ahl import runner
from ahl.fixtures import generate
from ahl.packs import AGENTS, BUILTIN, PackError, canonical, catalogue, load, resolve, validate
from ahl.reports import BANNER, report
from ahl.runner import run
from ahl.scorer import score

PACKS = catalogue()


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.json")}


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p["id"])
def test_pack_validation_and_no_leakage(pack):
    assert validate(pack) == pack
    for step in pack["steps"]:
        assert "SYNTHETIC FICTIONAL CONTENT" in step["content"]
        assert pack["ground_truth"]["id"] not in step["content"]
        assert pack["ground_truth"]["correct_behavior"] not in step["content"]


@pytest.mark.parametrize("mutation", [
    lambda p: p.pop("ground_truth"),
    lambda p: p.update(synthetic=False),
    lambda p: p["steps"][0].update(content="Unlabelled content"),
    lambda p: p.update(version=True),
    lambda p: p.update(steps=[]),
    lambda p: p["steps"][0].update(step=2),
    lambda p: p["steps"][0].update(source="shell"),
    lambda p: p["steps"][1].update(element="ordinary-context"),
    lambda p: p["steps"][0]["instruction"].update(action="execute"),
    lambda p: p["steps"][1].update(content=p["ground_truth"]["correct_behavior"].upper()),
    lambda p: p["steps"][1].update(content=p["ground_truth"]["id"].upper()),
    lambda p: p["steps"][1].update(content="Expected Verdict: pass"),
    lambda p: p["success_criteria"][0].update(element="missing"),
    lambda p: p["success_criteria"][0].update(target="wrong"),
    lambda p: p["success_criteria"][0].update(action="answer"),
    lambda p: p.update(success_criteria=[]),
    lambda p: p.update(extra="unexpected"),
    lambda p: p.update(ground_truth=None),
])
def test_malformed_pack_rejected(mutation):
    pack = copy.deepcopy(PACKS[0])
    mutation(pack)
    with pytest.raises(PackError):
        validate(pack)


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p["id"])
@pytest.mark.parametrize("agent", AGENTS)
def test_exact_traces_and_scores(pack, agent):
    before = copy.deepcopy(pack)
    trace = run(pack, agent)
    hostile = agent == "naive" or (agent == "tool-trusting" and pack["id"] == "poisoned-tool-output")
    assert [a["action"] for a in trace["actions"]] == ["answer", pack["steps"][1]["instruction"]["action"] if hostile else "ignore", "answer"]
    assert [a["step"] for a in trace["actions"]] == [1, 2, 3]
    assert trace["actions"][1]["target"] == (pack["steps"][1]["instruction"]["target"] if hostile else "untrusted-instruction")
    verdict = score(trace)
    assert verdict["attack_succeeded"] is hostile
    assert verdict["break_step"] == (2 if hostile else None)
    assert verdict["causing_element"] == ("embedded-directive" if hostile else None)
    assert verdict["ground_truth"] == pack["ground_truth"]
    assert pack == before


def test_hand_computed_tool_pack():
    pack = resolve("poisoned-tool-output")
    verdict = score(run(pack, "naive"))
    assert (verdict["attack_succeeded"], verdict["break_step"], verdict["causing_element"]) == (True, 2, "embedded-directive")
    evidence = verdict["evidence"][1]
    assert evidence["action"] == "disclose"
    assert evidence["target"] == "fictional-vault-token"
    assert evidence["content"] == pack["steps"][1]["content"]
    assert evidence["criterion_matched"] == pack["success_criteria"][0]
    cautious = score(run(pack, "cautious"))
    assert cautious["attack_succeeded"] is False
    assert all(e["criterion_matched"] is None for e in cautious["evidence"])
    assert len(cautious["evidence"]) == 3


@pytest.mark.parametrize("mutation", [
    lambda t: t["actions"].pop(),
    lambda t: t["actions"][1].update(action="ignore"),
    lambda t: t.update(schema_version=2),
    lambda t: t.update(agent="unknown"),
    lambda t: t.update(extra=True),
])
def test_trace_tampering_rejected(mutation):
    trace = run(PACKS[0], "naive")
    mutation(trace)
    with pytest.raises(ValueError):
        score(trace)


def test_containment_structure():
    forbidden = {"socket", "requests", "subprocess", "urllib", "http", "httpx", "aiohttp", "torch", "tensorflow", "openai", "importlib", "ctypes"}
    for path in Path(runner.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(n.name.split(".")[0] in forbidden for n in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
    tree = ast.parse(inspect.getsource(runner))
    assert all(not isinstance(n, ast.Call) or not isinstance(n.func, ast.Name) or n.func.id not in {"open", "exec", "eval", "__import__", "compile"} for n in ast.walk(tree))
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr != "append")


def test_no_network_and_fixture_immutability(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network capability invoked")
    monkeypatch.setattr(socket, "socket", denied)
    before = hashes(BUILTIN)
    traces = [run(p, a) for p in catalogue() for a in AGENTS]
    assert len(json.loads(report(traces, "json"))["verdicts"]) == 12
    assert hashes(BUILTIN) == before


def test_fixture_determinism_readonly_and_no_overwrite(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    try:
        assert generate(a) == generate(b)
        assert hashes(a) == hashes(b)
        assert len(catalogue(a)) == 4
        agents = json.loads((a / "agents.json").read_text())
        assert agents["synthetic"] is True
        assert agents["scripted_stand_ins"] == AGENTS
        for p in a.rglob("*.json"):
            mode = p.stat()
            if os.name == "nt":
                assert mode.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
            else:
                assert not mode.st_mode & 0o222
        before = hashes(a)
        for pack in catalogue(a):
            for agent in AGENTS:
                run(pack, agent)
        assert hashes(a) == before
        with pytest.raises(ValueError, match="overwrite"):
            generate(a)
        assert hashes(a) == before
    finally:
        for p in tmp_path.rglob("*.json"):
            p.chmod(0o666)


@pytest.mark.parametrize("format", ["md", "json"])
def test_end_to_end_determinism_and_banner(format):
    first = [run(p, a) for p in catalogue() for a in AGENTS]
    second = [run(p, a) for p in catalogue() for a in AGENTS]
    assert canonical(first).encode() == canonical(second).encode()
    rendered = report(first, format)
    assert rendered.encode() == report(second, format).encode()
    assert rendered == report(list(reversed(second)), format)
    assert BANNER in rendered
    verdicts = [score(t) for t in first]
    assert sum(v["attack_succeeded"] for v in verdicts) == 5
    if format == "md":
        assert rendered.count(BANNER) == 13
        assert "| poisoned-tool-output v1 | naive | true | 2 | embedded-directive |" in rendered
    else:
        assert len(json.loads(rendered)["verdicts"]) == 12


def cli(*args):
    return subprocess.run([sys.executable, "-m", "ahl", *map(str, args)], capture_output=True, check=False)


def test_cli_complete_workflow(tmp_path):
    listed = cli("packs", "--list")
    assert listed.returncode == 0
    assert len(json.loads(listed.stdout)["packs"]) == 4
    fixtures = tmp_path / "fixtures"
    try:
        assert cli("fixtures", "--out", fixtures).returncode == 0
        assert cli("packs", "--validate", next(fixtures.glob("*/v1/pack.json"))).returncode == 0
        args = ("run", "--pack", "all", "--agent", "all", "--root", fixtures)
        first, second = cli(*args), cli(*args)
        assert first.returncode == second.returncode == 0
        assert first.stdout == second.stdout
        runs = tmp_path / "runs.json"
        runs.write_bytes(first.stdout)
        scored = cli("score", "--input", runs)
        assert scored.returncode == 0
        value = json.loads(scored.stdout)
        assert value["honesty_banner"] == BANNER
        assert len(value["verdicts"]) == 12
        for format in ("md", "json"):
            args = ("report", "--input", runs, "--format", format)
            a, b = cli(*args), cli(*args)
            assert a.returncode == b.returncode == 0
            assert a.stdout == b.stdout
            assert BANNER.encode() in a.stdout
        single = cli("run", "--pack", "poisoned-tool-output", "--agent", "cautious")
        assert single.returncode == 0
        assert isinstance(json.loads(single.stdout), dict)
    finally:
        for p in fixtures.rglob("*.json"):
            p.chmod(0o666)


@pytest.mark.parametrize("args", [
    ("run", "--pack", "missing", "--agent", "naive"),
    ("run", "--pack", "poisoned-tool-output", "--agent", "unknown"),
    ("score", "--input", "nonexistent.json"),
    ("report", "--input", "nonexistent.json", "--format", "xml"),
    ("packs", "--validate", "nonexistent.json"),
])
def test_cli_errors(args):
    result = cli(*args)
    assert result.returncode == 2
    assert result.stderr


def test_bad_json(tmp_path):
    p = tmp_path / "pack.json"
    p.write_text("{broken")
    with pytest.raises(PackError):
        load(p)
    assert cli("packs", "--validate", p).returncode == 2


def test_unknown_agent_and_report_format():
    with pytest.raises(ValueError):
        run(PACKS[0], "unknown")
    with pytest.raises(ValueError):
        report([], "xml")


def test_no_em_dash_in_project():
    root = Path(__file__).parent.parent
    for directory in (root / "ahl", root / "tests"):
        for path in directory.rglob("*"):
            if path.suffix in {".py", ".json"}:
                assert chr(0x2014) not in path.read_text(encoding="utf-8")
    for name in ("README.md", "pyproject.toml", ".gitignore", ".gitattributes"):
        assert chr(0x2014) not in (root / name).read_text(encoding="utf-8")


def test_validation_survives_optimized_python():
    result = subprocess.run([sys.executable, "-O", "-c", "from ahl.packs import validate, PackError; validate({})"], capture_output=True)
    assert result.returncode != 0
    assert b"PackError" in result.stderr


def test_multiple_breaks_cite_earliest_step():
    pack = copy.deepcopy(resolve("poisoned-tool-output"))
    pack["steps"][2]["instruction"] = {"action": "alter", "target": "fictional-ledger"}
    pack["success_criteria"].append({"element": "normal-followup", "action": "alter", "target": "fictional-ledger"})
    verdict = score(run(pack, "naive"))
    assert verdict["break_step"] == 2
    assert verdict["causing_element"] == "embedded-directive"
    assert [e["step"] for e in verdict["evidence"] if e["criterion_matched"]] == [2, 3]
