import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Arc
from pathlib import Path
import numpy as np


def parse_rotation_angle(rot_str):
    """解析 Eagle 旋转字符串，MR/SMR 返回负角度。"""
    rot = str(rot_str or 'R0').upper()
    if rot.startswith(('MR', 'SMR')):
        try:
            return -float(rot.lstrip('MSR') or '0')
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
            start = list(seg.get('start', [0, 0]))
            end = list(seg.get('end', [0, 0]))
            center = list(seg.get('center', [0, 0]))
            radius = seg.get('radius', 1.0)
            start_angle = seg.get('start_angle', 0.0)
            end_angle = seg.get('end_angle', 90.0)
            sweep_angle = seg.get('sweep_angle', 90.0)
            
            # 如果需要镜像，对坐标和角度进行镜像
            if mirror_x and x_center is not None:
                start[0] = 2.0 * x_center - start[0]
                end[0] = 2.0 * x_center - end[0]
                center[0] = 2.0 * x_center - center[0]
                # 镜像后角度需要反向
                start_angle = 180.0 - start_angle
                end_angle = 180.0 - end_angle
                start_angle, end_angle = end_angle, start_angle
                sweep_angle = -sweep_angle
            
            # matplotlib Arc 总是逆时针从 theta1 到 theta2
            # 对于负扫描角（顺时针），交换角度
            if sweep_angle < 0:
                # 顺时针：交换角度使 matplotlib 绘制相同的圆弧
                theta1 = end_angle
                theta2 = start_angle
            else:
                # 逆时针：直接使用角度
                theta1 = start_angle
                theta2 = end_angle
            
            arc = Arc(
                center,
                2 * radius,
                2 * radius,
                angle=0.0,
                theta1=theta1,
                theta2=theta2,
                linewidth=2,
                color='black',
                zorder=100
            )
            plt.gca().add_patch(arc)


def block_corners_global(block, element_pos, element_rotation, element_mirrored=False):
    """按 Eagle 逻辑将 block 本地四角变换到板坐标。"""
    local_pos = block.get('position', [0.0, 0.0])
    width = float(block.get('width', 0.0) or 0.0)
    length = float(block.get('length', 0.0) or 0.0)
    block_rot = parse_rotation_angle(block.get('rotation', 'R0'))
    block_type = str(block.get('type', '')).lower()

    local_x = float(local_pos[0])
    local_y = float(local_pos[1])
    # 与 EagleXML_to_Brd.py 一致：MR/SMR 时 hole 先镜像 local x
    if element_mirrored and block_type == 'hole':
        local_x = -local_x

    corners = [
        [-width / 2.0, -length / 2.0],
        [width / 2.0, -length / 2.0],
        [width / 2.0, length / 2.0],
        [-width / 2.0, length / 2.0],
    ]

    corners = [rotate_point(c, [0, 0], block_rot) for c in corners]
    corners = [[c[0] + local_x, c[1] + local_y] for c in corners]
    corners = [rotate_point(c, [0, 0], element_rotation) for c in corners]
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


