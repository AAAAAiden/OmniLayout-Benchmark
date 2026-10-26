"""Package only validated processed board graphs, with hashes and no raw EDA inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v0.1.0"
GRAPH_KEYS = {"netlist", "board_boundary", "ic_library", "ic_position", "ic_dimension"}
RAW_MARKERS = (b"<?xml", b"<eagle", b"<board", b"<schematic")
SECRET_PATTERN = re.compile(rb"AIza[0-9A-Za-z_-]{35}|sk-(?:proj-|or-v1-)?[0-9A-Za-z_-]{30,}|gh[pousr]_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{30,}")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def encode_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def validate_graph(content: bytes, filename: str) -> dict:
    """Reject partial model predictions and raw XML disguised as graph JSON."""
    if not filename.endswith("_graph.json") or Path(filename).name != filename:
        raise ValueError(f"Unexpected graph filename: {filename}")
    if SECRET_PATTERN.search(content) or any(marker in content.lower() for marker in RAW_MARKERS):
        raise ValueError(f"Unexpected raw source or credential-like content in {filename}")
    graph = json.loads(content, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Non-finite JSON number: {value}")))
    if not isinstance(graph, dict) or set(graph) != GRAPH_KEYS:
        raise ValueError(f"Expected complete five-section ground-truth graph: {filename}")
    for key in GRAPH_KEYS - {"board_boundary"}:
        if not isinstance(graph[key], list):
            raise ValueError(f"Expected list for {key} in {filename}")
    if not isinstance(graph["board_boundary"], dict):
        raise ValueError(f"Expected board boundary object in {filename}")
    # Re-encoding also rejects non-finite numbers parsed from numeric overflow.
    encode_json(graph)
    return graph


def package_dataset(source: Path, output: Path, metadata: Path, version: str = VERSION) -> dict:
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Source must be a directory of processed graph JSON files")
    candidates = sorted(source.glob("*_graph.json"), key=lambda path: path.name)
    if not candidates:
        raise ValueError("No processed *_graph.json files found")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Version must look like v0.1.0")
    files = []
    contents = []
    for path in candidates:
        if path.is_symlink() or path.resolve().parent != source or not path.is_file():
            raise ValueError(f"Graph must be an ordinary file inside source: {path.name}")
        content = path.read_bytes()
        graph = validate_graph(content, path.name)
        smds = sum(len(item.get("smd", [])) for item in graph["ic_library"])
        entry = {
            "path": f"graph/{path.name}", "bytes": len(content), "sha256": sha256(content),
            "components": len(graph["ic_position"]), "library_records": len(graph["ic_library"]),
            "dimension_records": len(graph["ic_dimension"]), "nets": len(graph["netlist"]), "smds": smds,
        }
        files.append(entry)
        contents.append((entry["path"], content))
    manifest = {"version": version, "files": files}
    summary = {
        "version": version, "graph_count": len(files), "total_bytes": sum(item["bytes"] for item in files),
        "position_records": sum(item["components"] for item in files),
        "library_records": sum(item["library_records"] for item in files),
        "dimension_records": sum(item["dimension_records"] for item in files),
        "net_records": sum(item["nets"] for item in files), "smd_records": sum(item["smds"] for item in files),
        "unique_top_level_keys": sorted(GRAPH_KEYS),
    }
    manifest_bytes, summary_bytes = encode_json(manifest), encode_json(summary)
    notice = (ROOT / "LICENSE_DATA.md").read_bytes()
    output.mkdir(parents=True, exist_ok=True)
    metadata.mkdir(parents=True, exist_ok=True)
    archive = output / f"omnilayout-graphs-{version}.zip"
    if archive.exists():
        raise FileExistsError(f"Refusing to replace an existing dataset release: {archive.name}")
    # Write exact allowlisted members; never copy a preexisting source archive.
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, content in contents + [("manifest.json", manifest_bytes), ("summary.json", summary_bytes), ("LICENSE_DATA.md", notice)]:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 26, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, content, compresslevel=9)
    archive_digest = sha256(archive.read_bytes())
    release = {
        "version": version, "archive": archive.name, "sha256": archive_digest,
        "bytes": archive.stat().st_size,
        "url": f"https://github.com/AAAAAiden/OmniLayout-Benchmark/releases/download/{version}/{archive.name}",
        "graph_count": len(files),
    }
    for name, content in [("manifest.json", manifest_bytes), ("summary.json", summary_bytes), ("release.json", encode_json(release))]:
        (metadata / name).write_bytes(content)
    (output / "SHA256SUMS.txt").write_text(f"{archive_digest}  {archive.name}\n", encoding="utf-8")
    return release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Directory containing processed ground-truth *_graph.json files")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--metadata", type=Path, default=ROOT / "data")
    parser.add_argument("--version", default=VERSION)
    args = parser.parse_args()
    print(json.dumps(package_dataset(args.source, args.output, args.metadata, args.version), indent=2))


if __name__ == "__main__":
    main()
