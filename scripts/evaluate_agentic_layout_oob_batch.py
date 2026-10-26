#!/usr/bin/env python3
"""Batch-evaluate component out-of-boundary ratio for layout predictions."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval_code.evaluate_pred_gt_out_of_boundary import compute_out_of_boundary  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    return value


def discover(root: Path) -> list[tuple[str, str, Path]]:
    runs: list[tuple[str, str, Path]] = []
    for provider_dir in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("._")):
        for model_dir in sorted(path for path in provider_dir.iterdir() if path.is_dir() and not path.name.startswith("._")):
            json_dir = model_dir / "json"
            if not json_dir.is_dir():
                continue
            for path in sorted(json_dir.glob("*_graph.json")):
                if not path.name.startswith("._"):
                    runs.append((provider_dir.name, model_dir.name, path))
    return runs


def evaluate_one(task: tuple[str, str, str, str]) -> dict[str, Any]:
    provider, model, prediction_text, gt_root_text = task
    prediction_path = Path(prediction_text)
    gt_path = Path(gt_root_text) / prediction_path.name
    row: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "board": prediction_path.name.removesuffix("_graph.json"),
        "prediction": prediction_text,
        "gt_graph": str(gt_path),
        "status": "error",
        "error": "",
    }
    try:
        prediction = load_json(prediction_path)
        gt_graph = load_json(gt_path)
        pred_source = {"ic_position": prediction.get("ic_position", [])}
        if not pred_source["ic_position"]:
            raise ValueError("prediction has no ic_position entries")
        from omnilayout import validate_graph, validate_placement
        validate_graph(gt_graph)
        validate_placement(gt_graph, pred_source)
        result = compute_out_of_boundary(gt_graph, pred_source)
        row.update(
            {
                "oob_ratio": float(result["ratio"]),
                "oob_count": int(result["oob_count"]),
                "component_count": int(result["total_count"]),
                "status": "ok",
            }
        )
    except Exception as error:
        row["error"] = f"{type(error).__name__}: {error}"
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction-root", type=Path, required=True)
    parser.add_argument("--gt-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    return parser.parse_args()


def main() -> int:
    from omnilayout import _require_geometry
    _require_geometry()
    args = parse_args()
    prediction_root = args.prediction_root.resolve()
    gt_root = args.gt_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    discovered = discover(prediction_root)
    tasks = [(provider, model, str(path), str(gt_root)) for provider, model, path in discovered]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(evaluate_one, task) for task in tasks]
        for index, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if index % 25 == 0 or index == len(futures):
                print(f"[{index}/{len(futures)}] evaluated", flush=True)
    rows.sort(key=lambda row: (str(row["provider"]), str(row["model"]), str(row["board"])))
    write_csv(output_dir / "agentic_layout_oob_rows.csv", rows)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["provider"]), str(row["model"]))].append(row)
    summaries: list[dict[str, Any]] = []
    for (provider, model), group in sorted(grouped.items()):
        ok = [row for row in group if row["status"] == "ok"]
        values = [float(row["oob_ratio"]) for row in ok]
        summaries.append(
            {
                "run": f"{provider}/{model}",
                "predictions": len(group),
                "ok": len(ok),
                "failed": len(group) - len(ok),
                "oob_ratio_mean": statistics.fmean(values) if values else None,
                "oob_ratio_median": statistics.median(values) if values else None,
            }
        )
    write_csv(output_dir / "agentic_layout_oob_model_summary.csv", summaries)
    print(f"wrote {len(rows)} rows and {len(summaries)} summaries to {output_dir}")
    return 1 if any(row["status"] != "ok" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
