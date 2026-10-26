from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from placement_metrics import evaluate_placement  # noqa: E402


RAW_METRICS = (
    "hpwl",
    "net_crossings",
    "net_separation",
    "normalized_overlap",
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value must be an object")
    return data


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0.0:
        return None
    return numerator / denominator


def _numeric(values: Iterable[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def _stats(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = _numeric(row.get(field) for row in rows)
    if not values:
        return {
            f"{field}_count": 0,
            f"{field}_mean": None,
            f"{field}_median": None,
            f"{field}_sum": None,
        }
    return {
        f"{field}_count": len(values),
        f"{field}_mean": statistics.fmean(values),
        f"{field}_median": statistics.median(values),
        f"{field}_sum": sum(values),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok_rows = [row for row in rows if row.get("status") == "ok"]
    aggregate: dict[str, Any] = {
        "predictions": len(rows),
        "ok": len(ok_rows),
        "failed": len(rows) - len(ok_rows),
        "unique_boards": len({row.get("board") for row in rows}),
    }
    fields: list[str] = []
    for metric in RAW_METRICS:
        fields.extend(
            (
                f"{metric}_pred",
                f"{metric}_gt",
                f"{metric}_delta",
                f"{metric}_ratio",
            )
        )
    fields.append("runtime_seconds")
    for field in fields:
        aggregate.update(_stats(ok_rows, field))
    return aggregate


def _discover_runs(prediction_root: Path) -> list[tuple[str, str, Path]]:
    runs: list[tuple[str, str, Path]] = []
    for provider_dir in sorted(path for path in prediction_root.iterdir() if path.is_dir()):
        if provider_dir.name.startswith("._"):
            continue
        for model_dir in sorted(path for path in provider_dir.iterdir() if path.is_dir()):
            if model_dir.name.startswith("._"):
                continue
            json_dir = model_dir / "json"
            if json_dir.is_dir():
                runs.append((provider_dir.name, model_dir.name, json_dir))
    return runs


def _evaluate_one(
    provider: str,
    model: str,
    prediction_path: Path,
    gt_root: Path,
    gt_cache: dict[Path, tuple[dict[str, Any], dict[str, float]]],
) -> dict[str, Any]:
    gt_path = gt_root / prediction_path.name
    row: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "board": prediction_path.name.removesuffix("_graph.json"),
        "prediction": str(prediction_path),
        "gt_graph": str(gt_path),
        "status": "error",
        "error": "",
    }
    try:
        prediction = _load_json(prediction_path)
        if not gt_path.is_file():
            raise FileNotFoundError(f"missing GT graph: {gt_path}")

        if gt_path not in gt_cache:
            gt_graph = _load_json(gt_path)
            from omnilayout import validate_graph
            validate_graph(gt_graph)
            gt_source = {"ic_position": gt_graph.get("ic_position", [])}
            gt_cache[gt_path] = (
                gt_graph,
                evaluate_placement(gt_graph, gt_source),
            )
        gt_graph, gt_metrics = gt_cache[gt_path]

        pred_source = {"ic_position": prediction.get("ic_position", [])}
        if not pred_source["ic_position"]:
            raise ValueError("prediction has no ic_position entries")

        from omnilayout import validate_placement
        validate_placement(gt_graph, pred_source)
        pred_metrics = evaluate_placement(gt_graph, pred_source)
        row["pred_component_count"] = len(pred_source["ic_position"])
        row["gt_component_count"] = len(gt_graph.get("ic_position", []))
        for metric in RAW_METRICS:
            pred_value = float(pred_metrics[metric])
            gt_value = float(gt_metrics[metric])
            row[f"{metric}_pred"] = pred_value
            row[f"{metric}_gt"] = gt_value
            row[f"{metric}_delta"] = pred_value - gt_value
            row[f"{metric}_ratio"] = _safe_ratio(pred_value, gt_value)

        duration = prediction.get("duration_seconds")
        row["runtime_seconds"] = float(duration) if duration is not None else None
        row["status"] = "ok"
    except Exception as exc:  # preserve the rest of a batch when one board fails
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate selected agentic PCB placement outputs against GT graphs."
    )
    parser.add_argument(
        "--prediction-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--gt-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    from omnilayout import _require_geometry
    _require_geometry()
    args = parse_args()
    prediction_root = args.prediction_root.resolve()
    gt_root = args.gt_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    runs = _discover_runs(prediction_root)
    rows: list[dict[str, Any]] = []
    gt_cache: dict[Path, tuple[dict[str, Any], dict[str, float]]] = {}
    total = sum(
        1
        for _, _, json_dir in runs
        for path in json_dir.glob("*.json")
        if not path.name.startswith("._")
    )
    completed = 0
    for provider, model, json_dir in runs:
        prediction_paths = sorted(
            path for path in json_dir.glob("*.json") if not path.name.startswith("._")
        )
        for prediction_path in prediction_paths:
            rows.append(
                _evaluate_one(provider, model, prediction_path, gt_root, gt_cache)
            )
            completed += 1
            if completed % 25 == 0 or completed == total:
                print(f"[{completed}/{total}] evaluated", flush=True)

    per_model: dict[str, Any] = {}
    for provider, model, _ in runs:
        model_rows = [
            row
            for row in rows
            if row.get("provider") == provider and row.get("model") == model
        ]
        per_model[f"{provider}/{model}"] = _aggregate(model_rows)

    payload = {
        "evaluation": "agentic_layout_selected_50",
        "prediction_root": str(prediction_root),
        "gt_root": str(gt_root),
        "metric_note": (
            "net_separation_pred and net_separation_gt are raw sums of pairwise "
            "net-bounding-box intersection areas in square millimetres; they are not normalized."
        ),
        "overall": _aggregate(rows),
        "per_model": per_model,
        "rows": rows,
    }

    rows_path = output_dir / "agentic_layout_eval_rows.csv"
    summary_path = output_dir / "agentic_layout_eval_summary.json"
    model_summary_path = output_dir / "agentic_layout_eval_model_summary.csv"
    _write_csv(rows_path, rows)
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    model_rows = [
        {"run": run, **aggregate} for run, aggregate in per_model.items()
    ]
    _write_csv(model_summary_path, model_rows)

    print(f"rows: {rows_path}")
    print(f"summary: {summary_path}")
    print(f"model summary: {model_summary_path}")
    print(json.dumps(payload["overall"], indent=2))
    return 0 if payload["overall"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
