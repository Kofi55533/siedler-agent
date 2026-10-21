"""Resolve a private fixture's image from the same downloaded release bundle."""
import json
from pathlib import Path
import shutil


def resolve(directory, archive_root, newfix, verify):
    directory, archive_root = Path(directory), Path(archive_root)
    scenario = directory / "scenario.json"
    text = scenario.read_text().replace("/workspace/siedler/artifacts/new-fixtures", str(newfix))
    text = text.replace("/workspace/siedler/artifacts/codex-verify-infra", str(verify))
    value = json.loads(text)
    image = Path(value.get("image", ""))
    if not value.get("image"):
        raise ValueError("scenario has no image")
    if not image.is_absolute():
        image = directory / image
    if not image.is_file():
        # Map-start releases carry the captured image beside scenario.json or
        # under images/<map>/. Metadata imported from Git alone cannot supply it.
        beside = directory / image.name
        candidates = sorted(archive_root.rglob(image.name))
        matching = [p for p in candidates if p.parent.name == image.parent.name]
        if beside.is_file():
            source = beside
        elif len(matching) == 1:
            source = matching[0]
        elif len(candidates) == 1:
            source = candidates[0]
        else:
            raise ValueError("missing or ambiguous bundled start image")
        sidecar = Path(str(source) + ".state.json")
        if not sidecar.is_file():
            raise ValueError("bundled start image has no state sidecar")
        image = beside
        if source.resolve() != image.resolve():
            shutil.copy2(source, image)
            shutil.copy2(sidecar, str(image) + ".state.json")
            # Metadata-only PRs keep commands/scenario authoritative; retain
            # the bundle's reference streams needed by scenario consumers.
            for name in ("start.jsonl", "terminal.jsonl", "canary.jsonl.gz", "checksums.jsonl.gz",
                         "det.jsonl.gz", "detail.jsonl.gz", "wide.jsonl.gz", "commands.jsonl"):
                stream = source.parent / name
                if stream.is_file() and not (directory / name).exists():
                    shutil.copy2(stream, directory / name)
    if not Path(str(image) + ".state.json").is_file():
        raise ValueError("start image has no state sidecar")
    value["image"] = str(image.resolve())
    scenario.write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    import sys
    resolve(*sys.argv[1:])
