import math
import itertools
from typing import Any, Dict, List, Optional, Tuple

try:
    from shapely.geometry import Polygon as ShapelyPolygon
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


def parse_rotation_angle(rot_str: Optional[str]) -> float:
    rot = str(rot_str or "").upper()
    if rot.startswith(("MR", "SMR")):
        try:
            return -float(rot.lstrip('MSR') or '0')
        except ValueError:
            return 0.0
    if rot.startswith(("R", "S")):
        try:
            return float(rot.lstrip('SR') or '0')
        except ValueError:
            return 0.0
    return 0.0


def is_mirrored_rotation(rot_str: Optional[str]) -> bool:
    return str(rot_str or "").upper().startswith(("MR", "SMR"))


def rotate_point(point: List[float], origin: List[float], angle_deg: float) -> List[float]:
    ox, oy = origin
    px, py = point
    theta = math.radians(angle_deg)
    qx = ox + math.cos(theta) * (px - ox) - math.sin(theta) * (py - oy)
    qy = oy + math.sin(theta) * (px - ox) + math.cos(theta) * (py - oy)
    return [qx, qy]


def normalize_placement_source(placement_source: Any) -> Dict[str, Dict[str, Any]]:
    placements = []
    if isinstance(placement_source, dict):
        if 'placements' in placement_source:
            placements = placement_source['placements']
        elif 'ic_position' in placement_source:
            placements = placement_source['ic_position']
        elif 'position' in placement_source and 'element_name' in placement_source:
            placements = [placement_source]
        else:
            raise ValueError('Unsupported placement source format')
    elif isinstance(placement_source, list):
        placements = placement_source
    else:
        raise ValueError('Unsupported placement source type')

    placement_map: Dict[str, Dict[str, Any]] = {}
    for item in placements:
        name = item.get('element_name') or item.get('name')
        if not name:
            continue
        x = item.get('x') if 'x' in item else None
        y = item.get('y') if 'y' in item else None
        if x is None or y is None:
            pos = item.get('position')
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                x, y = float(pos[0]), float(pos[1])
            else:
                continue
        rotation = item.get('rotation') if 'rotation' in item else item.get('rot')
        placement_map[name] = {
            'position': [float(x), float(y)],
            'rotation': str(rotation or 'R0'),
        }
    return placement_map


def _block_corners_global(
    block: Dict[str, Any],
    element_pos: List[float],
    element_rotation: float,
    element_mirrored: bool = False,
) -> List[List[float]]:
    local_pos = block.get('position', block.get('pos', [0.0, 0.0]))
    if not isinstance(local_pos, (list, tuple)) or len(local_pos) < 2:
        local_pos = [0.0, 0.0]

    local_x = float(local_pos[0])
    local_y = float(local_pos[1])
    block_type = str(block.get('type', '')).lower()
    if element_mirrored and block_type == 'hole':
        local_x = -local_x

    width = float(block.get('width', 0.0) or 0.0)
    length = float(block.get('length', 0.0) or 0.0)
    block_rot = parse_rotation_angle(block.get('rotation', 'R0'))

    corners = [
        [-width / 2.0, -length / 2.0],
        [width / 2.0, -length / 2.0],
        [width / 2.0, length / 2.0],
        [-width / 2.0, length / 2.0],
    ]
    corners = [rotate_point(c, [0.0, 0.0], block_rot) for c in corners]
    corners = [[c[0] + local_x, c[1] + local_y] for c in corners]
    corners = [rotate_point(c, [0.0, 0.0], element_rotation) for c in corners]
    corners = [[c[0] + element_pos[0], c[1] + element_pos[1]] for c in corners]
    return corners


