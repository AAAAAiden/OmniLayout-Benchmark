import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.path import Path as MplPath

# Allow running as: python eval_code/evaluate_pred_gt_out_of_boundary.py
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from placement_metrics import (
    normalize_placement_source,
    parse_rotation_angle,
    is_mirrored_rotation,
    _block_corners_global,
    _polygon_area,
    _polygon_intersection_area,
)
from placement_to_img_0515 import main as render_placement

try:
    from shapely.geometry import Polygon as ShapelyPolygon
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


# ── Configuration ────────────────────────────────────────────────────────────
GRAPH_PATH = None
PLACEMENT_PATH = None
OUTPUT_DIR = None
SKIP_PLACEMENT_RENDER = False

# A block is "out of boundary" when its out-of-boundary area exceeds this fraction
# of the block's own area.  Adjust as needed.
OOB_EPSILON = 1e-6
ARC_SAMPLES = 32   # points used to approximate each arc segment


# ── Board polygon reconstruction ─────────────────────────────────────────────

def _sample_arc(seg: Dict[str, Any], n: int = ARC_SAMPLES) -> List[List[float]]:
    """Return n+1 points along an ARC segment (inclusive of start & end)."""
    cx, cy = seg["center"][0], seg["center"][1]
    r = seg["radius"]
    start_ang = math.radians(seg["start_angle"])
    sweep = math.radians(seg["sweep_angle"])
    if seg.get("clockwise", False):
        sweep = -abs(sweep)
    else:
        sweep = abs(sweep)
    pts = []
    for i in range(n + 1):
        angle = start_ang + sweep * i / n
        pts.append([cx + r * math.cos(angle), cy + r * math.sin(angle)])
    return pts


def build_board_polygon(board_boundary: Dict[str, Any]) -> List[List[float]]:
    """
    Reconstruct an ordered polygon from the board boundary segments.

    Strategy:
      1. Collect all primitives in source_index order.
      2. For each primitive, emit its point(s): LINE → [start, end]; ARC → sampled arc.
      3. Walk the chain, skipping duplicate consecutive points.
    Returns a flat list of [x, y] vertices forming the closed polygon.
    """
    # Prefer dxf_details (richer); fall back to segment_details.
    segs = board_boundary.get("dxf_details") or board_boundary.get("segment_details") or []
    if not segs:
        raise ValueError("board_boundary contains no segment data.")

    # Sort by source_index if present so we follow the outline order.
    segs = sorted(segs, key=lambda s: s.get("source_index", 0))

    pts: List[List[float]] = []
    for seg in segs:
        typ = seg.get("type", "LINE").upper()
        if typ == "LINE":
            for p in [seg["start"], seg["end"]]:
                pts.append([float(p[0]), float(p[1])])
        elif typ == "ARC":
            for p in _sample_arc(seg):
                pts.append(p)
        # Other primitive types (CIRCLE etc.) are skipped.

    # Deduplicate consecutive near-identical points (tolerance 1e-9 mm).
    EPS = 1e-9
    deduped: List[List[float]] = []
    for p in pts:
        if deduped and abs(p[0] - deduped[-1][0]) < EPS and abs(p[1] - deduped[-1][1]) < EPS:
            continue
        deduped.append(p)

    return deduped


def _build_board_shapely(polygon: List[List[float]]):
    """Return a Shapely polygon or None."""
    if HAS_SHAPELY and len(polygon) >= 3:
        try:
            return ShapelyPolygon(polygon)
        except Exception:
            pass
    return None


