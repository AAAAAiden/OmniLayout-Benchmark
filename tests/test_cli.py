"""Offline smoke tests; the synthetic geometry contains no source board data."""

from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def synthetic_graph() -> dict:
    points = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    segments = [
        {"type": "LINE", "start": points[index], "end": points[index + 1], "source_index": index}
        for index in range(4)
    ]
    block = {"name": "1", "type": "smd", "layer": "front", "position": [0, 0], "width": 1, "length": 1, "rotation": "R0"}
    return {
        "netlist": [{"name": "N1", "contactref": [{"element": "U1", "pad": "1"}, {"element": "U2", "pad": "1"}]}],
        "board_boundary": {
            "dxf_details": segments,
            "segment_details": copy.deepcopy(segments),
            "bounding_box": {"x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10, "width": 10, "height": 10},
        },
        "ic_library": [{"element_name": name, "smd": [{"name": "1", "pos": [0, 0]}]} for name in ("U1", "U2")],
        "ic_dimension": [{"element_name": name, "block_dimension": [copy.deepcopy(block)]} for name in ("U1", "U2")],
        "ic_position": [
            {"element_name": "U1", "position": [2, 2], "rotation": "R0"},
            {"element_name": "U2", "position": [8, 8], "rotation": "R0"},
        ],
    }


class CLISmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.graph = synthetic_graph()
        self.graph_path = self.work / "synthetic_graph.json"
        self.graph_path.write_text(json.dumps(self.graph), encoding="utf-8")

    def run_cli(self, *args: str, success: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"}
        result = subprocess.run(
            [sys.executable, str(ROOT / "omnilayout.py"), *map(str, args)],
            cwd=self.work, env=env, text=True, encoding="utf-8", capture_output=True,
            timeout=60,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_help_works_from_another_directory(self) -> None:
        self.assertIn("preprocess", self.run_cli("--help").stdout)
        self.assertIn("--provider", self.run_cli("place", "--help").stdout)

    def test_null_source_rotation_uses_the_existing_zero_rotation_convention(self) -> None:
        self.graph["ic_position"][0]["rotation"] = None
        self.graph_path.write_text(json.dumps(self.graph), encoding="utf-8")
        report = json.loads(self.run_cli("evaluate", self.graph_path).stdout)
        self.assertEqual(report["ground_truth"]["hpwl"], 12.0)

    def test_metrics_and_out_of_boundary_have_known_values(self) -> None:
        prediction = copy.deepcopy(self.graph)
        prediction["ic_position"][1]["position"] = [12, 8]
        prediction_path = self.work / "prediction.json"
        prediction_path.write_text(json.dumps(prediction), encoding="utf-8")
        report_path = self.work / "nested" / "metrics.json"
        self.run_cli("evaluate", self.graph_path, "--prediction", prediction_path, "--output", report_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["ground_truth"]["hpwl"], 12.0)
        self.assertEqual(report["prediction"]["hpwl"], 16.0)
        self.assertEqual(report["prediction"]["normalized_overlap"], 0.0)
        self.assertEqual(report["prediction"]["out_of_boundary"], {"ratio": 0.5, "count": 1, "total": 2})

    def test_incomplete_duplicate_and_nonfinite_predictions_are_rejected(self) -> None:
        for kind in ("missing", "duplicate", "nonfinite"):
            with self.subTest(kind=kind):
                prediction = copy.deepcopy(self.graph)
                if kind == "missing":
                    prediction["ic_position"].pop()
                elif kind == "duplicate":
                    prediction["ic_position"][1] = copy.deepcopy(prediction["ic_position"][0])
                else:
                    prediction["ic_position"][1]["position"][0] = float("nan")
                prediction_path = self.work / f"{kind}.json"
                prediction_path.write_text(json.dumps(prediction), encoding="utf-8")
                self.assertIn("Error:", self.run_cli("evaluate", self.graph_path, "--prediction", prediction_path, success=False).stderr)

    def test_renderer_creates_a_png(self) -> None:
        self.run_cli("render", self.graph_path, "--output-dir", self.work / "rendered")
        image = self.work / "rendered" / "synthetic_img.png"
        self.assertEqual(image.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_official_evaluation_requires_shapely(self) -> None:
        from omnilayout import _require_geometry
        with patch.dict(sys.modules, {"shapely": None}):
            with self.assertRaisesRegex(RuntimeError, "Install base dependencies"):
                _require_geometry()

    def test_batch_evaluation_matches_named_graphs(self) -> None:
        prediction_root = self.work / "predictions"
        json_dir = prediction_root / "synthetic_provider" / "synthetic_model" / "json"
        json_dir.mkdir(parents=True)
        (json_dir / self.graph_path.name).write_text(json.dumps(self.graph), encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"}
        for script in ("evaluate_agentic_layout_outputs.py", "evaluate_agentic_layout_oob_batch.py"):
            with self.subTest(script=script):
                output_dir = self.work / Path(script).stem
                args = [sys.executable, str(ROOT / "scripts" / script), "--prediction-root", str(prediction_root), "--gt-root", str(self.work), "--output-dir", str(output_dir)]
                if "oob" in script:
                    args += ["--workers", "1"]
                result = subprocess.run(args, cwd=self.work, env=env, text=True, encoding="utf-8", capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(list(output_dir.glob("*.csv")))

    def test_private_xml_preprocessing_emits_current_graph_schema(self) -> None:
        # A deliberately tiny, newly authored EAGLE XML board exercises conversion.
        wires = "".join(
            f'<wire x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" width="0" layer="20"/>'
            for a, b in zip([[0, 0], [10, 0], [10, 10], [0, 10]], [[10, 0], [10, 10], [0, 10], [0, 0]])
        )
        input_dir = self.work / "private_inputs"
        input_dir.mkdir()
        board = input_dir / "synthetic.BRD"
        board.write_text(
            '<eagle version="9.6.2"><drawing><board><plain>' + wires + '</plain>'
            '<libraries><library name="synthetic"><packages><package name="ONE_PAD">'
            '<smd name="1" x="0" y="0" dx="1" dy="1" layer="1"/>'
            '</package></packages></library></libraries><elements>'
            '<element name="U1" library="synthetic" package="ONE_PAD" value="SYNTHETIC" x="2" y="2" rot="R0"/>'
            '</elements><signals><signal name="N1"><contactref element="U1" pad="1"/>'
            '</signal></signals></board></drawing></eagle>', encoding="utf-8",
        )
        output = self.work / "preprocessed"
        self.run_cli("preprocess", input_dir, "--output-dir", output)
        result = json.loads((output / "graph" / "synthetic_graph.json").read_text(encoding="utf-8"))
        self.assertEqual(set(result), {"netlist", "board_boundary", "ic_library", "ic_position", "ic_dimension"})
        self.assertEqual(result["ic_position"][0]["element_name"], "U1")
        self.assertTrue(result["board_boundary"]["dxf_details"])

    def test_prompts_include_components_without_geometry_and_withhold_reference_positions(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["ic_position"].append({"element_name": "U_NO_GEOMETRY", "position": [123.456, 789.123], "rotation": "R0"})
        for module_name in ("llm_placer_gemini_0515", "llm_placer_openrouter_0515"):
            with self.subTest(provider=module_name):
                prompt = importlib.import_module(module_name).build_prompt(graph)
                self.assertIn("  - U_NO_GEOMETRY: geometry unavailable; placement is still required", prompt)
                self.assertIn("  - U1:", prompt)
                self.assertIn("  - U2:", prompt)
                self.assertNotIn("123.456", prompt)
                self.assertNotIn("789.123", prompt)

    def test_providers_save_current_prediction_before_rendering_without_api_calls(self) -> None:
        response = json.dumps({"placements": [
            {"name": "U1", "x": 2, "y": 2, "rotation": "R0"},
            {"name": "U2", "x": 8, "y": 8, "rotation": "R0"},
        ]})
        for module_name, call_name in (("llm_placer_gemini_0515", "call_gemini"), ("llm_placer_openrouter_0515", "call_openrouter")):
            with self.subTest(provider=module_name):
                module = importlib.import_module(module_name)
                rendered = []

                def inspect_saved_prediction(path: str, output: str) -> None:
                    rendered.append(json.loads(Path(path).read_text(encoding="utf-8")))

                with patch.object(module, call_name, return_value=response) as call, patch.object(module, "render_placement_to_img", side_effect=inspect_saved_prediction):
                    output = module.run(str(self.graph_path), str(self.work / module_name), model="offline-test", verbose=True)
                call.assert_called_once()
                self.assertEqual(len(rendered), 1)
                self.assertEqual(len(rendered[0]["ic_position"]), 2)
                self.assertEqual(len(output["ic_position"]), 2)


if __name__ == "__main__":
    unittest.main()
