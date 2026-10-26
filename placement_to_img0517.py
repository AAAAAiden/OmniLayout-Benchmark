import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Arc, Wedge
from pathlib import Path
import numpy as np


def parse_rotation_angle(rot_str):
    """解析 Eagle 旋转字符串，MR/SMR 返回正角度（镜像由几何变换实现）。"""
    rot = str(rot_str or 'R0').upper()
    if rot.startswith(('MR', 'SMR')):
        try:
            return float(rot.lstrip('MSR') or '0')
        except ValueError:
            return 0.0
    if rot.startswith(('R', 'S')):
        try:
            return float(rot.lstrip('SR') or '0')
        except ValueError:
            return 0.0
    return 0.0


def is_mirrored_rotation(rot_str):
    """判断是否为 MR/SMR 镜像旋转。"""
    return str(rot_str or '').upper().startswith(('MR', 'SMR'))


def normalize_block_layer(layer):
    """Normalize footprint layer to front / back / through."""
    layer_str = str(layer if layer is not None else 'front').strip().lower()
    if layer_str in ('16', 'bottom', 'back', 'b'):
        return 'back'
    if layer_str in ('through', 'th'):
        return 'through'
    return 'front'


def block_final_side(block, rot_str):
    """Resolve which board side a block ends up on after placement rotation."""
    initial = normalize_block_layer(block.get('layer'))
    if initial == 'through':
        return 'both'
    if is_mirrored_rotation(rot_str):
        return 'front' if initial == 'back' else 'back'
    return initial


def should_draw_block_on_view(block, rot_str, view):
    """Return True if block should appear on the given panel (front/back)."""
    if view is None:
        return True
    final_side = block_final_side(block, rot_str)
    if final_side == 'both':
        return True
    return final_side == view


def rotate_point(point, origin, angle_deg):
    """绕原点旋转点坐标，angle_deg 为角度（度）"""
    angle_rad = np.radians(angle_deg)
    ox, oy = origin
    px, py = point
    
    # 平移到原点
    x = px - ox
    y = py - oy
    
    # 旋转
    cos_a = np.cos(angle_rad)
    sin_a = np.sin(angle_rad)
    x_new = x * cos_a - y * sin_a
    y_new = x * sin_a + y * cos_a
    
    # 平移回去
    return [x_new + ox, y_new + oy]


def _resolve_arc_geometry(seg):
    center = list(seg.get('center', [0, 0]))
    start_angle = seg.get('start_angle', 0.0)
    end_angle = seg.get('end_angle', 90.0)
    sweep_angle = seg.get('sweep_angle')
    start = seg.get('start')
    end = seg.get('end')
    radius = seg.get('radius')

    if sweep_angle is None or start is None or end is None or radius is None:
        return center, start_angle, end_angle

    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    chord_dx = x2 - x1
    chord_dy = y2 - y1
    chord_len = float(np.hypot(chord_dx, chord_dy))
    if chord_len < 1e-9:
        return center, start_angle, end_angle

    mx = (x1 + x2) / 2.0
    my = (y1 + y2) / 2.0
    nx = -chord_dy / chord_len
    ny = chord_dx / chord_len
    center_sign = 1.0 if sweep_angle > 0 else -1.0
    h = float(radius) * np.cos(np.deg2rad(sweep_angle) / 2.0)
    center = [mx + center_sign * nx * h, my + center_sign * ny * h]
    start_angle = np.rad2deg(np.arctan2(y1 - center[1], x1 - center[0]))
    end_angle = start_angle + sweep_angle
    return center, start_angle, end_angle