def _polygon_area(polygon: List[List[float]]) -> float:
    if len(polygon) < 3:
        return 0.0
    area = 0.0
    for i in range(len(polygon)):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % len(polygon)]
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def _aabb_from_polygon(poly: List[List[float]]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _aabb_intersection_area(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _polygon_intersection_area(poly1: List[List[float]], poly2: List[List[float]]) -> float:
    if HAS_SHAPELY:
        try:
            return ShapelyPolygon(poly1).intersection(ShapelyPolygon(poly2)).area
        except Exception:
            pass
    return _aabb_intersection_area(_aabb_from_polygon(poly1), _aabb_from_polygon(poly2))


def _segment_orientation(a: List[float], b: List[float], c: List[float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: List[float], b: List[float], c: List[float]) -> bool:
    return (
        min(a[0], c[0]) <= b[0] <= max(a[0], c[0])
        and min(a[1], c[1]) <= b[1] <= max(a[1], c[1])
    )


def _segments_intersect(p1: List[float], p2: List[float], q1: List[float], q2: List[float]) -> bool:
    o1 = _segment_orientation(p1, p2, q1)
    o2 = _segment_orientation(p1, p2, q2)
    o3 = _segment_orientation(q1, q2, p1)
    o4 = _segment_orientation(q1, q2, p2)

    if o1 == 0 and _on_segment(p1, q1, p2):
        return True
    if o2 == 0 and _on_segment(p1, q2, p2):
        return True
    if o3 == 0 and _on_segment(q1, p1, q2):
        return True
    if o4 == 0 and _on_segment(q1, p2, q2):
        return True

    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _distance(a: List[float], b: List[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[rb] = ra
        return True


def build_mst_segments(points: List[List[float]]) -> List[Tuple[List[float], List[float]]]:
    if len(points) < 2:
        return []
    edges = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            edges.append((round(_distance(points[i], points[j]), 12), i, j))
    edges.sort(key=lambda x: x[0])
    uf = _UnionFind(len(points))
    segments = []
    for _, i, j in edges:
        if uf.union(i, j):
            segments.append((points[i], points[j]))
            if len(segments) == len(points) - 1:
                break
    return segments


def build_pin_position_map(graph_data: Dict[str, Any], placement_source: Any) -> Dict[str, Dict[str, List[float]]]:
    placements = normalize_placement_source(placement_source)
    dimensions = {d['element_name']: d for d in graph_data.get('ic_dimension', []) if 'element_name' in d}
    ic_library = graph_data.get('ic_library', []) or []

    # Style A: library entries are grouped by package/library and contain pad lists.
    package_by_name = {}
    # Style B: library entries are per element and contain smd/pads/holes lists.
    library_by_element = {}
    for pkg in ic_library:
        element_name = pkg.get('element_name')
        if element_name:
            library_by_element[element_name] = pkg
        if pkg.get('name') is None:
            continue
        key = (pkg.get('name'), pkg.get('library'))
        package_by_name[key] = pkg
        package_by_name[(pkg.get('name'), None)] = pkg

    pin_map: Dict[str, Dict[str, List[float]]] = {}
    for element_name, placement in placements.items():
        pos = placement['position']
        rotation = parse_rotation_angle(placement['rotation'])
        mirrored = is_mirrored_rotation(placement['rotation'])
        dim = dimensions.get(element_name, {})
        pkg_name = dim.get('package_name')
        lib_name = dim.get('library_name')
        pkg = None
        if pkg_name is not None:
            pkg = package_by_name.get((pkg_name, lib_name)) or package_by_name.get((pkg_name, None))
        element_lib = library_by_element.get(element_name)

        pin_map[element_name] = {}
        if element_lib is not None:
            pad_groups = [
                element_lib.get('smd', []),
                element_lib.get('pads', []),
                element_lib.get('pad', []),
                element_lib.get('holes', []),
                element_lib.get('hole', []),
            ]
            for group in pad_groups:
                for pad in group:
                    pad_name = pad.get('name')
                    if pad_name is None:
                        continue
                    local_pos = pad.get('pos', pad.get('position', [0.0, 0.0]))
                    if not isinstance(local_pos, (list, tuple)) or len(local_pos) < 2:
                        continue
                    pin_map[element_name][pad_name] = rotate_point(
                        [float(local_pos[0]), float(local_pos[1])],
                        [0.0, 0.0],
                        rotation,
                    )
                    pin_map[element_name][pad_name][0] += pos[0]
                    pin_map[element_name][pad_name][1] += pos[1]
        elif pkg is not None:
            for pad in pkg.get('pad', []):
                pad_name = pad.get('name')
                if pad_name is None:
                    continue
                local_pos = pad.get('pos', pad.get('position', [0.0, 0.0]))
                if not isinstance(local_pos, (list, tuple)) or len(local_pos) < 2:
                    continue
                pin_map[element_name][pad_name] = rotate_point(
                    [float(local_pos[0]), float(local_pos[1])],
                    [0.0, 0.0],
                    rotation,
                )
                pin_map[element_name][pad_name][0] += pos[0]
                pin_map[element_name][pad_name][1] += pos[1]
        else:
            blocks = dim.get('block_dimension') or dim.get('dimension') or []
            for block in blocks:
                block_type = str(block.get('type', '')).lower()
                if block_type not in ('pad', 'smd', 'hole'):
                    continue
                pad_name = block.get('name') or f"block_{element_name}_{len(pin_map[element_name])}"
                local_pos = block.get('position', block.get('pos', [0.0, 0.0]))
                if not isinstance(local_pos, (list, tuple)) or len(local_pos) < 2:
                    continue
                pin_map[element_name][pad_name] = rotate_point(
                    [float(local_pos[0]), float(local_pos[1])],
                    [0.0, 0.0],
                    rotation,
                )
                pin_map[element_name][pad_name][0] += pos[0]
                pin_map[element_name][pad_name][1] += pos[1]

    return pin_map


def compute_hpwl(graph_data: Dict[str, Any], placement_source: Any) -> float:
    pin_map = build_pin_position_map(graph_data, placement_source)
    total_hpwl = 0.0
    for net in graph_data.get('netlist', []):
        coords = []
        for ref in net.get('contactref', []):
            element = ref.get('element')
            pad = ref.get('pad')
            if not element or pad is None:
                continue
            coord = pin_map.get(element, {}).get(pad)
            if coord is not None:
                coords.append(coord)
        if len(coords) < 2:
            continue
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        total_hpwl += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total_hpwl


def _build_net_segments(graph_data: Dict[str, Any], placement_source: Any) -> List[Tuple[List[float], List[float]]]:
    pin_map = build_pin_position_map(graph_data, placement_source)
    segments = []
    for net in graph_data.get('netlist', []):
        coords = []
        for ref in net.get('contactref', []):
            element = ref.get('element')
            pad = ref.get('pad')
            if not element or pad is None:
                continue
            coord = pin_map.get(element, {}).get(pad)
            if coord is not None:
                coords.append(coord)
        if len(coords) < 2:
            continue
        if len(coords) == 2:
            segments.append((coords[0], coords[1]))
        else:
            segments.extend(build_mst_segments(coords))
    return segments


def compute_net_crossing(graph_data: Dict[str, Any], placement_source: Any) -> int:
    segments = _build_net_segments(graph_data, placement_source)
    crossing_count = 0
    for (p1, p2), (q1, q2) in itertools.combinations(segments, 2):
        if p1 == q1 or p1 == q2 or p2 == q1 or p2 == q2:
            continue
        if _segments_intersect(p1, p2, q1, q2):
            crossing_count += 1
    return crossing_count


def compute_net_separation(graph_data: Dict[str, Any], placement_source: Any) -> float:
    pin_map = build_pin_position_map(graph_data, placement_source)
    bboxes = []
    for net in graph_data.get('netlist', []):
        coords = []
        for ref in net.get('contactref', []):
            element = ref.get('element')
            pad = ref.get('pad')
            if not element or pad is None:
                continue
            coord = pin_map.get(element, {}).get(pad)
            if coord is not None:
                coords.append(coord)
        if not coords:
            continue
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        bboxes.append((min(xs), min(ys), max(xs), max(ys)))
    total_overlap = 0.0
    for a, b in itertools.combinations(bboxes, 2):
        total_overlap += _aabb_intersection_area(a, b)
    return total_overlap


def compute_overlap(graph_data: Dict[str, Any], placement_source: Any) -> float:
    placements = normalize_placement_source(placement_source)
    blocks = []
    total_block_area = 0.0
    for dim in graph_data.get('ic_dimension', []):
        name = dim.get('element_name')
        if not name:
            continue
        placement = placements.get(name)
        if not placement:
            continue
        pos = placement['position']
        rotation = parse_rotation_angle(placement['rotation'])
        mirrored = is_mirrored_rotation(placement['rotation'])
        for block in dim.get('block_dimension', []):
            poly = _block_corners_global(block, pos, rotation, mirrored)
            area = _polygon_area(poly)
            if area <= 0.0:
                continue
            blocks.append((name, poly, area))
            total_block_area += area

    if total_block_area <= 0.0:
        return 0.0

    overlap_area = 0.0
    for (name_a, poly_a, _), (name_b, poly_b, _) in itertools.combinations(blocks, 2):
        if name_a == name_b:
            continue
        overlap_area += _polygon_intersection_area(poly_a, poly_b)
    return overlap_area / total_block_area


def compute_runtime(result_data: Dict[str, Any]) -> float:
    return float(result_data.get('duration_seconds', 0.0) or 0.0)


def evaluate_placement(graph_data: Dict[str, Any], placement_source: Any, result_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metrics = {
        'hpwl': compute_hpwl(graph_data, placement_source),
        'net_crossings': compute_net_crossing(graph_data, placement_source),
        'net_separation': compute_net_separation(graph_data, placement_source),
        'normalized_overlap': compute_overlap(graph_data, placement_source),
    }
    if result_data is not None:
        metrics['runtime_seconds'] = compute_runtime(result_data)
    return metrics