def draw_elements(ic_position, ic_dimensions, layer_filter=None):
    """
    绘制所有元件及其焊盘
    layer_filter: None=全部, 'front'=非镜像(R), 'back'=镜像(MR)
    
    hole 和 pad 类型在两个视图中都显示（贯通）
    只有 SMD 根据层过滤
    """
    # 构建 element_name 到 dimension 的映射
    dim_map = {d['element_name']: d for d in ic_dimensions}
    
    # 创建颜色映射
    num_elements = len(ic_position)
    color_map = plt.get_cmap('tab20', num_elements)
    
    # 第一步：绘制所有元件的 hole 和 pad（不过滤，贯通显示，无虚线框）
    for idx, ele in enumerate(ic_position):
        name = ele['element_name']
        pos = ele['position']
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
            corners = block_corners_global(block, pos, rotation_angle, element_mirrored)
            cx = float(np.mean([p[0] for p in corners]))
            cy = float(np.mean([p[1] for p in corners]))
            w = float(block.get('width', 0.0) or 0.0)

            if block_type == 'pad':
                draw_pad_shape(corners, pad_type='pad', color='none', alpha=0.5)
            elif block_type == 'hole':
                draw_hole(cx, cy, w, alpha=0.3)
    
    # 第二步：根据 layer_filter 筛选元件，绘制 SMD
    filtered_elements = []
    for idx, ele in enumerate(ic_position):
        rot_str = ele.get('rotation', 'R0')
        element_mirrored = is_mirrored_rotation(rot_str)
        
        if layer_filter == 'front' and element_mirrored:
            continue
        if layer_filter == 'back' and not element_mirrored:
            continue
        
        filtered_elements.append((idx, ele))
    
    # 绘制过滤后元件的 SMD 和虚线框
    for idx, ele in filtered_elements:
        name = ele['element_name']
        pos = ele['position']
        rot_str = ele.get('rotation', 'R0')
        
        rotation_angle = parse_rotation_angle(rot_str)
        element_mirrored = is_mirrored_rotation(rot_str)
        
        dim = dim_map.get(name)
        if not dim:
            continue
        
        color = color_map(idx)
        rgb_color = color[:3]

        blocks = dim.get('dimension', [])

        # 基于 block_dimension 实际放置后的坐标绘制元件整体尺寸框
        draw_overall_dimension_box_from_blocks(
            blocks,
            pos,
            rotation_angle,
            rgb_color,
            alpha=0.15,
            element_mirrored=element_mirrored,
        )
        
        # 绘制 SMD 焊盘
        smd_blocks = [b for b in blocks if str(b.get('type', '')).lower() == 'smd']
        
        for block in smd_blocks:
            block_type = str(block.get('type', 'smd')).lower()
            corners = block_corners_global(block, pos, rotation_angle, element_mirrored)
            
            if block_type == 'smd':
                # SMD 焊盘：彩色填充
                draw_pad_shape(corners, pad_type='smd', color=rgb_color, alpha=0.7)
        
        # 标注元件名
        plt.text(pos[0], pos[1], name, fontsize=6, ha='center', va='center',
                color='black', weight='bold', 
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.7, edgecolor='none'))


def main(graph_json_path, output_folder):
    """
    从 graph JSON 文件读取数据并生成 PCB 布局图
    左侧显示 front (Regular, R 旋转)，右侧显示 back (Mirrored, MR 旋转)
    
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
    
    # Required input format: ic_dimension + ic_position.
    ic_dimension_list = data.get('ic_dimension', [])
    ic_dimensions = [
        {
            'element_name': d.get('element_name'),
            'dimension': d.get('block_dimension', []),
        }
        for d in ic_dimension_list
    ]
    
    board_boundary = data.get('board_boundary', {})
    dxf_details = board_boundary.get('dxf_details', [])

    # 计算图表范围
    all_points = []
    for seg in dxf_details:
        seg_type = seg.get('type')
        if seg_type == 'LINE':
            all_points.append(seg['start'])
            all_points.append(seg['end'])
        elif seg_type == 'ARC':
            all_points.append(seg['start'])
            all_points.append(seg['end'])

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
    draw_elements(ic_position, ic_dimensions, layer_filter='front')
    ax_front.set_xlim(x_min, x_max)
    ax_front.set_ylim(y_min, y_max)
    ax_front.set_aspect('equal', adjustable='box')
    ax_front.grid(True, which='both', axis='both', color='gray', linestyle='--', linewidth=0.3, alpha=0.5)
    ax_front.set_xlabel('X coordinate (mm)', fontsize=10)
    ax_front.set_ylabel('Y coordinate (mm)', fontsize=10)
    ax_front.set_title('PCB and SMD Positions (Regular)', fontsize=12, weight='bold')

    plt.sca(ax_back)
    draw_board_boundary(dxf_details, mirror_x=False, x_center=x_center)
    draw_elements(ic_position, ic_dimensions, layer_filter='back')
    ax_back.set_xlim(x_min, x_max)
    ax_back.set_ylim(y_min, y_max)
    ax_back.set_aspect('equal', adjustable='box')
    ax_back.grid(True, which='both', axis='both', color='gray', linestyle='--', linewidth=0.3, alpha=0.5)
    ax_back.set_xlabel('X coordinate (mm)', fontsize=10)
    ax_back.set_ylabel('Y coordinate (mm)', fontsize=10)
    ax_back.set_title('PCB and SMD Positions (Mirrored)', fontsize=12, weight='bold')

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