def _arc_sample_points(seg, num_points=128):
    """Sample ARC polyline using Eagle wire curve semantics (signed sweep)."""
    start = seg.get('start')
    end = seg.get('end')
    if start is None or end is None:
        return []

    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])

    if seg.get('sweep_angle') is not None:
        curve = abs(float(seg['sweep_angle']))
        if seg.get('clockwise'):
            curve = -curve
    else:
        curve = float(seg.get('end_angle', 0.0)) - float(seg.get('start_angle', 0.0))

    if abs(curve) < 1e-9:
        return [[x1, y1], [x2, y2]]

    chord_dx = x2 - x1
    chord_dy = y2 - y1
    chord_len = float(np.hypot(chord_dx, chord_dy))
    if chord_len < 1e-9:
        return [[x1, y1], [x2, y2]]

    half_theta = np.deg2rad(abs(curve) / 2.0)
    sin_half_theta = np.sin(half_theta)
    if abs(sin_half_theta) < 1e-12:
        return [[x1, y1], [x2, y2]]

    radius = chord_len / (2.0 * sin_half_theta)
    h = radius * np.cos(np.deg2rad(curve) / 2.0)

    mx = (x1 + x2) / 2.0
    my = (y1 + y2) / 2.0
    nx = -chord_dy / chord_len
    ny = chord_dx / chord_len
    center_sign = 1.0 if curve > 0 else -1.0
    cx = mx + center_sign * nx * h
    cy = my + center_sign * ny * h

    start_angle = np.arctan2(y1 - cy, x1 - cx)
    sweep_rad = np.deg2rad(curve)
    steps = max(int(abs(curve) / 4), 8, int(num_points))
    angles = np.linspace(start_angle, start_angle + sweep_rad, steps)

    return [
        [float(cx + radius * np.cos(a)), float(cy + radius * np.sin(a))]
        for a in angles
    ]


def draw_board_boundary(dxf_details, mirror_x=False, x_center=None):
    """绘制板框（基于 dxf_details 的实际线段和圆弧，黑色实线）
    
    Args:
        dxf_details: DXF 实体列表
        mirror_x: 是否对 x 坐标进行镜像（用于绘制 back 视图）
        x_center: x 轴中心（用于镜像计算）
    """
    for seg in dxf_details:
        entity_type = seg.get('type')
        
        if entity_type == 'LINE':
            start = list(seg['start'])
            end = list(seg['end'])
            
            # 如果需要镜像，对 x 坐标进行镜像
            if mirror_x and x_center is not None:
                start[0] = 2.0 * x_center - start[0]
                end[0] = 2.0 * x_center - end[0]
            
            x_coords = [start[0], end[0]]
            y_coords = [start[1], end[1]]
            plt.plot(x_coords, y_coords, color='black', linewidth=2, zorder=100)
        
        elif entity_type == 'ARC':
            # Sample arc points directly from geometry
            arc_pts = _arc_sample_points(seg, num_points=128)
            if not arc_pts:
                continue
            
            # Apply mirroring if needed
            if mirror_x and x_center is not None:
                arc_pts = [[2.0 * x_center - p[0], p[1]] for p in arc_pts]
            
            x_coords = [p[0] for p in arc_pts]
            y_coords = [p[1] for p in arc_pts]
            plt.plot(x_coords, y_coords, color='black', linewidth=2, zorder=100)


def block_corners_global(block, element_pos, element_rotation, element_mirrored=False):
    """将 block 本地四角变换到板坐标。

    MR/SMR：先逆时针旋转 footprint，再沿 Y 轴镜像（X 取反），
    最后平移到板坐标中心 (x, y)。
    """
    local_pos = block.get('position', [0.0, 0.0])
    width = float(block.get('width', 0.0) or 0.0)
    length = float(block.get('length', 0.0) or 0.0)
    block_rot = parse_rotation_angle(block.get('rotation', 'R0'))

    local_x = float(local_pos[0])
    local_y = float(local_pos[1])

    corners = [
        [-width / 2.0, -length / 2.0],
        [width / 2.0, -length / 2.0],
        [width / 2.0, length / 2.0],
        [-width / 2.0, length / 2.0],
    ]

    corners = [rotate_point(c, [0, 0], block_rot) for c in corners]
    corners = [[c[0] + local_x, c[1] + local_y] for c in corners]
    corners = [rotate_point(c, [0, 0], element_rotation) for c in corners]
    if element_mirrored:
        corners = [[-c[0], c[1]] for c in corners]
    corners = [[c[0] + element_pos[0], c[1] + element_pos[1]] for c in corners]
    return corners


def draw_pad_shape(corners, pad_type='smd', color='gray', alpha=0.7):
    """使用四角坐标绘制 pad/smd，避免 Rectangle 旋转锚点误差。"""
    poly = Polygon(
        corners,
        closed=True,
        linewidth=0.5,
        edgecolor='black' if pad_type == 'pad' else 'none',
        facecolor=color,
        alpha=alpha,
        linestyle='-' if pad_type == 'smd' else '--',
        zorder=50
    )
    plt.gca().add_patch(poly)


