#!/usr/bin/env python3
"""Portable entry points for the released OmniLayout graph benchmark."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def validate_graph(graph: dict[str, Any]) -> None:
    """Reject legacy or incomplete graph structures before running a command."""
    for field in ("netlist", "ic_library", "ic_position", "ic_dimension"):
        if not isinstance(graph.get(field), list):
            raise ValueError(f"Graph requires a {field} list (current graph schema)")
    if not isinstance(graph.get("board_boundary"), dict):
        raise ValueError("Graph requires a board_boundary object")
    boundary = graph["board_boundary"]
    if not (boundary.get("dxf_details") or boundary.get("segment_details")):
        raise ValueError("Graph boundary contains no outline segments")
    if not graph["ic_position"]:
        raise ValueError("Graph contains no ground-truth component positions")
    validate_placement(graph, graph)


def validate_placement(graph: dict[str, Any], prediction: dict[str, Any]) -> None:
    """Require each ground-truth component exactly once with finite coordinates."""
    expected = {item["element_name"] for item in graph["ic_position"]}
    rows = prediction.get("ic_position", prediction.get("placements"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Prediction requires a nonempty ic_position or placements list")
    names = []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Each placement must be an object")
        name = item.get("element_name") or item.get("name")
        if not name:
            raise ValueError("Each placement requires a component name")
        names.append(name)
        position = [item.get("x"), item.get("y")] if "x" in item and "y" in item else item.get("position")
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            raise ValueError(f"Component {name} requires two coordinates")
        if any(isinstance(value, bool) or not math.isfinite(float(value)) for value in position):
            raise ValueError(f"Component {name} has invalid coordinates")
        rotation = str(item.get("rotation") or item.get("rot") or "R0")
        if not re.fullmatch(r"S?M?R[+-]?(?:\d+(?:\.\d*)?|\.\d+)", rotation):
            raise ValueError(f"Component {name} has an invalid rotation code")
    if len(names) != len(set(names)):
        raise ValueError("Prediction contains duplicate component names")
    missing, extra = expected - set(names), set(names) - expected
    if missing or extra:
        raise ValueError(f"Component names differ: missing={sorted(missing)}, unexpected={sorted(extra)}")


def _require_geometry() -> None:
    try:
        import shapely  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Install base dependencies first: python -m pip install -r requirements.txt") from exc


def evaluate_graph(graph: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one complete prediction using the released metric implementations."""
    _require_geometry()
    validate_graph(graph)
    validate_placement(graph, prediction)
    from placement_metrics import evaluate_placement
    from eval_code.evaluate_pred_gt_out_of_boundary import compute_out_of_boundary

    def metrics(source: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        values = evaluate_placement(graph, source, result_data=result)
        oob = compute_out_of_boundary(graph, source)
        values["out_of_boundary"] = {
            "ratio": oob["ratio"],
            "count": oob["oob_count"],
            "total": oob["total_count"],
        }
        return values

    return {
        "prediction": metrics(prediction, prediction),
        "ground_truth": metrics(graph, graph),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preprocess = subparsers.add_parser("preprocess", help="Convert locally held EAGLE XML .brd files to graph JSON")
    preprocess.add_argument("input", type=Path, help="Private .brd file or directory; raw boards are never included in a release")
    preprocess.add_argument("--output-dir", type=Path, required=True)
    preprocess.add_argument("--draw-img", action="store_true")

    render = subparsers.add_parser("render", help="Render one graph or predicted graph JSON")
    render.add_argument("graph", type=Path)
    render.add_argument("--output-dir", type=Path, required=True)

    place = subparsers.add_parser("place", help="Generate placements with a configured LLM provider")
    place.add_argument("graph", type=Path)
    place.add_argument("--provider", choices=("gemini", "openrouter"), required=True)
    place.add_argument("--model", required=True, help="Provider model identifier; this command makes a paid API request")
    place.add_argument("--output-dir", type=Path, required=True)
    place.add_argument("--verbose", action="store_true")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate a prediction against a ground-truth graph")
    evaluate.add_argument("graph", type=Path)
    evaluate.add_argument("--prediction", type=Path, help="Defaults to the graph's own placement for a sanity check")
    evaluate.add_argument("--output", type=Path, help="Optional JSON report path; metrics are also printed")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "preprocess":
            from EagleXML_to_Brd_0515_2 import main as preprocess_board

            inputs = sorted(path for path in args.input.iterdir() if path.is_file() and path.suffix.lower() == ".brd") if args.input.is_dir() else [args.input]
            if not inputs:
                raise ValueError(f"No .brd files found in {args.input}")
            for path in inputs:
                if not path.is_file() or path.suffix.lower() != ".brd":
                    raise ValueError(f"Expected an existing EAGLE XML .brd file: {path}")
                preprocess_board(str(path), str(args.output_dir), draw_img=args.draw_img)
        elif args.command == "render":
            from placement_to_img0517 import main as render_graph

            render_graph(str(args.graph), str(args.output_dir))
        elif args.command == "place":
            graph = load_json(args.graph)
            validate_graph(graph)
            if args.provider == "gemini":
                from llm_placer_gemini_0515 import run
            else:
                from llm_placer_openrouter_0515 import run
            prediction = run(str(args.graph), str(args.output_dir), model=args.model, verbose=args.verbose)
            validate_placement(graph, prediction)
            print(f"Saved placements to {args.output_dir / 'json'}")
        elif args.command == "evaluate":
            graph = load_json(args.graph)
            prediction = load_json(args.prediction) if args.prediction else graph
            payload = evaluate_graph(graph, prediction)
            text = json.dumps(payload, indent=2, allow_nan=False)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(text + "\n", encoding="utf-8")
            print(text)
    except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
