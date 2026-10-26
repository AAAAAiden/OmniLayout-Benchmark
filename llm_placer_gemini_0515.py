#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemini wrapper for llm_placer-style PCB placement prompt."""

import json
import os
import re
import sys
import time
import io
import contextlib
from datetime import datetime
from pathlib import Path

from placement_to_img0517 import main as render_placement_to_img


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "placements": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "x": {"type": "NUMBER"},
                    "y": {"type": "NUMBER"},
                    "rotation": {
                        "type": "STRING",
                        "enum": ["R0", "R180", "R270", "MR0", "MR180", "MR270"],
                    },
                },
                "required": ["name", "x", "y", "rotation"],
            },
        }
    },
    "required": ["placements"],
}


def _get_board_boundary_bbox(board_boundary: dict):
    bb = board_boundary.get("bounding_box")
    if isinstance(bb, dict) and all(k in bb for k in ("x_min", "y_min", "x_max", "y_max")):
        return bb["x_min"], bb["y_min"], bb["x_max"], bb["y_max"], bb.get("width"), bb.get("height")

    segments = board_boundary.get("segment_details", [])
    xs = []
    ys = []
    for seg in segments:
        start = seg.get("start")
        end = seg.get("end")
        if start and end:
            xs.extend([float(start[0]), float(end[0])])
            ys.extend([float(start[1]), float(end[1])])
    if not xs or not ys:
        raise ValueError("board_boundary must contain either bounding_box or segment_details points")
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    return x_min, y_min, x_max, y_max, x_max - x_min, y_max - y_min


def _summarize_dxf_details(dxf_details: list) -> str:
    type_counts = {}
    for prim in dxf_details:
        typ = prim.get("type", "UNKNOWN")
        type_counts[typ] = type_counts.get(typ, 0) + 1
    parts = [f"{cnt} {typ}" for typ, cnt in sorted(type_counts.items())]
    return f"{len(dxf_details)} primitives ({', '.join(parts)})" if dxf_details else "no DXF primitives"


def _format_block_dimensions(blocks: list) -> str:
    if not blocks:
        return "no blocks"
    parts = []
    for block in blocks:
        name = block.get("name", "?")
        kind = block.get("type", "?")
        layer = block.get("layer", "unknown")
        w = float(block.get("width", 0.0))
        l = float(block.get("length", 0.0))
        pos = block.get("position", [0.0, 0.0])
        parts.append(
            f"{name}:{kind} ({layer}) {w:.2f}×{l:.2f} mm at ({pos[0]:.2f},{pos[1]:.2f})"
        )
    return "; ".join(parts)


def _infer_initial_side(blocks: list) -> str:
    layers = [str(block.get("layer", "")).strip().lower() for block in blocks if block.get("layer") is not None]
    if any(layer == "through" for layer in layers):
        return "through"
    if layers and all(layer == "back" for layer in layers):
        return "back"
    if layers and all(layer == "front" for layer in layers):
        return "front"
    if any(layer in ("front", "back") for layer in layers):
        return "front/back"
    return "front"