def draw_hole(cx, cy, drill_size, alpha=0.3):
    """绘制孔（圆形）"""
    circle = Circle((cx, cy), drill_size/2, facecolor='gray', alpha=alpha, 
                    edgecolor='black', linewidth=0.5, linestyle=':', zorder=50)
    plt.gca().add_patch(circle)


def octagon_points(cx, cy, size):
    """Create a centered octagon by chamfering a square equally."""
    half = size / 2.0
    cut = size / (2.0 + np.sqrt(2.0))
    return [
        [cx - half + cut, cy + half],
        [cx + half - cut, cy + half],
        [cx + half, cy + half - cut],
        [cx + half, cy - half + cut],
        [cx + half - cut, cy - half],
        [cx - half + cut, cy - half],
        [cx - half, cy - half + cut],
        [cx - half, cy + half - cut],
    ]

def capsule_points(cx, cy, width, length, n=32):
    """Build a capsule outline centered at (cx, cy), major axis along +X."""
    radius = width / 2.0
    straight = max(length - width, 0.0)
    pts = []

    for i in range(n + 1):
        t = np.pi * i / n
        x = cx + straight / 2.0 + radius * np.sin(t)
        y = cy + radius * np.cos(t)
        pts.append([x, y])

    for i in range(n + 1):
        t = np.pi + np.pi * i / n
        x = cx - straight / 2.0 + radius * np.sin(t)
        y = cy + radius * np.cos(t)
        pts.append([x, y])

    return pts



def capsule_points(cx, cy, width, length, n=24):
    """Build a capsule outline centered at (cx, cy), major axis along +X."""
    radius = width / 2.0
    straight = max(length - width, 0.0)
    pts = []

    for i in range(n + 1):
        t = np.pi * i / n
        x = cx + straight / 2.0 + radius * np.sin(t)
        y = cy + radius * np.cos(t)
        pts.append([x, y])

    for i in range(n + 1):
        t = np.pi + np.pi * i / n
        x = cx - straight / 2.0 + radius * np.sin(t)
        y = cy + radius * np.cos(t)
        pts.append([x, y])

    return pts