def _aabb_of(pts: List[List[float]]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _aabb_contains_polygon(board_aabb: Tuple, poly: List[List[float]]) -> bool:
    bx0, by0, bx1, by1 = board_aabb
    for x, y in poly:
        if x < bx0 or x > bx1 or y < by0 or y > by1:
            return False
    return True


def _estimate_inside_area_by_sampling(
    block_poly: List[List[float]],
    board_polygon: List[List[float]],
    block_area: float,
    samples_per_axis: int = 36,
) -> float:
    """
    Estimate area(block ∩ board) using point sampling in the block bounding box.
    This fallback correctly respects rounded/non-rectangular board outlines.
    """
    if block_area <= 0.0 or len(block_poly) < 3 or len(board_polygon) < 3:
        return 0.0

    bx0, by0, bx1, by1 = _aabb_of(block_poly)
    w = bx1 - bx0
    h = by1 - by0
    if w <= 0.0 or h <= 0.0:
        return 0.0

    nx = max(12, samples_per_axis)
    ny = max(12, samples_per_axis)

    block_path = MplPath(block_poly)
    board_path = MplPath(board_polygon)

    in_block = 0
    in_both = 0

    # Sample cell centers to avoid boundary ambiguity.
    for iy in range(ny):
        y = by0 + (iy + 0.5) * h / ny
        for ix in range(nx):
            x = bx0 + (ix + 0.5) * w / nx
            if not block_path.contains_point((x, y)):
                continue
            in_block += 1
            if board_path.contains_point((x, y)):
                in_both += 1

    if in_block == 0:
        return 0.0

    return block_area * (in_both / in_block)


def _out_of_boundary_area(
    block_poly: List[List[float]],
    board_polygon: List[List[float]],
    board_shapely,
    board_aabb: Tuple[float, float, float, float],
) -> float:
    """
    Return the area of block_poly that lies outside the board.
    Uses Shapely when available, otherwise falls back to AABB approximation.
    """
    block_area = _polygon_area(block_poly)
    if block_area <= 0.0:
        return 0.0

    if board_shapely is not None:
        try:
            blk_shp = ShapelyPolygon(block_poly)
            inside_area = board_shapely.intersection(blk_shp).area
            return max(0.0, block_area - inside_area)
        except Exception:
            pass

    # Non-shapely fallback: sampling against the actual board polygon.
    inside_area = _estimate_inside_area_by_sampling(block_poly, board_polygon, block_area)
    return max(0.0, block_area - inside_area)


# ── Per-component out-of-boundary evaluation ─────────────────────────────────

def compute_out_of_boundary(
    graph_data: Dict[str, Any],
    placement_source: Any,
    epsilon: float = OOB_EPSILON,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "ratio": float,              # out-of-boundary components / total components
        "oob_count": int,
        "total_count": int,
        "components": [
          {
            "name": str,
            "oob_area": float,       # total out-of-boundary area across all blocks
            "total_area": float,     # total block area
            "is_oob": bool,
            "blocks": [ {"poly": [...], "oob_area": float} ]
          }, ...
        ]
      }
    """
    placements = normalize_placement_source(placement_source)
    board_polygon = build_board_polygon(graph_data["board_boundary"])
    board_shapely = _build_board_shapely(board_polygon)
    board_aabb = _aabb_of(board_polygon)

    dimensions = {
        d["element_name"]: d
        for d in graph_data.get("ic_dimension", [])
        if "element_name" in d
    }

    component_results = []
    for name, placement in placements.items():
        pos = placement["position"]
        rotation = parse_rotation_angle(placement["rotation"])
        mirrored = is_mirrored_rotation(placement["rotation"])
        dim = dimensions.get(name, {})
        blocks = dim.get("block_dimension") or dim.get("dimension") or []

        comp_oob = 0.0
        comp_total = 0.0
        block_details = []
        for block in blocks:
            poly = _block_corners_global(block, pos, rotation, mirrored)
            area = _polygon_area(poly)
            oob = _out_of_boundary_area(poly, board_polygon, board_shapely, board_aabb)
            comp_oob += oob
            comp_total += area
            block_details.append({"poly": poly, "oob_area": oob})

        is_oob = comp_oob > epsilon
        component_results.append(
            {
                "name": name,
                "oob_area": comp_oob,
                "total_area": comp_total,
                "is_oob": is_oob,
                "blocks": block_details,
            }
        )

    total = len(component_results)
    oob_count = sum(1 for c in component_results if c["is_oob"])
    ratio = oob_count / total if total > 0 else 0.0

    return {
        "ratio": ratio,
        "oob_count": oob_count,
        "total_count": total,
        "components": component_results,
    }


# ── Visualisation ─────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_boundary_segments(board_boundary: Dict[str, Any]) -> List[Dict[str, Any]]:
    segs = board_boundary.get("dxf_details")
    if isinstance(segs, list) and segs:
        return segs
    segs = board_boundary.get("segment_details")
    if isinstance(segs, list):
        return segs
    return []


def _draw_board_boundary(ax, segments: List[Dict[str, Any]]) -> None:
    for seg in segments:
        typ = seg.get("type", "LINE").upper()
        if typ == "LINE":
            start, end = seg.get("start"), seg.get("end")
            if start and end:
                ax.plot([start[0], end[0]], [start[1], end[1]],
                        color="black", linewidth=1.5, zorder=10)
        elif typ == "ARC":
            pts = _sample_arc(seg)
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ax.plot(xs, ys, color="black", linewidth=1.5, zorder=10)


def _draw_oob_overlay(
    ax,
    oob_result: Dict[str, Any],
    board_polygon: List[List[float]],
) -> None:
    # Semi-transparent board fill.
    if len(board_polygon) >= 3:
        ax.add_patch(
            MplPolygon(
                board_polygon,
                closed=True,
                facecolor="lightblue",
                edgecolor="none",
                alpha=0.15,
                zorder=5,
            )
        )

    for comp in oob_result["components"]:
        for blk in comp["blocks"]:
            poly = blk["poly"]
            if len(poly) < 3:
                continue
            if blk["oob_area"] > OOB_EPSILON:
                # Out-of-boundary block: red fill.
                fc, ec, lw = "red", "darkred", 1.0
                alpha = 0.45
            else:
                # In-boundary block: green fill.
                fc, ec, lw = "green", "darkgreen", 0.4
                alpha = 0.25
            ax.add_patch(
                MplPolygon(
                    poly,
                    closed=True,
                    facecolor=fc,
                    edgecolor=ec,
                    linewidth=lw,
                    alpha=alpha,
                    zorder=20,
                )
            )

        # Label out-of-boundary components.
        if comp["is_oob"]:
            xs = [p[0] for blk in comp["blocks"] for p in blk["poly"]]
            ys = [p[1] for blk in comp["blocks"] for p in blk["poly"]]
            if xs and ys:
                cx = (min(xs) + max(xs)) / 2
                cy = (min(ys) + max(ys)) / 2
                ax.text(
                    cx, cy, comp["name"],
                    fontsize=5, ha="center", va="center",
                    color="darkred", zorder=30,
                )


def _plot_and_save(
    graph: Dict[str, Any],
    placement_source: Dict[str, Any],
    label: str,
    out_stem: str,
) -> Dict[str, Any]:
    oob_result = compute_out_of_boundary(graph, placement_source)
    board_polygon = build_board_polygon(graph["board_boundary"])

    fig, ax = plt.subplots(1, 1, figsize=(14, 10))
    _draw_board_boundary(ax, _extract_boundary_segments(graph.get("board_boundary", {})))
    _draw_oob_overlay(ax, oob_result, board_polygon)

    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.3, alpha=0.5)
    ax.set_xlabel("X coordinate (mm)")
    ax.set_ylabel("Y coordinate (mm)")
    ax.set_title(
        (
            f"Out-of-Boundary ({label})"
            f" | OOB ratio={oob_result['ratio']:.4f}"
            f" | {oob_result['oob_count']}/{oob_result['total_count']} components"
        ),
        fontsize=12,
        weight="bold",
    )

    out_path = OUTPUT_DIR / out_stem
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path}")
    return oob_result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    global GRAPH_PATH, PLACEMENT_PATH, OUTPUT_DIR, SKIP_PLACEMENT_RENDER

    parser = argparse.ArgumentParser(description="Evaluate and visualize component out-of-boundary ratio.")
    parser.add_argument("graph", type=Path)
    parser.add_argument("--prediction", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-placement-render", action="store_true")
    args = parser.parse_args()
    GRAPH_PATH = args.graph
    PLACEMENT_PATH = args.prediction if args.prediction is not None else args.graph
    OUTPUT_DIR = args.output_dir
    SKIP_PLACEMENT_RENDER = args.skip_placement_render
    graph = _load_json(GRAPH_PATH)
    placement_data = _load_json(PLACEMENT_PATH) if PLACEMENT_PATH.exists() else graph
    placement_source = {"ic_position": placement_data.get("ic_position", graph.get("ic_position", []))}

    if not placement_source["ic_position"]:
        raise ValueError("No ic_position found in placement or graph JSON.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not HAS_SHAPELY:
        print(
            "Warning: shapely not installed — falling back to AABB approximation. "
            "Install with: pip install shapely"
        )

    # ── Prediction placement ──────────────────────────────────────────────────
    print("=== Prediction ===")
    oob_pred = compute_out_of_boundary(graph, placement_source)
    print(
        f"OOB ratio      = {oob_pred['ratio']:.6f}  "
        f"({oob_pred['oob_count']} / {oob_pred['total_count']} components)"
    )
    if oob_pred["oob_count"]:
        for c in oob_pred["components"]:
            if c["is_oob"]:
                print(f"  OUT: {c['name']}  oob_area={c['oob_area']:.4f} mm²")

    # Optional placement render.
    if not SKIP_PLACEMENT_RENDER:
        render_input = PLACEMENT_PATH if PLACEMENT_PATH.exists() else GRAPH_PATH
        render_placement(str(render_input), str(OUTPUT_DIR))

    board_name = GRAPH_PATH.stem.replace("_graph", "")
    _plot_and_save(
        graph,
        placement_source,
        label="Prediction",
        out_stem=f"{board_name}_oob_pred.png",
    )

    # ── GT placement ─────────────────────────────────────────────────────────
    if graph.get("ic_position"):
        print("\n=== Ground Truth ===")
        gt_placement_source = {"ic_position": graph["ic_position"]}
        oob_gt = compute_out_of_boundary(graph, gt_placement_source)
        print(
            f"OOB ratio      = {oob_gt['ratio']:.6f}  "
            f"({oob_gt['oob_count']} / {oob_gt['total_count']} components)"
        )
        if oob_gt["oob_count"]:
            for c in oob_gt["components"]:
                if c["is_oob"]:
                    print(f"  OUT: {c['name']}  oob_area={c['oob_area']:.4f} mm²")

        _plot_and_save(
            graph,
            gt_placement_source,
            label="GT",
            out_stem=f"{board_name}_oob_gt.png",
        )


if __name__ == "__main__":
    main()