def build_prompt(graph_data: dict) -> str:
    x0, y0, x1, y1, bw, bh = _get_board_boundary_bbox(graph_data["board_boundary"])
    dxf_summary = _summarize_dxf_details(graph_data["board_boundary"].get("dxf_details", []))

    board_desc = (
        f"Board boundary extents: ({x0:.2f}, {y0:.2f}) to ({x1:.2f}, {y1:.2f}), "
        f"size {bw:.2f} mm × {bh:.2f} mm."
    )
    dxf_desc = (
        f"Board outline is defined by board_boundary.dxf_details. "
        f"The boundary contains {dxf_summary}. "
        f"Use this outline to constrain all placements inside the board."
    )

    # Build dimension map for quick lookup
    dimension_map = {d["element_name"]: d["block_dimension"] for d in graph_data.get("ic_dimension", [])}

    ic_lines = []
    # Read only component names here; reference placement coordinates are withheld.
    for component in graph_data["ic_position"]:
        name = component["element_name"]
        blocks = dimension_map.get(name, [])

        block_desc = _format_block_dimensions(blocks) if blocks else "geometry unavailable; placement is still required"
        ic_lines.append(f"  - {name}: {block_desc}")

    ic_desc = "Components (name and block dimensions):\n" + "\n".join(ic_lines)

    net_lines = []
    for net in graph_data["netlist"]:
        members = ", ".join(f"{r['element']}:{r['pad']}" for r in net["contactref"])
        net_lines.append(f"  - Net '{net['name']}': {members}")
    net_desc = "Netlist (connections):\n" + "\n".join(net_lines)

    prompt = f"""You are an expert PCB layout engineer. Your task is to place all components on a PCB board optimally.

{board_desc}
{dxf_desc}

For each component:
- block_dimension is a list of blocks inside the component that cannot overlap with blocks from other components.
- Each block has: position (relative to component center), width, length, type (smd/pad/hole), and layer (front/back/through).
- Allowed rotation codes:
    - R0: front side, 0 degree
    - R180: front side, 180 degree
    - R270: front side, 270 degree
    - MR0: back side (mirrored), 0 degree
    - MR180: back side (mirrored), 180 degree
    - MR270: back side (mirrored), 270 degree

{ic_desc}

{net_desc}

PLACEMENT RULES:
1. Coordinates must be inside the board boundary ({x0:.2f} <= x <= {x1:.2f}, {y0:.2f} <= y <= {y1:.2f}).
2. **CRITICAL**: Block dimensions from different components must NOT overlap. Blocks within the same component (smd, pad, hole) are already non-overlapping by design.
3. Components themselves CAN occupy the same space (overlap their centers), but their block_dimensions must not overlap in 2D.
4. Choose placement side (R* vs MR*) to avoid block collisions while respecting layer constraints.

OUTPUT FORMAT (strictly follow this):
Return ONLY a valid JSON object, no explanation, no markdown fences.
The JSON must have this exact structure:
{{
  "placements": [
        {{"name": "COMPONENT_NAME", "x": X_COORDINATE, "y": Y_COORDINATE, "rotation": "R0|R180|R270|MR0|MR180|MR270"}},
    ...
  ]
}}

Every component listed in the Components section must appear exactly once in placements.
Coordinates are in millimeters. Place component CENTER at (x, y).
"""
    return prompt


def parse_llm_response(response_text: str) -> dict:
    text = re.sub(r"```(?:json)?\s*", "", response_text)
    text = re.sub(r"```", "", text)
    match = re.search(r"\{[\s\S]*\"placements\"[\s\S]*\}", text)
    if not match:
        raise ValueError(f"Could not find JSON in Gemini response:\n{response_text[:500]}")
    data = json.loads(match.group())
    if "placements" not in data:
        raise ValueError("JSON does not contain placements")
    return data


def call_gemini(prompt: str, model: str = "gemini-2.0-flash") -> str:
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    # Remove any surrounding quotes from the API key
    api_key = api_key.strip('"\'')
    
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Use: set GEMINI_API_KEY=your_key_here")
    
    if len(api_key) < 20:
        raise RuntimeError(f"GEMINI_API_KEY appears to be invalid (too short: {len(api_key)} chars)")

    client = genai.Client(api_key=api_key)
    contents = [
        {
            "role": "user",
            "parts": [{"text": prompt}],
        }
    ]
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config={
            "temperature": 0.0,
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
        },
    )
    return response.text or ""