def draw_through_pad_shape(corners, block):
    """Draw through-hole pad copper by Eagle pad shape and overlay drill hole."""
    cx = float(np.mean([p[0] for p in corners]))
    cy = float(np.mean([p[1] for p in corners]))

    drill_val = block.get('drill')
    diameter_val = block.get('diameter')
    shape = str(block.get('shape', '') or '').lower()

    drill_size = float(drill_val) if drill_val is not None else 0.0
    drill_radius = max(drill_size / 2.0, 0.0)

    # Compute pad dimensions from corners (already rotated)
    edge01 = [corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]]
    edge12 = [corners[2][0] - corners[1][0], corners[2][1] - corners[1][1]]
    len01 = float(np.hypot(edge01[0], edge01[1]))
    len12 = float(np.hypot(edge12[0], edge12[1]))

    outer_size = float(diameter_val) if diameter_val is not None else max(len01, len12, drill_size + 0.5)

    if shape in ('', 'round'):
        outer_radius = outer_size / 2.0
        if drill_radius > 0.0 and outer_radius > drill_radius:
            ring = Wedge(
                (cx, cy),
                outer_radius,
                0,
                360,
                width=outer_radius - drill_radius,
                facecolor='gray',
                edgecolor='black',
                linewidth=0.5,
                zorder=50,
            )
            plt.gca().add_patch(ring)
        else:
            disk = Circle((cx, cy), radius=max(outer_radius, 0.05), facecolor='gray', edgecolor='black', linewidth=0.5, zorder=50)
            plt.gca().add_patch(disk)
    elif shape == 'square':
        poly = Polygon(corners, closed=True, facecolor='gray', edgecolor='black', linewidth=0.5, zorder=50)
        plt.gca().add_patch(poly)
    elif shape.startswith('oct'):
        # Octagon: approximate by drawing the corners as octagon
        poly = Polygon(corners, closed=True, facecolor='gray', edgecolor='black', linewidth=0.5, zorder=50)
        plt.gca().add_patch(poly)
    elif shape in ('long', 'offset'):
        # For long/offset pads: build a capsule in local axis-aligned space
        # and rotate it to match the pad's major axis.
        if len01 >= len12:
            # edge01 is major axis
            major_pt_a = corners[0]
            major_pt_b = corners[1]
            minor_len = len12
        else:
            # edge12 is major axis
            major_pt_a = corners[1]
            major_pt_b = corners[2]
            minor_len = len01

        # Capsule: compute perpendicular to major axis, build semicircles + straight edges
        major_vec = [major_pt_b[0] - major_pt_a[0], major_pt_b[1] - major_pt_a[1]]
        major_len = float(np.hypot(major_vec[0], major_vec[1]))
        
        if major_len > 1e-9:
            # Derive the pad's total rotation from the major axis.
            total_rot = np.degrees(np.arctan2(major_vec[1], major_vec[0]))

            # Long pads are drawn from their actual width/length rather than from the corner chain.
            pad_width = max(minor_len, drill_size + 0.1, 0.1)
            pad_length = max(major_len, pad_width)

            offset_dist = 0.0
            if shape == 'offset' and drill_radius > 0.0:
                margin = 0.08
                offset_dist = max((pad_length / 2.0) - drill_radius - margin, 0.0)

            capsule_center = [cx, cy]
            if offset_dist > 0.0:
                rad = np.radians(total_rot)
                capsule_center[0] += offset_dist * np.cos(rad)
                capsule_center[1] += offset_dist * np.sin(rad)

            pts = capsule_points(capsule_center[0], capsule_center[1], pad_width, pad_length, n=32)
            pts = [rotate_point(pt, capsule_center, total_rot) for pt in pts]
            
            if pts:
                poly = Polygon(pts, closed=True, facecolor='gray', edgecolor='black', linewidth=0.5, zorder=50)
                plt.gca().add_patch(poly)
        else:
            # Degenerate case
            poly = Polygon(corners, closed=True, facecolor='gray', edgecolor='black', linewidth=0.5, zorder=50)
            plt.gca().add_patch(poly)
    else:
        draw_pad_shape(corners, pad_type='pad', color='gray', alpha=0.5)

    if drill_radius > 0.0:
        drill_hole = Circle((cx, cy), radius=max(drill_radius, 0.05), facecolor='white', edgecolor='black', linewidth=0.5, zorder=51)
        plt.gca().add_patch(drill_hole)


def draw_block_dimension_box_from_bbox(bbox, color, alpha=0.15):
    """直接从 bbox [xmin, ymin, xmax, ymax] 绘制虚线框"""
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return
    
    xmin, ymin, xmax, ymax = bbox
    box_corners = [
        [xmin, ymin],
        [xmax, ymin],
        [xmax, ymax],
        [xmin, ymax]
    ]
    poly = Polygon(box_corners, closed=True, linewidth=1, edgecolor=color, facecolor=color, alpha=alpha, linestyle='--', zorder=1)
    plt.gca().add_patch(poly)


def draw_block_dimension_box(blocks, pos, rotation_angle, color, alpha=0.15, element_mirrored=False):
    """绘制虚线框表示 block dimensions"""
    if not blocks:
        return
    
    # 收集所有 block 的变换后角点，再求 axis-aligned bbox
    all_points = []
    for block in blocks:
        all_points.extend(block_corners_global(block, pos, rotation_angle, element_mirrored))

    if not all_points:
        return

    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    # 得到全局包围盒四角
    box_corners = [
        [x_min, y_min],
        [x_max, y_min],
        [x_max, y_max],
        [x_min, y_max]
    ]
    poly = Polygon(box_corners, closed=True, linewidth=1, edgecolor=color, facecolor=color, alpha=alpha, linestyle='--', zorder=1)
    plt.gca().add_patch(poly)


def draw_overall_dimension_box_from_blocks(blocks, pos, rotation_angle, color, alpha=0.15, element_mirrored=False):
    """基于 block_dimension 实际放置后的坐标绘制元件整体尺寸框。"""
    if not blocks:
        return

    # 先把每个 block 变换到板坐标，再从所有角点求整体 axis-aligned bbox
    all_points = []
    for block in blocks:
        all_points.extend(block_corners_global(block, pos, rotation_angle, element_mirrored))

    if not all_points:
        return

    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    box_corners = [
        [x_min, y_min],
        [x_max, y_min],
        [x_max, y_max],
        [x_min, y_max],
    ]

    poly = Polygon(
        box_corners,
        closed=True,
        linewidth=1,
        edgecolor=color,
        facecolor=color,
        alpha=alpha,
        linestyle='--',
        zorder=1,
    )
    plt.gca().add_patch(poly)


