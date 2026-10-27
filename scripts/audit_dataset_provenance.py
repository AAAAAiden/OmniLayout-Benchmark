"""Inventory local evidence without copying private EDA designs or granting rights.

Run with --source-root pointing to the private working copy. Only hashes,
relative evidence paths, standardized notice signals, and public URLs are saved.
Filename matches and embedded library notices never clear a graph for release.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tarfile
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
URL_RE = re.compile(r"https?://[^\s<>\"'\\]+", re.IGNORECASE)
BRANDS = {
    "adafruit": r"adafruit|featherwing|feather wing|hallowing|qt py|trinket",
    "sparkfun": r"sparkfun|qwiic",
    "seeed": r"seeed|grove|recomputer",
    "protocentral": r"protocentral",
    "arduino": r"arduino",
    "olimex": r"olimex",
    "wemos": r"wemos|lolin",
    "espressif": r"espressif",
}
LICENSE_PATTERNS = {
    "CC-BY-SA-4.0": r"creativecommons\.org/licenses/by-sa/4\.0|\bcc\s*by[ -]sa\s*4\.0\b|attribution[ -]share[ -]?alike\s*4\.0",
    "CC-BY-SA-3.0": r"creativecommons\.org/licenses/by-sa/3\.0|\bcc\s*by[ -]sa\s*3\.0\b|attribution[ -]share[ -]?alike\s*3\.0",
    "CC-BY-SA-unspecified": r"creative commons.*?share[ -]?alike|\bcc[ -]by[ -]sa\b",
    "CC-BY-4.0": r"creativecommons\.org/licenses/by/4\.0|\bcc\s*by\s*4\.0\b",
    "CC-BY-3.0": r"creativecommons\.org/licenses/by/3\.0|\bcc\s*by\s*3\.0\b",
    "CC-BY-NC-SA-4.0": r"creativecommons\.org/licenses/by-nc-sa/4\.0|\bcc\s*by[ -]nc[ -]sa\s*4\.0\b",
    "CC0-1.0": r"creativecommons\.org/publicdomain/zero/1\.0|\bcc0\b",
    "MIT": r"permission is hereby granted,? free of charge|\bmit license\b",
    "GPL-unspecified": r"gnu (?:general public license|gpl)|\bgpl[ -]?[23](?:\.0)?\b",
    "CERN-OHL-unspecified": r"cern.*?open hardware licen[sc]e|cern[ -]ohl",
    "TAPR-OHL": r"tapr.*?open hardware licen[sc]e",
    "Creative-Commons-unspecified": r"creative commons",
}
SKIP_DIRS = {".git", ".venv", "node_modules", "__MACOSX", "__pycache__"}


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_lineage_value(value: str) -> bool:
    """Recognize Windows and POSIX absolute paths regardless of the audit OS."""
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    return not windows.drive and not windows.is_absolute() and not posix.is_absolute() and ".." not in posix.parts


def public_urls(text: str) -> list[str]:
    """Drop credentials, query strings, fragments, and local/private endpoints."""
    found = set()
    for candidate in URL_RE.findall(html.unescape(text)):
        candidate = candidate.rstrip(".,;:)]}")
        try:
            parts = urlsplit(candidate)
            host = (parts.hostname or "").lower()
            if parts.username or parts.password or "." not in host:
                continue
            if host in {"localhost", "127.0.0.1", "0.0.0.0"} or re.fullmatch(r"[\d.]+", host) or host.endswith((".local", ".internal", ".localhost", ".lan", ".test", ".invalid")):
                continue
            if any(word in candidate.lower() for word in ("api_key", "access_token", "secret=", "token=")):
                continue
            found.add(urlunsplit((parts.scheme.lower(), host, parts.path, "", "")))
        except ValueError:
            continue
    return sorted(found)


def license_signals(text: str) -> list[str]:
    normalized = html.unescape(text)
    found = {name for name, pattern in LICENSE_PATTERNS.items() if re.search(pattern, normalized, re.I | re.S)}
    if found - {"Creative-Commons-unspecified"}:
        found.discard("Creative-Commons-unspecified")
    if any(name.startswith("CC-BY-SA-") and name != "CC-BY-SA-unspecified" for name in found):
        found.discard("CC-BY-SA-unspecified")
    return sorted(found)


def inspect_xml(content: bytes) -> dict:
    try:
        tree = ET.fromstring(content)
    except (ET.ParseError, ValueError):
        return {"parse_status": "not_parseable_eagle_xml", "notice_signals": [], "public_urls": []}
    signals = set()
    urls: dict[str, set[str]] = defaultdict(set)

    def walk(node: ET.Element, ancestors: tuple[str, ...]) -> None:
        tag = node.tag.rsplit("}", 1)[-1]
        chain = ancestors + (tag,)
        # Descriptions and texts inside package/device/library definitions do not
        # establish the licence of the board or its graph representation.
        embedded = any(part in {"libraries", "library", "packages", "package", "symbols", "symbol", "devicesets", "deviceset"} for part in chain)
        scope = "embedded_library" if embedded else "board_or_schematic"
        if tag in {"description", "text", "attribute", "note"}:
            text = " ".join(node.itertext()) + " " + " ".join(node.attrib.values())
            for signal in license_signals(text):
                signals.add((scope, signal))
            for url in public_urls(text):
                urls[url].add(scope)
        for child in node:
            walk(child, chain)

    walk(tree, ())
    return {
        "parse_status": "parsed_eagle_xml" if tree.tag == "eagle" else "parsed_other_xml",
        "notice_signals": [{"scope": scope, "license_id_signal": signal, "verification": "unverified_text_signal"} for scope, signal in sorted(signals)],
        "public_urls": [{"url": url, "scopes": sorted(scopes), "verification": "embedded_url_only"} for url, scopes in sorted(urls.items())],
    }


def iter_source_files(source_root: Path):
    datasets = source_root / "datasets"
    if not datasets.is_dir():
        raise ValueError("Private source root must contain datasets/")
    for path in datasets.rglob("*"):
        relative = path.relative_to(source_root)
        if any(part in SKIP_DIRS or part.startswith("._") for part in relative.parts):
            continue
        if path.is_file() and not path.is_symlink() and (path.suffix.lower() in {".brd", ".sch"} or path.name == "sources.txt"):
            yield path


def inventory_archives(source_root: Path) -> list[dict]:
    candidates = [p for p in source_root.iterdir() if p.is_file()]
    candidates.extend(p for p in (source_root / "datasets").rglob("*") if p.is_file() and not any(s in SKIP_DIRS for s in p.parts))
    result = []
    for path in sorted(candidates):
        suffixes = "".join(path.suffixes).lower()
        if not any(suffixes.endswith(s) for s in (".tar", ".tar.gz", ".tgz", ".zip", ".zip.partial")):
            continue
        entry = {"path": path.relative_to(source_root).as_posix(), "bytes": path.stat().st_size}
        counts = Counter()
        metadata = []
        try:
            if suffixes.endswith(".zip.partial"):
                entry["scan_status"] = "incomplete_archive_not_opened"
                result.append(entry)
                continue
            if suffixes.endswith(".zip"):
                with zipfile.ZipFile(path) as bundle:
                    names = bundle.namelist()
            else:
                with tarfile.open(path) as bundle:
                    names = [member.name for member in bundle]
            for name in names:
                safe = PurePosixPath(name.replace("\\", "/"))
                if safe.is_absolute() or re.match(r"^[A-Za-z]:", safe.as_posix()) or ".." in safe.parts or any(s in SKIP_DIRS for s in safe.parts):
                    continue
                suffix = safe.suffix.lower()
                if suffix in {".brd", ".sch"}:
                    counts[suffix.removeprefix(".")] += 1
                elif suffix in {".py", ".ipynb"}:
                    counts["code_or_notebook"] += 1
                elif suffix in {".csv", ".tsv"}:
                    counts["table"] += 1
                elif suffix == ".json":
                    counts["json"] += 1
                if safe.name == "sources.txt" or re.search(r"(^|[/_-])(license|copying|source|manifest|provenance|readme)", safe.as_posix(), re.I):
                    if len(metadata) < 60:
                        metadata.append(safe.as_posix())
                    counts["possible_provenance_or_license_metadata"] += 1
            entry.update(scan_status="indexed_members_only", member_count=len(names), member_type_counts=dict(sorted(counts.items())), candidate_metadata_members=sorted(metadata))
        except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
            entry["scan_status"] = "cannot_index_archive"
            entry["error_type"] = type(error).__name__
        result.append(entry)
    return result


def load_external_evidence(metadata_dir: Path, graph_ids: set[str]) -> tuple[list[dict], dict[str, list[dict]]]:
    inventories = []
    by_graph: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(metadata_dir.glob("license_evidence_*.json")):
        content = path.read_bytes()
        evidence = json.loads(content)
        entries = evidence.get("entries", []) if isinstance(evidence, dict) else evidence
        if not isinstance(entries, list):
            raise ValueError(f"External evidence entries must be a list: {path.name}")
        inventories.append({"path": f"data/{path.name}", "sha256": digest(content), "entry_count": len(entries), "integration": "references_only_no_automatic_rights_clearance"})
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            ids = entry.get("graph_ids", [entry.get("graph_id")])
            for graph_id in ids:
                if graph_id in graph_ids:
                    by_graph[graph_id].append({"path": f"data/{path.name}", "entry_index": index, "match_status": entry.get("match_status", "unspecified"), "license_id": entry.get("license_id"), "local_board_sha256": entry.get("local_board_sha256") or entry.get("local_raw_board_sha256"), "verification": "external_evidence_requires_review"})
    return inventories, by_graph


def external_references(references: list[dict], local_hashes: list[str]) -> list[dict]:
    return [
        {**reference, "local_hash_matches_inventory": reference["local_board_sha256"] in local_hashes if reference.get("local_board_sha256") else None}
        for reference in references
    ]


def external_counts(records: list[dict]) -> dict:
    return {
        "graphs_with_external_evidence": sum(bool(record["external_evidence"]) for record in records),
        "graphs_with_verified_exact_upstream_source_evidence": sum(any(evidence["match_status"] == "verified_exact_source" and evidence.get("local_hash_matches_inventory") for evidence in record["external_evidence"]) for record in records),
        "external_exact_source_claims_with_mismatching_local_hash": sum(evidence["match_status"] == "verified_exact_source" and evidence.get("local_hash_matches_inventory") is False for record in records for evidence in record["external_evidence"]),
    }


def audit(source_root: Path, manifest_path: Path, metadata_dir: Path) -> tuple[dict, dict]:
    source_root = source_root.resolve(strict=True)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    graphs = manifest["files"]
    graph_ids = {PurePosixPath(entry["path"]).name.removesuffix("_graph.json") for entry in graphs}
    raw_index: dict[str, dict] = {}
    lineage_index: dict[str, dict] = {}
    candidates: dict[str, set[str]] = defaultdict(set)
    lineage_by_graph: dict[str, set[str]] = defaultdict(set)
    parsed_cache: dict[str, dict] = {}
    files = sorted(iter_source_files(source_root))
    print(f"Inventorying {len(files)} raw source/lineage files for {len(graph_ids)} graphs", flush=True)
    for number, path in enumerate(files, 1):
        relative = path.relative_to(source_root).as_posix()
        if path.name != "sources.txt" and path.stem not in graph_ids:
            continue
        content = path.read_bytes()
        sha = digest(content)
        if path.name == "sources.txt":
            fields = {}
            for line in content.decode("utf-8", errors="replace").splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    fields[key] = value
            graph_id = fields.get("output_base", path.parent.name)
            if graph_id not in graph_ids:
                continue
            if sha not in lineage_index:
                # Original absolute paths are deliberately omitted.
                safe_fields = {key: fields[key] for key in ("source_group", "source_pair_dir", "output_base") if key in fields and safe_lineage_value(fields[key])}
                for key in ("brd_source", "sch_source"):
                    if key in fields:
                        safe_fields[key] = PurePosixPath(fields[key].replace("\\", "/")).name
                lineage_index[sha] = {"sha256": sha, "paths": [], "safe_lineage_fields": safe_fields, "public_urls": public_urls(content.decode("utf-8", errors="replace")), "verification": "local_processing_lineage_only"}
            lineage_index[sha]["paths"].append(relative)
            lineage_by_graph[graph_id].add(sha)
        else:
            if sha not in raw_index:
                parsed_cache[sha] = inspect_xml(content)
                raw_index[sha] = {"sha256": sha, "kind": path.suffix.lower().removeprefix("."), "bytes": len(content), "paths": [], **parsed_cache[sha]}
            raw_index[sha]["paths"].append(relative)
            candidates[path.stem].add(sha)
        if number % 1000 == 0:
            print(f"Inspected {number}/{len(files)} local source files", flush=True)
    archives = inventory_archives(source_root)
    external_files, external_by_graph = load_external_evidence(metadata_dir, graph_ids)
    records = []
    for graph in graphs:
        graph_id = PurePosixPath(graph["path"]).name.removesuffix("_graph.json")
        sources = [raw_index[sha] for sha in sorted(candidates[graph_id])]
        board_hashes = [item["sha256"] for item in sources if item["kind"] == "brd"]
        scope_counts = Counter(signal["scope"] for item in sources for signal in item["notice_signals"])
        record = {
            "graph_id": graph_id,
            "graph_path": graph["path"],
            "graph_sha256": graph["sha256"],
            "local_match_status": "exact_filename_candidates" if sources else "no_local_source_found",
            "raw_source_sha256": [item["sha256"] for item in sources],
            "board_revision_status": "multiple_distinct_local_candidates" if len(board_hashes) > 1 else "one_local_board_candidate" if board_hashes else "no_local_board_candidate",
            "lineage_sha256": sorted(lineage_by_graph[graph_id]),
            "brand_hints": [{"brand": name, "basis": "graph_filename_only", "verification": "unverified_hint_not_license"} for name, pattern in BRANDS.items() if re.search(pattern, graph_id, re.I)],
            "board_or_schematic_notice_signal_count": scope_counts["board_or_schematic"],
            "embedded_library_notice_signal_count": scope_counts["embedded_library"],
            "external_evidence": external_references(external_by_graph.get(graph_id, []), [item["sha256"] for item in sources]),
            "rights_status": "unverified_requires_upstream_review",
            "dataset_license": None,
            "release_clearance": False,
        }
        records.append(record)
    summary = {
        "schema_version": 1,
        "graph_count": len(records),
        "graphs_with_exact_local_board_candidate": sum(any(raw_index[sha]["kind"] == "brd" for sha in record["raw_source_sha256"]) for record in records),
        "graphs_with_exact_local_schematic_candidate": sum(any(raw_index[sha]["kind"] == "sch" for sha in record["raw_source_sha256"]) for record in records),
        "graphs_with_multiple_distinct_local_board_candidates": sum(record["board_revision_status"] == "multiple_distinct_local_candidates" for record in records),
        "graphs_with_lineage_metadata": sum(bool(record["lineage_sha256"]) for record in records),
        "graphs_with_board_or_schematic_notice_signals": sum(record["board_or_schematic_notice_signal_count"] > 0 for record in records),
        "graphs_with_only_embedded_library_notice_signals": sum(record["embedded_library_notice_signal_count"] > 0 and not record["board_or_schematic_notice_signal_count"] for record in records),
        **external_counts(records),
        "unique_local_raw_source_hashes": len(raw_index),
        "local_raw_source_file_copies": sum(len(entry["paths"]) for entry in raw_index.values()),
        "unique_lineage_metadata_hashes": len(lineage_index),
        "external_evidence_files": external_files,
        "archives_indexed": len(archives),
        "rights_status_counts": dict(Counter(record["rights_status"] for record in records)),
        "graphs_cleared_for_redistribution": 0,
        "brand_hint_counts_not_license_assignments": dict(sorted(Counter(hint["brand"] for record in records for hint in record["brand_hints"]).items())),
        "limitations": [
            "Raw files were matched by exact filename, not by rerunning conversion and reproducing the published graph.",
            "Embedded notice signals and brand hints are leads only; they do not establish licensing of a design or graph.",
            "Library notices are classified separately from board/schematic notices and cannot clear the design.",
            "External evidence is referenced for review; this script never grants release clearance automatically.",
            "Archive members were indexed without extracting raw designs; their contents were not used for clearance.",
            "Private absolute paths and original raw design contents are omitted.",
        ],
    }
    provenance = {
        "schema_version": 1,
        "dataset_version": manifest.get("version"),
        "manifest_sha256": digest(manifest_bytes),
        "method": "local exact-filename candidates; SHA256 deduplication; XML scoped notice/URL signals; lineage metadata; archive member inventory; external evidence references",
        "clearance_policy": "No automatic licensing or redistribution clearance; review verified upstream terms and exact source matches per graph.",
        "graphs": records,
        "local_source_evidence": dict(sorted(raw_index.items())),
        "lineage_evidence": dict(sorted(lineage_index.items())),
        "source_archive_inventory": archives,
        "external_evidence_files": external_files,
    }
    return provenance, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, help="Private working copy containing datasets/")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data" / "manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data")
    parser.add_argument("--refresh-external-evidence", action="store_true", help="Refresh external evidence references in an existing audit without rereading private designs")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.refresh_external_evidence:
        provenance = json.loads((args.output / "provenance.json").read_text(encoding="utf-8"))
        summary = json.loads((args.output / "provenance_summary.json").read_text(encoding="utf-8"))
        graph_ids = {record["graph_id"] for record in provenance["graphs"]}
        external_files, external_by_graph = load_external_evidence(args.output, graph_ids)
        for record in provenance["graphs"]:
            record["external_evidence"] = external_references(external_by_graph.get(record["graph_id"], []), record["raw_source_sha256"])
        provenance["external_evidence_files"] = external_files
        summary["external_evidence_files"] = external_files
        summary.update(external_counts(provenance["graphs"]))
    else:
        if args.source_root is None:
            parser.error("--source-root is required unless --refresh-external-evidence is used")
        provenance, summary = audit(args.source_root, args.manifest, args.output)
    for name, value in (("provenance.json", provenance), ("provenance_summary.json", summary)):
        (args.output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