def run(graph_json_path: str, output_folder: str, model: str = "gemini-2.0-flash", verbose: bool = False) -> dict:
    start_time = time.perf_counter()
    with open(graph_json_path, "r", encoding="utf-8") as f:
        graph_data = json.load(f)

    prompt = build_prompt(graph_data)
    if verbose:
        print(f"Prompt length: {len(prompt)} characters")

    response_text = call_gemini(prompt, model=model)
    if verbose:
        print("Gemini response text:")
        print(response_text)

    result = parse_llm_response(response_text)
    base = Path(graph_json_path).stem.replace("_graph", "")
    out_folder = Path(output_folder)
    
    # Create json and img subfolders
    json_folder = out_folder / "json"
    img_folder = out_folder / "img"
    json_folder.mkdir(parents=True, exist_ok=True)
    img_folder.mkdir(parents=True, exist_ok=True)

    # Convert LLM placements to ic_position format
    ic_position = [
        {
            "element_name": item["name"],
            "position": [float(item["x"]), float(item["y"])],
            "rotation": item["rotation"],
        }
        for item in result["placements"]
    ]

    # Build output graph data with LLM placement positions
    # Include only: board_boundary, ic_dimension, ic_position
    output_graph_data = {
        "board_boundary": graph_data.get("board_boundary", {}),
        "ic_dimension": graph_data.get("ic_dimension", []),
        "ic_position": ic_position,
    }

    from omnilayout import validate_placement
    validate_placement(graph_data, output_graph_data)

    output_graph_json_path = json_folder / f"{base}_graph.json"
    # Save before rendering so the renderer reads this prediction.
    with open(output_graph_json_path, "w", encoding="utf-8") as f:
        json.dump(output_graph_data, f, indent=2, ensure_ascii=False)
    # Call placement_to_img to render the placement
    try:
        # Suppress detailed renderer logs; batch runner prints only overall progress.
        with contextlib.redirect_stdout(io.StringIO()):
            render_placement_to_img(str(output_graph_json_path), str(img_folder))
    except Exception as e:
        if verbose:
            print(f"Warning: Failed to render image: {e}")

    elapsed = round(time.perf_counter() - start_time, 3)
    output_graph_data["duration_seconds"] = elapsed

    # Save output graph json to json folder after duration is available
    with open(output_graph_json_path, "w", encoding="utf-8") as f:
        json.dump(output_graph_data, f, indent=2, ensure_ascii=False)

    return output_graph_data