def draw_elements(ic_position, ic_dimensions, layer_filter=None, x_center=None):
    """
    绘制所有元件及其焊盘
    layer_filter: None=全部, 'front'=正面视图, 'back'=背面视图

    SMD 层过滤：读取 block 的 layer（front/back/through），MR 旋转会翻转贴装面；
    through 的 hole/pad 在两个视图都显示。
    """
    # 构建 element_name 到 dimension 的映射
    dim_map = {d['element_name']: d for d in ic_dimensions}
    
    # 创建颜色映射
    num_elements = len(ic_position)
    color_map = plt.get_cmap('tab20', num_elements)
    
    # 第一步：绘制所有元件的 hole 和 pad（贯通显示，无虚线框）
    for idx, ele in enumerate(ic_position):
        name = ele['element_name']
        base_pos = list(ele['position'])
        rot_str = ele.get('rotation', 'R0')
        
        rotation_angle = parse_rotation_angle(rot_str)
        element_mirrored = is_mirrored_rotation(rot_str)

        dim = dim_map.get(name)
        if not dim:
            continue
        
        blocks = dim.get('dimension', [])
        hole_pad_blocks = [b for b in blocks if str(b.get('type', '')).lower() in ['hole', 'pad']]

        for block in hole_pad_blocks:
            block_type = str(block.get('type', '')).lower()
            corners = block_corners_global(block, base_pos, rotation_angle, element_mirrored)
            cx = float(np.mean([p[0] for p in corners]))
            cy = float(np.mean([p[1] for p in corners]))
            w = float(block.get('width', 0.0) or 0.0)

            if block_type == 'pad':
                draw_through_pad_shape(corners, block)
            elif block_type == 'hole':
                draw_hole(cx, cy, w, alpha=0.3)

            # 在每个贯通 pad/drill 旁边标注元件名
            label_dx = 0.35
            label_dy = 0.35
            plt.text(
                cx + label_dx,
                cy + label_dy,
                name,
                fontsize=4,
                ha='left',
                va='bottom',
                color='black',
                alpha=0.8,
                zorder=60,
            )
    
    # 第二步：绘制 SMD 和虚线框
    for idx, ele in enumerate(ic_position):
        name = ele['element_name']
        pos = list(ele['position'])
        rot_str = ele.get('rotation', 'R0')
        
        rotation_angle = parse_rotation_angle(rot_str)
        element_mirrored = is_mirrored_rotation(rot_str)

        dim = dim_map.get(name)
        if not dim:
            continue

        blocks = dim.get('dimension', [])
        smd_blocks = [
            b for b in blocks
            if str(b.get('type', '')).lower() == 'smd'
            and should_draw_block_on_view(b, rot_str, layer_filter)
        ]
        if layer_filter is not None and not smd_blocks:
            continue

        color = color_map(idx)
        rgb_color = color[:3]

        draw_overall_dimension_box_from_blocks(
            smd_blocks,
            pos,
            rotation_angle,
            rgb_color,
            alpha=0.15,
            element_mirrored=element_mirrored,
        )

        for block in smd_blocks:
            corners = block_corners_global(block, pos, rotation_angle, element_mirrored)
            draw_pad_shape(corners, pad_type='smd', color=rgb_color, alpha=0.7)

        plt.text(pos[0], pos[1], name, fontsize=6, ha='center', va='center',
                color='black', weight='bold',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.7, edgecolor='none'))


