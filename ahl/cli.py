"""CLI for generating, running, scoring, and reporting synthetic fixtures."""
import argparse
import json
import sys
from pathlib import Path
from .fixtures import generate
from .packs import AGENTS, BUILTIN, PackError, canonical, catalogue, load, resolve
from .runner import run
from .scorer import score
from .reports import BANNER, report


def read_runs(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return value if isinstance(value, list) else [value]


def main(argv=None):
    parser = argparse.ArgumentParser(description=BANNER)
    sub = parser.add_subparsers(dest="command", required=True)
    packs = sub.add_parser("packs")
    packs.add_argument("--list", action="store_true")
    packs.add_argument("--validate", type=Path)
    packs.add_argument("--root", type=Path, default=BUILTIN)
    runner = sub.add_parser("run")
    runner.add_argument("--pack", required=True, help="Pack id or all")
    runner.add_argument("--agent", required=True, choices=[*AGENTS, "all"])
    runner.add_argument("--root", type=Path, default=BUILTIN)
    for name in ("score", "report"):
        command = sub.add_parser(name)
        command.add_argument("--input", required=True, type=Path)
        if name == "report":
            command.add_argument("--format", choices=["md", "json"], default="md")
    fixtures = sub.add_parser("fixtures")
    fixtures.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "packs":
            value = {"valid": load(args.validate)["id"]} if args.validate else {"synthetic": True, "packs": [{"id": p["id"], "version": p["version"], "description": p["description"]} for p in catalogue(args.root)], "scripted_agents": AGENTS}
            output = canonical(value)
        elif args.command == "fixtures":
            output = canonical({"synthetic": True, "files": generate(args.out)})
        elif args.command == "run":
            selected = catalogue(args.root) if args.pack == "all" else [resolve(args.pack, args.root)]
            agents = list(AGENTS) if args.agent == "all" else [args.agent]
            traces = [run(p, a) for p in selected for a in agents]
            output = canonical(traces if args.pack == "all" or args.agent == "all" else traces[0])
        elif args.command == "score":
            output = canonical({"honesty_banner": BANNER, "verdicts": [score(t) for t in read_runs(args.input)]})
        else:
            output = report(read_runs(args.input), args.format)
        sys.stdout.write(output)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("ahl: " + str(exc), file=sys.stderr)
        return 2
