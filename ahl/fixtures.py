"""Generate labelled, immutable fixture copies and agent declarations."""
from pathlib import Path
from .packs import AGENTS, canonical, catalogue


def generate(out):
    out = Path(out)
    files = {out / "agents.json": canonical({"synthetic": True, "scripted_stand_ins": AGENTS})}
    for pack in catalogue():
        files[out / pack["id"] / "v1" / "pack.json"] = canonical(pack)
    for path, content in files.items():
        if path.exists():
            raise ValueError("Refusing to overwrite existing fixture: " + str(path))
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        path.chmod(0o444)
    return sorted(str(p.relative_to(out)) for p in files)