def main(graph_json_path, output_folder):
    """
    从 graph JSON 文件读取数据并生成 PCB 布局图
    左侧显示 front，右侧显示 back（按 footprint layer + MR 旋转决定贴装面）
    
    Args:
        graph_json_path: 输入的 graph JSON 文件路径
        output_folder: 输出文件夹路径
    """

    output_dir = Path(output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    graph_json_path = Path(graph_json_path)
    if graph_json_path.is_dir():
        json_files = sorted(graph_json_path.glob("*.json"))
        if not json_files:
            print(f"文件夹 {graph_json_path} 下没有 *.json 文件")
            return
        for jf in json_files:
            try:
                print(f"处理: {jf.name}")
                _render_single(jf, output_dir)
            except Exception as e:
                print(f"  ✗ 错误: {e}")
        return
    else:
        _render_single(graph_json_path, output_dir)
        return

def _render_single(graph_json_path, output_dir):
    with open(graph_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    ic_position = data.get('ic_position', [])
    ic_library = data.get('ic_library', [])
    pad_lookup = {}
    for ic in ic_library:
        element_name = ic.get('element_name')
        if not element_name:
            continue
        for pad in ic.get('pads', []):
            pad_name = pad.get('name')
            if pad_name is None:
                continue
            pad_lookup[(element_name, pad_name)] = pad
    
    # Required input format: ic_dimension + ic_position.
    ic_dimension_list = data.get('ic_dimension', [])
    ic_dimensions = [
        {
            'element_name': d.get('element_name'),
            'dimension': [
                {
                    **block,
                    **{
                        key: value
                        for key, value in pad_lookup.get((d.get('element_name'), block.get('name')), {}).items()
                        if key in ('drill', 'diameter', 'shape', 'rot')
                    },
                }
                for block in d.get('block_dimension', [])
            ],
        }
        for d in ic_dimension_list
    ]
    
    board_boundary = data.get('board_boundary', {})
    dxf_details = board_boundary.get('dxf_details', [])

    # 计算图表范围 - 包括所有采样的弧点
    all_points = []
    for seg in dxf_details:
        seg_type = seg.get('type')
        if seg_type == 'LINE':
            all_points.append(seg['start'])
            all_points.append(seg['end'])
        elif seg_type == 'ARC':
            # 包括端点和所有采样的弧点
            all_points.append(seg['start'])
            all_points.append(seg['end'])
            arc_pts = _arc_sample_points(seg, num_points=128)
            all_points.extend(arc_pts)

    if not all_points:
        all_points = [[0, 0], [10, 10]]

    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    x_min, x_max = min(xs) - 2, max(xs) + 2
    y_min, y_max = min(ys) - 2, max(ys) + 2
    x_center = (x_min + x_max) / 2.0

    # Extract json filename and clean up suffixes (_graph, _gemini_graph)
    json_name = Path(graph_json_path).stem
    json_name = json_name.replace("_gemini_graph", "").replace("_graph", "")

    fig, (ax_front, ax_back) = plt.subplots(1, 2, figsize=(28, 10))

    plt.sca(ax_front)
    draw_board_boundary(dxf_details, mirror_x=False, x_center=x_center)
    draw_elements(ic_position, ic_dimensions, layer_filter='front', x_center=x_center)
    ax_front.set_xlim(x_min, x_max)
    ax_front.set_ylim(y_min, y_max)
    ax_front.set_aspect('equal', adjustable='box')
    ax_front.grid(True, which='both', axis='both', color='gray', linestyle='--', linewidth=0.3, alpha=0.5)
    ax_front.set_xlabel('X coordinate (mm)', fontsize=10)
    ax_front.set_ylabel('Y coordinate (mm)', fontsize=10)
    ax_front.set_title('PCB and SMD Positions (Front)', fontsize=12, weight='bold')

    plt.sca(ax_back)
    draw_board_boundary(dxf_details, mirror_x=False, x_center=x_center)
    draw_elements(ic_position, ic_dimensions, layer_filter='back', x_center=x_center)
    ax_back.set_xlim(x_min, x_max)
    ax_back.set_ylim(y_min, y_max)
    ax_back.set_aspect('equal', adjustable='box')
    ax_back.grid(True, which='both', axis='both', color='gray', linestyle='--', linewidth=0.3, alpha=0.5)
    ax_back.set_xlabel('X coordinate (mm)', fontsize=10)
    ax_back.set_ylabel('Y coordinate (mm)', fontsize=10)
    ax_back.set_title('PCB and SMD Positions (Back)', fontsize=12, weight='bold')

    output_img = output_dir / f'{json_name}_img.png'
    plt.savefig(output_img, dpi=150, bbox_inches='tight')
    print(f'组合图像已保存至: {output_img}')
    plt.close()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render graph JSON placement geometry.")
    parser.add_argument("input", help="Graph JSON file or directory of graph JSON files")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    main(args.input, args.output_dir)
