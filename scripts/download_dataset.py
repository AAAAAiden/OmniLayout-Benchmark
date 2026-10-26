"""Download and verify the graph-only dataset without requiring an EDA installation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_archive(archive: Path, output: Path, release: dict, manifest: dict, summary: dict) -> None:
    """Verify the trusted archive/member hashes before installing a fresh dataset."""
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists; choose a new directory: {output}")
    if archive.stat().st_size != release["bytes"] or digest_file(archive) != release["sha256"]:
        raise ValueError("Dataset archive SHA-256 or size does not match data/release.json")
    files = manifest["files"]
    if len(files) != release["graph_count"] or manifest["version"] != release["version"]:
        raise ValueError("Inconsistent release metadata")
    expected = {item["path"]: item for item in files}
    if len(expected) != len(files):
        raise ValueError("Duplicate graph paths in manifest")
    allowed = set(expected) | {"manifest.json", "summary.json", "LICENSE_DATA.md"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".omnilayout-", dir=output.parent) as temporary:
        staged = Path(temporary) / "dataset"
        staged.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != allowed:
                raise ValueError("Dataset archive contains unexpected, missing, or duplicate members")
            for info in infos:
                name = info.filename
                member = PurePosixPath(name)
                if member.is_absolute() or ".." in member.parts or "\\" in name or ":" in name or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("Unsafe dataset archive member")
                if name in expected and (len(member.parts) != 2 or member.parts[0] != "graph" or not name.endswith("_graph.json")):
                    raise ValueError("Dataset manifest contains a non-graph member")
                if name in expected and info.file_size != expected[name]["bytes"]:
                    raise ValueError(f"Incorrect graph size: {name}")
                content = bundle.read(info)
                if name in expected:
                    if hashlib.sha256(content).hexdigest() != expected[name]["sha256"]:
                        raise ValueError(f"Incorrect graph SHA-256: {name}")
                    if not isinstance(json.loads(content), dict):
                        raise ValueError(f"Graph must be a JSON object: {name}")
                elif name == "manifest.json" and json.loads(content) != manifest:
                    raise ValueError("Archive manifest differs from repository manifest")
                elif name == "summary.json" and json.loads(content) != summary:
                    raise ValueError("Archive summary differs from repository summary")
                destination = staged.joinpath(*member.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        # rename refuses to merge or overwrite a pre-existing dataset.
        staged.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use an already-downloaded release ZIP")
    parser.add_argument("--output", type=Path, default=ROOT / "datasets" / "graphs")
    args = parser.parse_args()
    release = json.loads((ROOT / "data" / "release.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((ROOT / "data" / "summary.json").read_text(encoding="utf-8"))
    if args.archive:
        install_archive(args.archive, args.output, release, manifest, summary)
    else:
        if not release["url"].startswith("https://github.com/AAAAAiden/OmniLayout-Benchmark/releases/download/"):
            raise ValueError("Unexpected dataset download host")
        with tempfile.TemporaryDirectory(prefix="omnilayout-download-") as temporary:
            archive = Path(temporary) / release["archive"]
            request = urllib.request.Request(release["url"], headers={"User-Agent": "OmniLayout-Benchmark"})
            with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            install_archive(archive, args.output, release, manifest, summary)
    print(f"Verified {release['graph_count']} graphs in {args.output.resolve() / 'graph'}")


if __name__ == "__main__":
    main()