def run_batch(path: str, output_folder: str, model: str = "gemini-2.0-flash", overwrite: bool = True) -> list:
    """Process single file or all graph files in a directory."""
    path_obj = Path(path)
    out_folder = Path(output_folder)
    
    # Create json and img subfolders
    json_folder = out_folder / "json"
    img_folder = out_folder / "img"
    json_folder.mkdir(parents=True, exist_ok=True)
    img_folder.mkdir(parents=True, exist_ok=True)
    
    if path_obj.is_file():
        # Single file
        print(f"[1/1] {path_obj.name}")
        result = run(str(path_obj), str(out_folder), model=model, verbose=False)
        print(f"Summary | total: 1, success: 1, failed: 0, model: {model}")
        return [result]
    elif path_obj.is_dir():
        # Directory: find all *_graph.json files
        graph_files = sorted(path_obj.glob("*_graph.json"))
        if not graph_files:
            print(f"No *_graph.json files found in {path}")
            return []
        total_files = len(graph_files)

        # Reusable summary JSON saved under output folder parent.
        report_parent = out_folder.parent
        report_path = report_parent / f"{out_folder.name}_summary.json"

        previous_status_map = {}
        if not overwrite and report_path.exists():
            try:
                with open(report_path, "r", encoding="utf-8") as f:
                    previous_report = json.load(f)
                for item in previous_report.get("files", []):
                    name = item.get("graph_json")
                    if name:
                        previous_status_map[name] = item
            except Exception as e:
                print(f"Warning: Failed to read previous summary ({report_path}): {e}")

        results = []
        file_reports = []

        def _save_summary_snapshot():
            success_count_local = sum(1 for item in file_reports if item["status"] == "success")
            failed_count_local = sum(1 for item in file_reports if item["status"] == "failed")
            skipped_success_count_local = sum(
                1 for item in file_reports if item.get("status") == "success" and item.get("skipped", False)
            )
            executed_count_local = len(file_reports) - skipped_success_count_local

            batch_report_local = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "input_graph_folder": str(path_obj),
                "output_folder": str(out_folder),
                "model": model,
                "overwrite": overwrite,
                "summary": {
                    "total": total_files,
                    "success": success_count_local,
                    "failed": failed_count_local,
                    "executed": executed_count_local,
                    "skipped_previous_success": skipped_success_count_local,
                    "processed": len(file_reports),
                    "remaining": total_files - len(file_reports),
                },
                "files": file_reports,
            }

            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(batch_report_local, f, indent=2, ensure_ascii=False)

        for idx, graph_file in enumerate(graph_files, start=1):
            print(f"[{idx}/{total_files}] {graph_file.name}")
            prev_item = previous_status_map.get(graph_file.name)
            if not overwrite and prev_item and prev_item.get("status") == "success":
                file_reports.append(
                    {
                        "graph_json": graph_file.name,
                        "status": "success",
                        "duration": prev_item.get("duration", prev_item.get("duration_seconds", 0.0)),
                        "skipped": True,
                    }
                )
                _save_summary_snapshot()
                continue

            start_time = time.perf_counter()
            try:
                result = run(str(graph_file), str(out_folder), model=model, verbose=False)
                results.append(result)
                elapsed = time.perf_counter() - start_time
                file_reports.append(
                    {
                        "graph_json": graph_file.name,
                        "status": "success",
                        "duration": round(elapsed, 3),
                    }
                )
            except Exception as e:
                elapsed = time.perf_counter() - start_time
                file_reports.append(
                    {
                        "graph_json": graph_file.name,
                        "status": "failed",
                        "duration": round(elapsed, 3),
                        "error": str(e),
                    }
                )
            finally:
                _save_summary_snapshot()

        success_count = sum(1 for item in file_reports if item["status"] == "success")
        failed_count = total_files - success_count
        skipped_success_count = sum(
            1 for item in file_reports if item.get("status") == "success" and item.get("skipped", False)
        )
        executed_count = total_files - skipped_success_count

        batch_report = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "input_graph_folder": str(path_obj),
            "output_folder": str(out_folder),
            "model": model,
            "overwrite": overwrite,
            "summary": {
                "total": total_files,
                "success": success_count,
                "failed": failed_count,
                "executed": executed_count,
                "skipped_previous_success": skipped_success_count,
            },
            "files": file_reports,
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(batch_report, f, indent=2, ensure_ascii=False)

        print(
            f"Summary | total: {total_files}, success: {success_count}, "
            f"failed: {failed_count}, executed: {executed_count}, "
            f"skipped_success: {skipped_success_count}, model: {model}"
        )
        return results
    else:
        raise ValueError(f"Path does not exist or is not a file/directory: {path}")


def main(graph_json_path: str, output_folder: str, model: str = "gemini-2.0-flash", overwrite: bool = True):
    """Main entry point for LLM-based placement via Gemini.
    
    Args:
        graph_json_path: Path to graph JSON file or folder containing graph files
        output_folder: Output folder for placement JSON results
        model: Gemini model name (default: gemini-2.0-flash)
        overwrite: Whether to overwrite/re-run all cases in folder mode.
                   If False, read summary JSON and skip previous successes,
                   re-running only failed and never-run cases.
    """
    return run_batch(graph_json_path, output_folder, model=model, overwrite=overwrite)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Graph JSON file or directory of *_graph.json files")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model", required=True, help="Provider model identifier")
    parser.add_argument("--resume", action="store_true", help="Skip successful entries in a previous batch summary")
    args = parser.parse_args()
    main(args.input, args.output_dir, model=args.model, overwrite=not args.resume)
