#!/usr/bin/env python
# coding: utf-8


import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Wedge, FancyBboxPatch
from matplotlib.transforms import Affine2D


def rotate_point(point, origin=None, angle=0):
	if origin is None:
		origin = [0, 0]
	radians = np.deg2rad(angle)
	ox, oy = origin
	x, y = point

	x_new = ox + np.cos(radians) * (x - ox) - np.sin(radians) * (y - oy)
	y_new = oy + np.sin(radians) * (x - ox) + np.cos(radians) * (y - oy)

	return [x_new, y_new]


def _sample_segment_points(segment, num_points=40):
	"""Sample straight or curved boundary segment into drawable points."""
	if isinstance(segment, dict):
		start = segment.get("start", [0.0, 0.0])
		end = segment.get("end", [0.0, 0.0])
		curve = float(segment.get("curve", 0.0) or 0.0)
	else:
		start = segment[0]
		end = segment[1]
		curve = 0.0

	x1, y1 = float(start[0]), float(start[1])
	x2, y2 = float(end[0]), float(end[1])

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

	mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
	nx, ny = -chord_dy / chord_len, chord_dx / chord_len
	center_sign = 1.0 if curve > 0 else -1.0
	cx = mx + center_sign * nx * h
	cy = my + center_sign * ny * h

	start_angle = np.arctan2(y1 - cy, x1 - cx)
	sweep = np.deg2rad(curve)
	steps = max(int(abs(curve) / 4), 8, num_points)
	angles = np.linspace(start_angle, start_angle + sweep, steps)

	return [[cx + radius * np.cos(a), cy + radius * np.sin(a)] for a in angles]


def capsule_points(cx, cy, w, h, n=32):
	"""Build a stadium (capsule) outline centered at (cx, cy).

	w: short-edge width (also the semicircle diameter)
	h: long-edge length along the local X axis
	"""
	r = w / 2
	straight = max(h - w, 0.0)

	pts = []

	# right semicircle
	for i in range(n + 1):
		t = math.pi * i / n
		x = cx + straight / 2 + r * math.sin(t)
		y = cy + r * math.cos(t)
		pts.append((x, y))

	# left semicircle
	for i in range(n + 1):
		t = math.pi + math.pi * i / n
		x = cx - straight / 2 + r * math.sin(t)
		y = cy + r * math.cos(t)
		pts.append((x, y))

	return pts


def _get_long_pad_length(width, pad):
	"""Compute long-pad main-axis length from optional elongation ratio.

	If XML provides elongation/ratio, use it; otherwise default to 2x width.
	"""
	elongation_ratio = 2.0
	for key in ("elongation", "ratio"):
		val = pad.get(key)
		if val is None:
			continue
		try:
			f = float(val)
		except (TypeError, ValueError):
			continue
		# Some formats store elongation as percent, e.g. 150 means 1.5x.
		elongation_ratio = f / 100.0 if f > 10 else f
		break

	if elongation_ratio < 1.0:
		elongation_ratio = 1.0
	return width * elongation_ratio


def octagon_points(cx, cy, size):
	"""Create a centered octagon by chamfering a square's corners equally.

	size: outer square size used as octagon external dimension reference.
	"""
	half = size / 2.0
	cut = size / (2.0 + math.sqrt(2.0))

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

def plot_pcb_and_elements(
	ax,
	border_segments,
	elements,
	ic_library,
	mirrored=False,
	legend=False,
	show_rectangles=True,
	show_bbox=False,
	show_gray_cover=True,
):
	if not border_segments:
		raise ValueError("No board border segments were found in the XML file.")

	border_paths = [_sample_segment_points(segment) for segment in border_segments]
	x_coords_border = [coord[0] for path in border_paths for coord in path]
	y_coords_border = [coord[1] for path in border_paths for coord in path]

	x_min, x_max = min(x_coords_border), max(x_coords_border)
	y_min, y_max = min(y_coords_border), max(y_coords_border)
	x_center = (x_min + x_max) / 2.0
	# Keep front/back in the same board coordinate system.
	# `mirrored` is used for side filtering, not geometric x-mirroring.
	mirror_coordinates = False

	def _mirror_x(x):
		return 2.0 * x_center - x

	def _draw_block_bbox(points, face_alpha=0.08, edge_alpha=0.45):
		"""Draw a light-black axis-aligned bbox from a list of points."""
		if not points:
			return
		xs = [p[0] for p in points]
		ys = [p[1] for p in points]
		xmin, xmax = min(xs), max(xs)
		ymin, ymax = min(ys), max(ys)
		rect = plt.Rectangle(
			(xmin, ymin),
			xmax - xmin,
			ymax - ymin,
			linewidth=0.7,
			edgecolor=(0.0, 0.0, 0.0, edge_alpha),
			facecolor=(0.0, 0.0, 0.0, face_alpha),
			zorder=5,
		)
		ax.add_patch(rect)

	if mirrored and mirror_coordinates:
		x_coords_border = [_mirror_x(x) for x in x_coords_border]
		border_paths = [[[_mirror_x(coord[0]), coord[1]] for coord in path] for path in border_paths]

	for path in border_paths:
		x_values = [point[0] for point in path]
		y_values = [point[1] for point in path]
		ax.plot(x_values, y_values, color="black", linestyle="-", linewidth=2, label="PCB Border")

	num_elements = max(len(elements), 1)
	color_map = plt.get_cmap("tab20", num_elements)

	for idx, element in enumerate(elements):
		ic = next(
			(
				item
				for item in ic_library
				if item.get("name") == element.get("package")
				and (
					element.get("library") is None
					or item.get("library") == element.get("library")
				)
			),
			None,
		)
		if ic is None and element.get("library") is not None:
			ic = next((item for item in ic_library if item.get("name") == element.get("package")), None)
		if ic is None:
			continue
		has_through_hole_pad = len(ic.get("pad", [])) > 0
		has_hole = len(ic.get("hole", [])) > 0
		
		# Check if this element has any SMDs on the current view's side
		has_smd_on_this_side = any(
			_smd_should_display_in_view(element.get("rot", "R0"), smd.get("layer"), mirrored)
			for smd in ic.get("smd", [])
		)
		# Always draw through-hole components (they appear on both sides)
		# Draw this component if it has SMDs on this side OR has through-hole features
		draw_bbox_this_side = has_smd_on_this_side or has_through_hole_pad or has_hole

		# 绘制 bbox（优先使用分面 bbox）
		side_bbox_key = "back_bbox" if mirrored else "front_bbox"
		bbox_group = element.get("gt_draw_bbox") if isinstance(element.get("gt_draw_bbox"), dict) else {}
		bbox = bbox_group.get(side_bbox_key)
		if not (isinstance(bbox, list) and len(bbox) == 4):
			bbox = element.get(side_bbox_key)
		if not (isinstance(bbox, list) and len(bbox) == 4):
			bbox = element.get("bbox")
		if bbox is None and isinstance(element.get("dimension"), list) and len(element.get("dimension")) == 4:
			# Backward compatibility for old dimension format [xmin, ymin, xmax, ymax].
			bbox = element.get("dimension")
		if show_bbox and draw_bbox_this_side and bbox is not None:
			xmin, ymin, xmax, ymax = bbox
			if mirrored and mirror_coordinates:
				rect_x = _mirror_x(xmax)
				rect_w = xmax - xmin
			else:
				rect_x = xmin
				rect_w = xmax - xmin
			rect = plt.Rectangle(
				(rect_x, ymin),
				rect_w,
				ymax - ymin,
				linewidth=1.5,
				edgecolor="red",
				facecolor="none",
				linestyle="--",
				alpha=0.9,
			)
			ax.add_patch(rect)

		element_pos_base = [float(coord) for coord in element.get("pos", [0.0, 0.0])]
		element_rotation_base = _parse_rot_deg(element.get("rot", "R0"))
		element_pos_plot = [element_pos_base[0], element_pos_base[1]]
		element_rotation_plot = element_rotation_base

		if mirrored and mirror_coordinates:
			element_pos_plot[0] = _mirror_x(element_pos_plot[0])
			element_rotation_plot = -element_rotation_plot

		# Component marker and SMD pads are only drawn on the element's owning side.
		if has_smd_on_this_side:
			circle = Circle(element_pos_plot, radius=0.05, color=color_map(idx), alpha=0.8)
			ax.add_patch(circle)
			element_name = str(element.get("name", "")).strip()
			if element_name:
				# Keep labels legible on dense plots by using a compact translucent tag.
				ax.text(
					element_pos_plot[0],
					element_pos_plot[1],
					element_name,
					ha="center",
					va="center",
					fontsize=6,
					fontweight="bold",
					color="white",
					bbox=dict(
						boxstyle="round,pad=0.12",
						fc=(color_map(idx)[0], color_map(idx)[1], color_map(idx)[2], 0.55),
						ec="none",
					),
					zorder=8,
				)

		for pad in ic.get("pad", []):
			pad_relative_pos = pad.get("pos", [0.0, 0.0])
			# Compute pad position in regular global coordinates first,
			# then mirror final X for mirrored view.
			pad_relative_rotated = rotate_point(pad_relative_pos, [0, 0], element_rotation_base)
			pad_pos = [
				pad_relative_rotated[0] + element_pos_base[0],
				pad_relative_rotated[1] + element_pos_base[1],
			]
			if mirrored and mirror_coordinates:
				pad_pos[0] = _mirror_x(pad_pos[0])
			pad_shape = str(pad.get("shape", "")).lower()
			pad_rot = _parse_rot_deg(pad.get("rot", "R0"))
			total_rot = element_rotation_base + pad_rot
			if mirrored and mirror_coordinates:
				total_rot = -total_rot
			drill_val = pad.get("drill")
			diameter_val = pad.get("diameter")
			if drill_val is not None:
				drill_radius = float(drill_val) / 2
				outer_radius = float(diameter_val) / 2 if diameter_val is not None else drill_radius + 0.25
				pad_bbox_points = []

				if pad_shape == "round" or pad_shape == "":
					if outer_radius > drill_radius > 0:
						pad_ring = Wedge(
							center=pad_pos,
							r=outer_radius,
							theta1=0,
							theta2=360,
							width=outer_radius - drill_radius,
							facecolor="blue",
							edgecolor="blue",
							alpha=0.6,
						)
						ax.add_patch(pad_ring)
						ax.add_patch(Circle(pad_pos, radius=max(drill_radius, 0.05), color="white", fill=True))
					else:
						ax.add_patch(Circle(pad_pos, radius=max(outer_radius, 0.05), fill=False, edgecolor="blue", linewidth=1.2))
					pad_bbox_points = [
						[pad_pos[0] - outer_radius, pad_pos[1] - outer_radius],
						[pad_pos[0] + outer_radius, pad_pos[1] - outer_radius],
						[pad_pos[0] + outer_radius, pad_pos[1] + outer_radius],
						[pad_pos[0] - outer_radius, pad_pos[1] + outer_radius],
					]
				elif pad_shape.startswith("oct"):
					size = max(float(diameter_val) if diameter_val is not None else float(drill_val) + 0.5, 0.1)
					oct_pts = octagon_points(pad_pos[0], pad_pos[1], size)
					oct_pts = [rotate_point(pt, pad_pos, total_rot) for pt in oct_pts]
					ax.add_patch(Polygon(oct_pts, facecolor="blue", edgecolor="blue", alpha=0.6))
					ax.add_patch(Circle(pad_pos, radius=max(drill_radius, 0.05), color="white", fill=True))
					pad_bbox_points = oct_pts
				elif pad_shape == "square":
					side = max(float(diameter_val) if diameter_val is not None else float(drill_val) + 0.5, 0.1)
					half = side / 2.0
					square_pts = [
						[pad_pos[0] - half, pad_pos[1] - half],
						[pad_pos[0] + half, pad_pos[1] - half],
						[pad_pos[0] + half, pad_pos[1] + half],
						[pad_pos[0] - half, pad_pos[1] + half],
					]
					square_pts = [rotate_point(pt, pad_pos, total_rot) for pt in square_pts]
					ax.add_patch(Polygon(square_pts, facecolor="blue", edgecolor="blue", alpha=0.6))
					ax.add_patch(Circle(pad_pos, radius=max(drill_radius, 0.05), color="white", fill=True))
					pad_bbox_points = square_pts
				elif pad_shape == "long":
					# Long pad: stadium copper shape, then drill hole at center.
					w = max(float(diameter_val) if diameter_val is not None else float(drill_val), 0.1)
					h = max(_get_long_pad_length(w, pad), w)
					capsule_pts = capsule_points(pad_pos[0], pad_pos[1], w, h, n=32)
					capsule_pts = [rotate_point(pt, pad_pos, total_rot) for pt in capsule_pts]
					ax.add_patch(Polygon(capsule_pts, facecolor="blue", edgecolor="blue", alpha=0.6))
					ax.add_patch(Circle(pad_pos, radius=max(drill_radius, 0.05), color="white", fill=True))
					pad_bbox_points = capsule_pts
				elif pad_shape == "offset":
					# Offset pad: 铜皮整体相对钻孔偏移，钻孔位置固定
					w = max(float(diameter_val) if diameter_val is not None else float(drill_val), 0.1)
					h = max(_get_long_pad_length(w, pad), w)
					margin = 0.08
					offset_dist = (h / 2.0) - (drill_radius) - margin
					# 胶囊铜皮中心点 = pad_pos 沿主轴负方向偏移 offset_dist
					dx = -offset_dist * math.cos(np.deg2rad(total_rot))
					dy = -offset_dist * math.sin(np.deg2rad(total_rot))
					capsule_center = [pad_pos[0] + dx, pad_pos[1] + dy]
					capsule_pts = capsule_points(capsule_center[0], capsule_center[1], w, h, n=32)
					capsule_pts = [rotate_point(pt, capsule_center, total_rot) for pt in capsule_pts]
					ax.add_patch(Polygon(capsule_pts, facecolor="blue", edgecolor="blue", alpha=0.6))
					# drill孔始终在 pad_pos
					ax.add_patch(Circle(pad_pos, radius=max(drill_radius, 0.05), color="white", fill=True))
					pad_bbox_points = capsule_pts
				elif outer_radius > drill_radius > 0:
					pad_ring = Wedge(
						center=pad_pos,
						r=outer_radius,
						theta1=0,
						theta2=360,
						width=outer_radius - drill_radius,
						facecolor="blue",
						edgecolor="blue",
						alpha=0.6,
					)
					ax.add_patch(pad_ring)
					pad_bbox_points = [
						[pad_pos[0] - outer_radius, pad_pos[1] - outer_radius],
						[pad_pos[0] + outer_radius, pad_pos[1] - outer_radius],
						[pad_pos[0] + outer_radius, pad_pos[1] + outer_radius],
						[pad_pos[0] - outer_radius, pad_pos[1] + outer_radius],
					]
				else:
					ax.add_patch(Circle(pad_pos, radius=max(outer_radius, 0.05), fill=False, edgecolor="blue", linewidth=1.2))
					r = max(outer_radius, 0.05)
					pad_bbox_points = [
						[pad_pos[0] - r, pad_pos[1] - r],
						[pad_pos[0] + r, pad_pos[1] - r],
						[pad_pos[0] + r, pad_pos[1] + r],
						[pad_pos[0] - r, pad_pos[1] + r],
					]

				if show_gray_cover:
					_draw_block_bbox(pad_bbox_points)

				pad_name = str(pad.get("name", "")).strip()
				if pad_name:
					ax.text(
						pad_pos[0],
						pad_pos[1],
						pad_name,
						fontsize=8,
						ha="center",
						va="center",
						color="black",
						bbox=dict(boxstyle="round,pad=0.08", fc=(1.0, 1.0, 0.85, 0.85), ec="none"),
						zorder=12,
					)

		for hole in ic.get("hole", []):
			hole_relative_pos = hole.get("pos", [0.0, 0.0])
			hole_local = [hole_relative_pos[0], hole_relative_pos[1]]
			# For MR/SMR elements, mirror local X first, then rotate in board coordinates.
			if _is_mirrored_rot(element.get("rot", "")):
				hole_local[0] = -hole_local[0]
			hole_relative_rotated = rotate_point(hole_local, [0, 0], element_rotation_base)
			hole_pos = [
				hole_relative_rotated[0] + element_pos_base[0],
				hole_relative_rotated[1] + element_pos_base[1],
			]
			if mirrored and mirror_coordinates:
				hole_pos[0] = _mirror_x(hole_pos[0])
			hole_drill = hole.get("drill")
			if hole_drill is None:
				continue
			hole_radius = max(float(hole_drill) / 2.0, 0.05)
			ax.add_patch(Circle(hole_pos, radius=hole_radius, facecolor="white", edgecolor="black", linewidth=1.0))
			hole_bbox_points = [
				[hole_pos[0] - hole_radius, hole_pos[1] - hole_radius],
				[hole_pos[0] + hole_radius, hole_pos[1] - hole_radius],
				[hole_pos[0] + hole_radius, hole_pos[1] + hole_radius],
				[hole_pos[0] - hole_radius, hole_pos[1] + hole_radius],
			]
			if show_gray_cover:
				_draw_block_bbox(hole_bbox_points)
			hole_name = str(hole.get("name", "")).strip()
			if hole_name:
				ax.text(
					hole_pos[0],
					hole_pos[1],
					hole_name,
					fontsize=8,
					ha="center",
					va="center",
					color="black",
					bbox=dict(boxstyle="round,pad=0.08", fc=(1.0, 1.0, 0.85, 0.85), ec="none"),
					zorder=12,
				)

		for smd in ic.get("smd", []):
			# Check if this SMD should be displayed in the current view
			if not _smd_should_display_in_view(element.get("rot", "R0"), smd.get("layer"), mirrored):
				continue

			smd_rotation = _parse_rot_deg(smd.get("rot", "R0"))
			smd_relative_pos = smd.get("pos", [0.0, 0.0])
			smd_wid_len = smd.get("wid_len", [0.1, 0.1])
			try:
				roundness = float(smd.get("roundness", 0) or 0)
			except (TypeError, ValueError):
				roundness = 0.0
			roundness = min(max(roundness, 0.0), 100.0)
			corner_radius = min(smd_wid_len[0], smd_wid_len[1]) * (roundness / 100.0) / 2.0

			# Use element color for all SMDs in this element
			smd_color = color_map(idx)
			
			smd_corners = [
				[-smd_wid_len[0] / 2, -smd_wid_len[1] / 2],
				[smd_wid_len[0] / 2, -smd_wid_len[1] / 2],
				[smd_wid_len[0] / 2, smd_wid_len[1] / 2],
				[-smd_wid_len[0] / 2, smd_wid_len[1] / 2],
			]

			smd_corners_rotated = [rotate_point(corner, [0, 0], smd_rotation) for corner in smd_corners]
			smd_corners_rotated = [
				[corner[0] + smd_relative_pos[0], corner[1] + smd_relative_pos[1]]
				for corner in smd_corners_rotated
			]
			smd_corners_final = [rotate_point(corner, [0, 0], element_rotation_plot) for corner in smd_corners_rotated]
			smd_corners_final = [
				[corner[0] + element_pos_plot[0], corner[1] + element_pos_plot[1]]
				for corner in smd_corners_final
			]

			smd_center_rotated = rotate_point(smd_relative_pos, [0, 0], element_rotation_plot)
			smd_center = [
				smd_center_rotated[0] + element_pos_plot[0],
				smd_center_rotated[1] + element_pos_plot[1],
			]
			total_smd_rotation = element_rotation_plot + smd_rotation

			if show_rectangles:
				if corner_radius > 0:
					rounded = FancyBboxPatch(
						(-smd_wid_len[0] / 2, -smd_wid_len[1] / 2),
						smd_wid_len[0],
						smd_wid_len[1],
						boxstyle=f"round,pad=0,rounding_size={corner_radius}",
						facecolor=smd_color[:3],
						edgecolor=smd_color[:3],
						linewidth=0,
						alpha=0.4,
					)
					rounded.set_transform(
						Affine2D()
						.rotate_deg(total_smd_rotation)
						.translate(smd_center[0], smd_center[1])
						+ ax.transData
					)
					ax.add_patch(rounded)
				else:
					polygon = Polygon(smd_corners_final, color=smd_color, alpha=0.4)
					ax.add_patch(polygon)
			else:
				ax.scatter(*smd_corners_final[0], color=smd_color[:3] if isinstance(smd_color, tuple) else smd_color, alpha=0.4)

			# Draw block dimension overlay after SMD with 95% transparency (alpha=0.05).
			if show_gray_cover:
				_draw_block_bbox(smd_corners_final, face_alpha=0.05, edge_alpha=0.12)

			smd_label_x = smd_center[0]
			smd_label_y = smd_center[1]
			ax.text(smd_label_x, smd_label_y, f"{smd.get('name', '')}", fontsize=8, ha="center", va="center", color="white")

	margin = 5.0
	ax.set_xlim(min(x_coords_border) - margin, max(x_coords_border) + margin)
	ax.set_ylim(y_min - margin, y_max + margin)
	ax.set_aspect("equal", adjustable="box")

	plot_type = "Mirrored" if mirrored else "Regular"
	ax.set_xlabel("X coordinate")
	ax.set_ylabel("Y coordinate")
	ax.set_title(f"PCB and SMD Positions ({plot_type})")
	ax.grid(True, which="both", axis="both", color="gray", linestyle="--", linewidth=0.5)

	ax.set_xticks(np.arange(min(x_coords_border) - margin, max(x_coords_border) + margin, 1))
	ax.set_yticks(np.arange(y_min - margin, y_max + margin, 1))

	if legend:
		ax.legend(loc="upper right", fontsize="small", markerscale=0.5)

def plot_all_elements(
	border_segments,
	elements,
	ic_library,
	view="both",
	legend=False,
	show_rectangles=True,
	show_bbox=False,
	show_gray_cover=True,
):
	"""Plot all elements with options to display mirrored, regular, or both elements."""
	num_plots = 2 if view == "both" else 1
	fig, axes = plt.subplots(num_plots, 1, figsize=(10, 8 * num_plots))

	if not isinstance(axes, np.ndarray):
		axes = [axes]

	if view in ["regular", "both"]:
		plot_pcb_and_elements(
			axes[0],
			border_segments,
			elements,
			ic_library,
			mirrored=False,
			legend=legend,
			show_rectangles=show_rectangles,
			show_bbox=show_bbox,
			show_gray_cover=show_gray_cover,
		)

	if view in ["mirrored", "both"]:
		plot_pcb_and_elements(
			axes[-1],
			border_segments,
			elements,
			ic_library,
			mirrored=True,
			legend=legend,
			show_rectangles=show_rectangles,
			show_bbox=show_bbox,
			show_gray_cover=show_gray_cover,
		)

	plt.tight_layout()
	return fig

def render_board_to_image(
	board_boundary,
	ic_dimensions,
	output_image_path=None,
	view="both",
	legend=False,
	show_rectangles=True,
	show_bbox=False,
	show_gray_cover=True,
):
	# Keep full segment details so curve segments can be rendered as arcs.
	border_segments = board_boundary.get("segment_details", [])
	# Convert ic_dimensions to elements/ic_library format
	elements = [
		{
			"name": ic["element_name"],
			"library": ic.get("library_name"),
			"pos": ic["position"],
			"package": ic["package_name"],
			"rot": ic.get("rotation", "R0"),
			"dimension": ic.get("dimension"),
			"gt_draw_bbox": ic.get(
				"gt_draw_bbox",
				{
					"front_bbox": ic.get("front_bbox", []),
					"back_bbox": ic.get("back_bbox", []),
				},
			),
		}
		for ic in ic_dimensions
	]
	# Build ic_library (group by package_name)
	ic_library = {}
	for ic in ic_dimensions:
		pkg = ic["package_name"]
		lib = ic.get("library_name")
		pkg_key = (lib, pkg)
		if pkg_key not in ic_library:
			ic_library[pkg_key] = {
				"library": lib,
				"name": pkg,
				"smd": [],
				"pad": [],
				"hole": [],
			}
		smd_items = ic.get("smd", ic.get("smd_pads", []))
		for smd in smd_items:
			smd_entry = {
				"name": smd["name"],
				"pos": smd["position"],
				"wid_len": [smd["width"], smd["length"]],
				"rot": smd["rotation"],
			}
			for key, value in smd.items():
				if key in ("name", "position", "width", "length", "rotation"):
					continue
				smd_entry[key] = value
			ic_library[pkg_key]["smd"].append(smd_entry)
		for pad in ic.get("pads", ic.get("pad", [])):
			pad_entry = {
				"name": pad["name"],
				"pos": pad["position"],
			}
			if "drill" in pad:
				pad_entry["drill"] = pad.get("drill")
			if "diameter" in pad:
				pad_entry["diameter"] = pad.get("diameter")
			if "shape" in pad:
				pad_entry["shape"] = pad.get("shape")
			if "rot" in pad:
				pad_entry["rot"] = pad.get("rot")
			ic_library[pkg_key]["pad"].append(pad_entry)
		for hole in ic.get("holes", ic.get("hole", [])):
			hole_entry = {
				"pos": hole["position"],
			}
			if "drill" in hole:
				hole_entry["drill"] = hole.get("drill")
			for key, value in hole.items():
				if key in ("position", "drill"):
					continue
				hole_entry[key] = value
			ic_library[pkg_key]["hole"].append(hole_entry)
	ic_library_list = list(ic_library.values())
	fig = plot_all_elements(
		border_segments=border_segments,
		elements=elements,
		ic_library=ic_library_list,
		view=view,
		legend=legend,
		show_rectangles=show_rectangles,
		show_bbox=show_bbox,
		show_gray_cover=show_gray_cover,
	)
	if output_image_path is not None:
		fig.savefig(output_image_path, dpi=300, bbox_inches="tight")
		plt.close(fig)
		return output_image_path
	return fig


def xml_to_dict(element):
	"""Recursively convert an XML element and its children into a dictionary."""
	result = {element.tag: {} if element.attrib else None}

	if element.attrib:
		result[element.tag].update((key, value) for key, value in element.attrib.items())

	if element.text and element.text.strip():
		text = element.text.strip()
		if result[element.tag]:
			result[element.tag]["text"] = text
		else:
			result[element.tag] = text

	children = list(element)
	if children:
		child_dict = {}
		for child in children:
			child_result = xml_to_dict(child)
			if child.tag in child_dict:
				if isinstance(child_dict[child.tag], list):
					child_dict[child.tag].append(child_result[child.tag])
				else:
					child_dict[child.tag] = [child_dict[child.tag], child_result[child.tag]]
			else:
				child_dict.update(child_result)
		if result[element.tag]:
			result[element.tag].update(child_dict)
		else:
			result[element.tag] = child_dict

	return result


def as_list(value):
	if value is None:
		return []
	return value if isinstance(value, list) else [value]


def get_wire(wire):
	return [
		[float(wire.get("x1")), float(wire.get("y1"))],
		[float(wire.get("x2")), float(wire.get("y2"))],
	]


def get_wires(elements):
	return [get_wire(element) for element in as_list(elements)]


def get_package_wire_detail(wire):
	curve_raw = wire.get("curve")
	curve_val = float(curve_raw) if curve_raw is not None else 0.0
	return {
		"start": [float(wire.get("x1")), float(wire.get("y1"))],
		"end": [float(wire.get("x2")), float(wire.get("y2"))],
		"layer": str(wire.get("layer", "")),
		"curve": curve_val,
	}


def get_package_wires(elements):
	return [get_package_wire_detail(element) for element in as_list(elements)]


def get_smd(ic_smd):
	smd_names = []
	for smd in as_list(ic_smd):
		tmp_smd = {
			"name": smd.get("name"),
			"pos": [float(smd.get("x")), float(smd.get("y"))],
			"wid_len": [float(smd.get("dx")), float(smd.get("dy"))],
			"rot": smd.get("rot", "R0"),
		}
		for key, value in smd.items():
			if key in ("name", "x", "y", "dx", "dy", "rot"):
				continue
			tmp_smd[key] = value
		smd_names.append(tmp_smd)
	return smd_names


def get_pad(ic_pad):
	pads = []
	for pad in as_list(ic_pad):
		drill_val = float(pad.get("drill")) if pad.get("drill") is not None else None
		shape_val = pad.get("shape")
		diameter_val = float(pad.get("diameter")) if pad.get("diameter") is not None else None
		if shape_val is not None and diameter_val is None and drill_val is not None:
			# For shaped through-hole pads, infer a usable outer diameter.
			diameter_val = drill_val + 2.0 * 0.25

		tmp_pad = {
			"name": pad.get("name"),
			"pos": [float(pad.get("x")), float(pad.get("y"))],
		}
		if drill_val is not None:
			tmp_pad["drill"] = drill_val
		if diameter_val is not None:
			tmp_pad["diameter"] = diameter_val
		if shape_val is not None:
			tmp_pad["shape"] = shape_val
		if pad.get("rot") is not None:
			tmp_pad["rot"] = pad.get("rot")
		pads.append(tmp_pad)
	return pads


def get_hole(ic_hole):
	holes = []
	for hole in as_list(ic_hole):
		drill_val = float(hole.get("drill")) if hole.get("drill") is not None else None
		tmp_hole = {
			"pos": [float(hole.get("x")), float(hole.get("y"))],
		}
		if drill_val is not None:
			tmp_hole["drill"] = drill_val
		for key, value in hole.items():
			if key in ("x", "y", "drill"):
				continue
			tmp_hole[key] = value
		holes.append(tmp_hole)
	return holes


def get_one_ic(pack, library_name):
	return {
		"name": pack.get("name"),
		"library": library_name,
		"wire": get_wires(pack.get("wire", [])),
		"wire_detail": get_package_wires(pack.get("wire", [])),
		"smd": get_smd(pack.get("smd", [])),
		"pad": get_pad(pack.get("pad", [])),
		"hole": get_hole(pack.get("hole", [])),
	}


def get_ic_info(list_library):
	ics = []
	for library in as_list(list_library):
		packages = library.get("packages", {}).get("package", [])
		for pack in as_list(packages):
			ics.append(get_one_ic(pack, library.get("name")))
	return ics


def get_element_info(list_element):
	elements = []
	for ele in as_list(list_element):
		element = {
			"name": ele.get("name"),
			"library": ele.get("library"),
			"pos": [float(ele.get("x")), float(ele.get("y"))],
			"package": ele.get("package"),
		}
		if ele.get("rot") is not None:
			element["rot"] = ele.get("rot")
		elements.append(element)
	return elements


def get_signal_info(list_signal):
	"""Extract netlist from signals."""
	signals = []
	for sig in as_list(list_signal):
		signal = {
			"name": sig.get("name"),
			"contactref": [],
		}
		if "contactref" in sig:
			for contact in as_list(sig.get("contactref")):
				contactref = {
					"element": contact.get("element"),
					"pad": contact.get("pad"),
				}
				signal["contactref"].append(contactref)
		signals.append(signal)
	return signals


def _parse_rot_deg(rot):
	rot = (rot or "R0").upper()
	if rot.startswith(("MR", "SMR")):
		return -float(rot.lstrip("MSR"))
	if rot.startswith(("R", "S")):
		return float(rot.lstrip("SR"))
	return 0.0


def _is_mirrored_rot(rot):
	return str(rot or "").upper().startswith(("MR", "SMR"))


def _is_smd_on_back_side(element_rot, smd_layer):
	"""Resolve SMD board side from element mirror state and package SMD layer."""
	layer_str = str(smd_layer if smd_layer is not None else "1")
	is_bottom_local = layer_str == "16"
	if _is_mirrored_rot(element_rot):
		return not is_bottom_local
	return is_bottom_local


def _smd_should_display_in_view(element_rot, smd_layer, is_mirrored_view):
	"""Check if SMD should be displayed in the current view.
	
	Regular view (is_mirrored_view=False):
		- Shows SMDs where: (R* AND layer=1) OR (MR* AND layer=16)
	
	Mirrored view (is_mirrored_view=True):
		- Shows SMDs where: (MR* AND layer=1) OR (R* AND layer=16)
	"""
	element_is_mirrored = _is_mirrored_rot(element_rot)
	layer_str = str(smd_layer if smd_layer is not None else "1")
	is_layer_16 = layer_str == "16"
	is_layer_1 = layer_str == "1"
	
	if is_mirrored_view:
		# Mirrored view: (MR* AND layer=1) OR (R* AND layer=16)
		return (element_is_mirrored and is_layer_1) or (not element_is_mirrored and is_layer_16)
	else:
		# Regular view: (R* AND layer=1) OR (MR* AND layer=16)
		return (not element_is_mirrored and is_layer_1) or (element_is_mirrored and is_layer_16)


def _transform_local_point_to_board(local_point, element):
	"""Transform package-local point into board coordinates for an element instance."""
	local_x = float(local_point[0])
	local_y = float(local_point[1])
	is_mirrored = _is_mirrored_rot(element.get("rot", ""))
	if is_mirrored:
		local_x = -local_x
	el_rot = _parse_rot_deg(element.get("rot", "R0"))
	rotated = rotate_point([local_x, local_y], [0, 0], el_rot)
	el_pos = element.get("pos", [0.0, 0.0])
	return [rotated[0] + el_pos[0], rotated[1] + el_pos[1]]


def get_ic_layer20_boundary(elements, ic_library):
	"""Collect board-boundary segments from IC package wires on layer 20."""
	boundary_segments = []
	element_map = {(ic.get("library"), ic.get("name")): ic for ic in ic_library}
	fallback_map = {ic.get("name"): ic for ic in ic_library}

	for element in elements:
		package_name = element.get("package")
		ic_package = element_map.get((element.get("library"), package_name))
		if ic_package is None:
			ic_package = fallback_map.get(package_name)
		if ic_package is None:
			continue

		for wire in as_list(ic_package.get("wire_detail", [])):
			if str(wire.get("layer", "")) != "20":
				continue

			start = _transform_local_point_to_board(wire.get("start", [0.0, 0.0]), element)
			end = _transform_local_point_to_board(wire.get("end", [0.0, 0.0]), element)
			curve_val = float(wire.get("curve", 0.0) or 0.0)
			if _is_mirrored_rot(element.get("rot", "")):
				curve_val = -curve_val

			boundary_segments.append(
				{
					"start": start,
					"end": end,
					"layer": "20",
					"curve": curve_val,
				}
			)

	return boundary_segments


def convert_segment_details_to_cad_entities(segment_details, flip_y=False, y_center=None):
	"""Convert Eagle-style segment_details to DXF-like CAD entities.
	
	Args:
		segment_details: List of segments with start, end, curve, layer
		flip_y: If True, flip Y coordinates (for coordinate system conversion)
		y_center: Center Y coordinate for flipping. If None, auto-calculate from data
	
	Returns:
		List of CAD entities (LINE or ARC) with explicit geometric parameters
	"""
	if not segment_details:
		return []
	
	# Calculate y_center if flip_y is enabled but y_center not provided
	if flip_y and y_center is None:
		all_ys = []
		for seg in segment_details:
			start_y = seg.get("start", [0, 0])[1]
			end_y = seg.get("end", [0, 0])[1]
			all_ys.extend([start_y, end_y])
		if all_ys:
			y_center = (min(all_ys) + max(all_ys)) / 2.0
		else:
			y_center = 0.0
	
	dxf_details = []
	
	# Keep a strict 1:1 mapping with segment_details in the same iteration order.
	for segment_idx, segment in enumerate(segment_details):
		start = list(segment.get("start", [0.0, 0.0]))
		end = list(segment.get("end", [0.0, 0.0]))
		curve = float(segment.get("curve", 0.0) or 0.0)
		layer = segment.get("layer", "20")

		# 修正负角度的arc，保证和Eagle渲染一致
		if curve < 0:
			start, end = end, start
			curve = -curve

		# Flip Y coordinates if needed
		if flip_y and y_center is not None:
			start[1] = 2.0 * y_center - start[1]
			end[1] = 2.0 * y_center - end[1]

		if abs(curve) < 1e-9:
			# Straight line
			entity = {
				"type": "LINE",
				"start": start,
				"end": end,
				"layer": layer,
				"source_index": segment_idx,
			}
			dxf_details.append(entity)
		else:
			# Arc segment - use same logic as _sample_segment_points
			x1, y1 = float(start[0]), float(start[1])
			x2, y2 = float(end[0]), float(end[1])
			sweep_angle_deg = curve

			# If Y is flipped, sweep angle direction is reversed
			if flip_y:
				sweep_angle_deg = -sweep_angle_deg

			sweep_angle_rad = np.deg2rad(sweep_angle_deg)
			
			# 计算弦长
			chord_dx = x2 - x1
			chord_dy = y2 - y1
			chord_len = np.hypot(chord_dx, chord_dy)
			
			if chord_len < 1e-9:
				# Preserve source order/count even for degenerate segments.
				entity = {
					"type": "LINE",
					"start": start,
					"end": end,
					"layer": layer,
					"source_index": segment_idx,
				}
				dxf_details.append(entity)
				continue
			
			# 计算半角
			half_theta = abs(sweep_angle_rad) / 2.0
			sin_half_theta = np.sin(half_theta)
			
			if abs(sin_half_theta) < 1e-12:
				# Essentially a line
				entity = {
					"type": "LINE",
					"start": start,
					"end": end,
					"layer": layer,
					"source_index": segment_idx,
				}
				dxf_details.append(entity)
				continue
			
			# 计算圆弧的半径
			radius = chord_len / (2.0 * sin_half_theta)
			
			# 计算圆心 (using same logic as _sample_segment_points)
			mx = (x1 + x2) / 2.0
			my = (y1 + y2) / 2.0
			nx = -chord_dy / chord_len
			ny = chord_dx / chord_len
			
			# Keep the sign of cos(theta/2) so major arcs pick the correct side.
			h = radius * np.cos(sweep_angle_rad / 2.0)
			
			# 根据 sweep angle 的符号选择圆心
			center_sign = 1.0 if sweep_angle_deg > 0 else -1.0
			cx = mx + center_sign * nx * h
			cy = my + center_sign * ny * h
			
			# 计算 start_angle (弧度，范围 [-π, π])
			start_angle_rad = np.arctan2(y1 - cy, x1 - cx)
			
			# end_angle = start_angle + sweep_angle (弧度)
			end_angle_rad = start_angle_rad + sweep_angle_rad
			
			# 转换为度数（保持角度关系）
			start_angle_deg = np.rad2deg(start_angle_rad)
			end_angle_deg = np.rad2deg(end_angle_rad)
			
			# Keep the raw angles without normalization to preserve the sweep direction
			# matplotlib Arc needs to know which direction to sweep, so we must maintain
			# the relationship between start_angle and end_angle
			entity = {
				"type": "ARC",
				"start": start,
				"end": end,
				"center": [cx, cy],
				"radius": abs(radius),
				"start_angle": start_angle_deg,
				"end_angle": end_angle_deg,
				"sweep_angle": sweep_angle_deg,
				"clockwise": sweep_angle_deg < 0,
				"layer": layer,
				"source_index": segment_idx,
			}
			dxf_details.append(entity)
	
	return dxf_details


def get_board_boundary(board_dimension, elements=None, ic_library=None):
	"""Extract board boundary segments from plain (layer 20) and package layer 20 outlines."""
	boundary_segments_detailed = []
	all_points = []

	# 兼容 board_dimension 既可能是 list 也可能是 dict（如 {'circle': {...}, 'wire': [...], ...}）
	plain_objs = []
	if isinstance(board_dimension, dict):
		for k, v in board_dimension.items():
			if isinstance(v, list):
				for obj in v:
					if isinstance(obj, dict):
						obj = obj.copy()
						obj["_tag"] = k
						plain_objs.append(obj)
			elif isinstance(v, dict):
				obj = v.copy()
				obj["_tag"] = k
				plain_objs.append(obj)
	else:
		for obj in as_list(board_dimension):
			plain_objs.append(obj)

	for item in plain_objs:
		# xml_to_dict 可能解析成 {'circle': {...}} 或 {'polygon': {...}} 或 {'wire': {...}}，也可能直接是 dict
		# 统一展开
		if isinstance(item, dict) and len(item) == 1 and list(item.keys())[0] in ("wire", "circle", "polygon", "dimension"):
			key = list(item.keys())[0]
			val = item[key]
			if isinstance(val, dict):
				val = val.copy()
				val["_tag"] = key
			item = val
		# Ignore Eagle <dimension> objects on layer 20; they are annotations, not board outline.
		if str(item.get("_tag", "")).lower() == "dimension":
			continue
		# 只处理 layer=20
		if str(item.get("layer", "")) != "20":
			continue
		# wire
		if "x1" in item and "y1" in item and "x2" in item and "y2" in item:
			start = [float(item.get("x1")), float(item.get("y1"))]
			end = [float(item.get("x2")), float(item.get("y2"))]
			curve_raw = item.get("curve")
			curve_val = float(curve_raw) if curve_raw is not None else 0.0
			boundary_segments_detailed.append({
				"start": start,
				"end": end,
				"layer": "20",
				"curve": curve_val,
			})
			if abs(curve_val) < 1e-9:
				all_points.extend([start, end])
			else:
				pts = _sample_segment_points({"start": start, "end": end, "curve": curve_val}, num_points=20)
				all_points.extend(pts)
			continue
		# circle
		if ("x" in item and "y" in item and "radius" in item):
			cx = float(item.get("x"))
			cy = float(item.get("y"))
			r = float(item.get("radius"))
			nseg = 64
			pts = [
				[cx + r * np.cos(2 * np.pi * i / nseg), cy + r * np.sin(2 * np.pi * i / nseg)]
				for i in range(nseg)
			]
			for i in range(nseg):
				seg = {
					"start": pts[i],
					"end": pts[(i + 1) % nseg],
					"layer": "20",
					"curve": 0.0,
				}
				boundary_segments_detailed.append(seg)
				all_points.extend([pts[i], pts[(i + 1) % nseg]])
			continue
		# polygon
		if "vertex" in item:
			vertices = as_list(item["vertex"])
			if len(vertices) < 2:
				continue
			pts = [[float(v["x"]), float(v["y"])] for v in vertices]
			for i in range(len(pts)):
				seg = {
					"start": pts[i],
					"end": pts[(i + 1) % len(pts)],
					"layer": "20",
					"curve": float(vertices[i].get("curve", 0.0) or 0.0),
				}
				boundary_segments_detailed.append(seg)
				all_points.extend([pts[i], pts[(i + 1) % len(pts)]])
			continue

	# Include layer-20 wires from element package outlines as board boundary too.
	if elements is not None and ic_library is not None:
		for seg in get_ic_layer20_boundary(elements, ic_library):
			boundary_segments_detailed.append(seg)
			curve_val = float(seg.get("curve", 0.0) or 0.0)
			start = seg.get("start", [0.0, 0.0])
			end = seg.get("end", [0.0, 0.0])
			if abs(curve_val) < 1e-9:
				all_points.extend([start, end])
			else:
				pts = _sample_segment_points({"start": start, "end": end, "curve": curve_val}, num_points=20)
				all_points.extend(pts)

	if not boundary_segments_detailed or not all_points:
		return {"segment_details": []}

	xs = [p[0] for p in all_points]
	ys = [p[1] for p in all_points]
	xmin, xmax = min(xs), max(xs)
	ymin, ymax = min(ys), max(ys)
	bbox = {
		"xmin": xmin,
		"ymin": ymin,
		"xmax": xmax,
		"ymax": ymax,
		"width": xmax - xmin,
		"height": ymax - ymin
	}
	y_center = (ymin + ymax) / 2.0
	dxf_details = convert_segment_details_to_cad_entities(boundary_segments_detailed, flip_y=False, y_center=y_center)

	# Check if the outline is closed
	closed = False
	if dxf_details and len(dxf_details) > 0:
		first_start = dxf_details[0].get("start")
		last_end = dxf_details[-1].get("end")
		if first_start and last_end:
			if abs(first_start[0] - last_end[0]) < 1e-6 and abs(first_start[1] - last_end[1]) < 1e-6:
				closed = True

	return {
		"segment_details": boundary_segments_detailed,
		"unit": "mm",
		"closed": closed,
		"dxf_details": dxf_details,
	}


def get_ic_dimensions(elements, ic_library):
	"""Extract IC dimensions for each element with positions and rotations.

	Includes:
	- SMD-only components
	- PAD-only (through-hole) components
	- symbol/mechanical components with no pads (kept with empty pad list)
	"""
	ic_dimensions = []
	gt_position_list = []
	element_map = {(ic.get("library"), ic.get("name")): ic for ic in ic_library}
	fallback_map = {ic.get("name"): ic for ic in ic_library}
	for element in elements:
		package_name = element.get("package")
		# Collect ic_position info
		gt_position_list.append({
			"element_name": element.get("name"),
			"position": element.get("pos"),
			"rotation": element.get("rot")
		})
		ic_package = element_map.get((element.get("library"), package_name))
		if ic_package is None:
			ic_package = fallback_map.get(package_name)
		if ic_package is None:
			continue
		smd_list = ic_package.get("smd", [])
		pad_list = ic_package.get("pad", [])
		hole_list = ic_package.get("hole", [])
		if not smd_list and not pad_list and not hole_list:
			# Skip non-physical packages that have no drawable copper/drill features.
			continue
		is_mirrored = _is_mirrored_rot(element.get("rot", ""))
		has_through_hole_or_hole = (len(pad_list) > 0) or (len(hole_list) > 0)
		dimensions = {
			"element_name": element.get("name"),
			"library_name": element.get("library"),
			"package_name": package_name,
			"position": element.get("pos"),  # Global position on board
			"smd": [],
			"pads": [],
			"holes": [],
			"gt_draw_bbox": {
				"front_bbox": [],
				"back_bbox": [],
			},
		}
		if element.get("rot") is not None:
			dimensions["rotation"] = element.get("rot")  # Global rotation
		# 收集所有smd和pad的全局坐标点
		all_points = []
		all_points_front = []
		all_points_back = []
		block_dimensions = []
		# SMD
		for smd in smd_list:
			smd_dim = {
				"name": smd.get("name"),
				"position": smd.get("pos"),  # Relative position in IC coordinate system
				"width": smd.get("wid_len")[0] if smd.get("wid_len") else 0,
				"length": smd.get("wid_len")[1] if smd.get("wid_len") else 0,
				"rotation": smd.get("rot"),  # Pad rotation
			}
			for key, value in smd.items():
				if key in ("name", "pos", "wid_len", "rot"):
					continue
				smd_dim[key] = value
			dimensions["smd"].append(smd_dim)
			# Determine SMD layer: layer 1 = front, layer 16 = back
			smd_layer = smd.get("layer", "1")
			smd_layer_str = "back" if str(smd_layer) == "16" else "front"
			block_dimensions.append(
				{
					"name": smd_dim.get("name"),
					"type": "smd",
					"width": float(smd_dim.get("width", 0.0) or 0.0),
					"length": float(smd_dim.get("length", 0.0) or 0.0),
					"position": smd_dim.get("position", [0.0, 0.0]),
					"rotation": smd_dim.get("rotation", "R0") or "R0",
					"layer": smd_layer_str,
				}
			)
			# 计算全局四角
			w, l = smd_dim["width"], smd_dim["length"]
			local_corners = [
				[-w/2, -l/2], [w/2, -l/2], [w/2, l/2], [-w/2, l/2]
			]
			pad_rot = _parse_rot_deg(smd_dim["rotation"])
			# 先局部旋转
			rotated = [rotate_point(pt, [0,0], pad_rot) for pt in local_corners]
			# 平移到元件局部坐标
			shifted = [[pt[0]+smd_dim["position"][0], pt[1]+smd_dim["position"][1]] for pt in rotated]
			# 再整体旋转
			el_rot = _parse_rot_deg(element.get("rot", "R0"))
			final = [rotate_point(pt, [0,0], el_rot) for pt in shifted]
			# 平移到全局
			el_pos = element.get("pos", [0,0])
			final = [[pt[0]+el_pos[0], pt[1]+el_pos[1]] for pt in final]
			all_points.extend(final)
			# Determine if SMD is on back side for bbox calculation
			if _smd_should_display_in_view(element.get("rot", "R0"), smd.get("layer"), True):
				all_points_back.extend(final)
			else:
				all_points_front.extend(final)
		# PAD
		for pad in pad_list:
			pad_dim = {
				"name": pad.get("name"),
				"position": pad.get("pos"),
			}
			if pad.get("drill") is not None:
				pad_dim["drill"] = pad.get("drill")
			if pad.get("diameter") is not None:
				pad_dim["diameter"] = pad.get("diameter")
			if pad.get("shape") is not None:
				pad_dim["shape"] = pad.get("shape")
			if pad.get("rot") is not None:
				pad_dim["rot"] = pad.get("rot")
			dimensions["pads"].append(pad_dim)
			# 计算pad外形全局点
			shape = str(pad.get("shape", "")).lower()
			diam = float(pad.get("diameter") or pad.get("drill") or 0)
			pad_block_width = 0.0
			pad_block_length = 0.0
			center = pad.get("pos", [0,0])
			pad_rot = _parse_rot_deg(pad.get("rot", "R0"))
			el_rot = _parse_rot_deg(element.get("rot", "R0"))
			el_pos = element.get("pos", [0,0])

			def _pad_points_to_global(local_points):
				# 先按 pad 旋转（局部坐标原点），再平移到 pad 中心，
				# 最后按元件旋转并平移到全局。
				rot_pad = [rotate_point(pt, [0, 0], pad_rot) for pt in local_points]
				shift_pad = [[pt[0] + center[0], pt[1] + center[1]] for pt in rot_pad]
				rot_ele = [rotate_point(pt, [0, 0], el_rot) for pt in shift_pad]
				return [[pt[0] + el_pos[0], pt[1] + el_pos[1]] for pt in rot_ele]

			# 先局部建模，再做坐标变换
			if shape in ("", "round"):
				# 圆pad，取正方形外接框
				r = diam / 2
				pad_block_width = diam
				pad_block_length = diam
				local = [[-r, -r], [r, -r], [r, r], [-r, r]]
				final = _pad_points_to_global(local)
				all_points.extend(final)
			elif shape == "square":
				s = diam
				pad_block_width = s
				pad_block_length = s
				half = s / 2
				local = [[-half, -half], [half, -half], [half, half], [-half, half]]
				final = _pad_points_to_global(local)
				all_points.extend(final)
			elif shape.startswith("oct"):
				size = diam
				pad_block_width = size
				pad_block_length = size
				oct_pts = octagon_points(0.0, 0.0, size)
				final = _pad_points_to_global(oct_pts)
				all_points.extend(final)
			elif shape == "long":
				w = diam
				h = max(_get_long_pad_length(w, pad), w)
				pad_block_width = h
				pad_block_length = w
				capsule = capsule_points(0.0, 0.0, w, h, n=8)
				final = _pad_points_to_global(capsule)
				all_points.extend(final)
			elif shape == "offset":
				w = diam
				h = max(_get_long_pad_length(w, pad), w)
				pad_block_width = h
				pad_block_length = w
				margin = 0.08
				drill_radius = float(pad.get("drill") or 0) / 2
				offset_dist = (h / 2) - drill_radius - margin
				capsule_center_local = [-offset_dist, 0.0]
				capsule = capsule_points(capsule_center_local[0], capsule_center_local[1], w, h, n=8)
				final = _pad_points_to_global(capsule)
				all_points.extend(final)

			if pad_block_width <= 0.0:
				pad_block_width = diam
			if pad_block_length <= 0.0:
				pad_block_length = diam
			block_dimensions.append(
				{
					"name": pad_dim.get("name"),
					"type": "pad",
					"width": float(pad_block_width),
					"length": float(pad_block_length),
					"position": pad_dim.get("position", [0.0, 0.0]),
					"rotation": pad_dim.get("rot", "R0") or "R0",
					"layer": "through",
				}
			)

		# HOLE
		for hole in hole_list:
			hole_dim = {
				"position": hole.get("pos"),
			}
			if hole.get("drill") is not None:
				hole_dim["drill"] = hole.get("drill")
			for key, value in hole.items():
				if key in ("pos", "drill"):
					continue
				hole_dim[key] = value
			dimensions["holes"].append(hole_dim)
			block_dimensions.append(
				{
					"name": hole_dim.get("name"),
					"type": "hole",
					"width": float(hole_dim.get("drill", 0.0) or 0.0),
					"length": float(hole_dim.get("drill", 0.0) or 0.0),
					"position": hole_dim.get("position", [0.0, 0.0]),
					"layer": "through",
				}
			)

			center = hole.get("pos", [0, 0])
			# Keep hole bbox geometry consistent with plotting for MR/SMR elements:
			# mirror local X before element rotation.
			if str(element.get("rot", "")).upper().startswith(("MR", "SMR")):
				center = [-center[0], center[1]]
			r = float(hole.get("drill") or 0) / 2.0
			if r <= 0:
				continue
			local = [
				[center[0] - r, center[1] - r],
				[center[0] + r, center[1] - r],
				[center[0] + r, center[1] + r],
				[center[0] - r, center[1] + r],
			]
			el_rot = _parse_rot_deg(element.get("rot", "R0"))
			el_pos = element.get("pos", [0, 0])
			rot = [rotate_point(pt, [0, 0], el_rot) for pt in local]
			final = [[pt[0] + el_pos[0], pt[1] + el_pos[1]] for pt in rot]
			all_points.extend(final)
		# 计算bbox
		if all_points:
			xs = [p[0] for p in all_points]
			ys = [p[1] for p in all_points]
			xmin, ymin, xmax, ymax = min(xs), min(ys), max(xs), max(ys)
			bbox = [xmin, ymin, xmax, ymax]

			def _bbox_from_points(points):
				if not points:
					return []
				px = [p[0] for p in points]
				py = [p[1] for p in points]
				return [min(px), min(py), max(px), max(py)]

			def _bbox_dimension(bbox_value):
				if not (isinstance(bbox_value, list) and len(bbox_value) == 4):
					return None
				return {
					"width": bbox_value[2] - bbox_value[0],
					"length": bbox_value[3] - bbox_value[1],
				}

			if has_through_hole_or_hole:
				dimensions["gt_draw_bbox"]["front_bbox"] = list(bbox)
				dimensions["gt_draw_bbox"]["back_bbox"] = list(bbox)
			else:
				dimensions["gt_draw_bbox"]["front_bbox"] = _bbox_from_points(all_points_front)
				dimensions["gt_draw_bbox"]["back_bbox"] = _bbox_from_points(all_points_back)

			front_bbox = dimensions["gt_draw_bbox"].get("front_bbox", [])
			back_bbox = dimensions["gt_draw_bbox"].get("back_bbox", [])
			if has_through_hole_or_hole:
				overall_bbox = list(bbox)
			elif front_bbox and not back_bbox:
				overall_bbox = front_bbox
			elif back_bbox and not front_bbox:
				overall_bbox = back_bbox
			elif front_bbox and back_bbox:
				overall_bbox = [
					min(front_bbox[0], back_bbox[0]),
					min(front_bbox[1], back_bbox[1]),
					max(front_bbox[2], back_bbox[2]),
					max(front_bbox[3], back_bbox[3]),
				]
			else:
				overall_bbox = list(bbox)

			dimensions["dimension"] = block_dimensions
		else:
			dimensions["dimension"] = block_dimensions
			dimensions["gt_draw_bbox"] = {
				"front_bbox": [],
				"back_bbox": [],
			}
		ic_dimensions.append(dimensions)
	return ic_dimensions, gt_position_list


def process_board_file(file_path):
	"""Parse Eagle XML board file and extract netlist, boundary, and IC dimensions."""
	tree = ET.parse(file_path)
	root = tree.getroot()
	xml_dict = xml_to_dict(root)


	board = xml_dict.get("eagle", {}).get("drawing", {}).get("board", {})
	plain = board.get("plain", {})

	board_library = board.get("libraries", {}).get("library", [])
	board_element = board.get("elements", {}).get("element", [])
	board_signal = board.get("signals", {}).get("signal", [])

	# Extract IC library
	ic_library = get_ic_info(board_library)

	# Extract elements
	elements = get_element_info(board_element)

	# Extract netlist from signals
	netlist = get_signal_info(board_signal)

	# Extract board boundary (board plain + IC package layer-20 wires)
	board_boundary = get_board_boundary(plain, elements=elements, ic_library=ic_library)

	# Extract IC dimensions and ic_position
	ic_dimensions, ic_position = get_ic_dimensions(elements, ic_library)

	board_info = {
		"netlist": netlist,
		"board_boundary": board_boundary,
		"ic_dimensions": ic_dimensions,
		"ic_position": ic_position,
	}

	return board_info


def _process_single_file(
	xml_file_path,
	output_folder_path=None,
	draw_img=False,
	show_bbox=False,
	show_gray_cover=True,
):
	"""
	Process a single XML board file.
	
	Args:
		xml_file_path: Path to the Eagle XML PCB file
		output_folder_path: Optional folder path to save JSON output
	
	Returns:
		Dictionary containing netlist, board boundary, and IC dimensions
	"""
	xml_path = Path(xml_file_path)
	
	board_info = process_board_file(xml_file_path)
	
	# Save to JSON file if output folder is provided
	if output_folder_path:
		output_folder = Path(output_folder_path)
		graph_folder = output_folder / "graph"
		img_folder = output_folder / "img"
		graph_folder.mkdir(parents=True, exist_ok=True)
		img_folder.mkdir(parents=True, exist_ok=True)

		# Generate output filename based on XML filename
		xml_stem = xml_path.stem  # Get filename without extension
		output_filename = f"{xml_stem}_graph.json"
		output_path = graph_folder / output_filename

		# Convert numpy types to standard Python types for JSON serialization
		def convert_to_serializable(obj):
			if isinstance(obj, np.integer):
				return int(obj)
			elif isinstance(obj, np.floating):
				return float(obj)
			elif isinstance(obj, dict):
				return {k: convert_to_serializable(v) for k, v in obj.items()}
			elif isinstance(obj, (list, tuple)):
				return [convert_to_serializable(item) for item in obj]
			return obj

		# Reorganize ic_dimensions into ic_library and dimension for output
		ic_library_list = []
		ic_dimension_list = []
		for ic in board_info["ic_dimensions"]:
			# Extract dimension info
			dim_data = {
				"element_name": ic.get("element_name"),
				"block_dimension": ic.get("dimension", [])
			}
			ic_dimension_list.append(dim_data)
			
			# Create ic_library entry without dimension
			ic_lib = {k: v for k, v in ic.items() if k != "dimension"}
			ic_library_list.append(ic_lib)

		# Create output structure with ic_library and ic_dimension at same level as ic_position
		output_info = {
			"netlist": board_info["netlist"],
			"board_boundary": board_info["board_boundary"],
			"ic_library": ic_library_list,
			"ic_position": board_info["ic_position"],
			"ic_dimension": ic_dimension_list,
		}

		serializable_info = convert_to_serializable(output_info)

		with open(output_path, "w") as f:
			json.dump(serializable_info, f, indent=2)
		print(f"\nData saved to: {output_path}")

		# Draw image if requested
		if draw_img:
			# 命名为“原文件名_img.png”，如“Adafruit 24LC32 I2C EEPROM##1_img.png”
			output_img_path = img_folder / f"{xml_path.stem}_img.png"
			try:
				render_board_to_image(
					board_info["board_boundary"],
					board_info["ic_dimensions"],
					output_image_path=output_img_path,
					view="both",
					legend=False,
					show_rectangles=True,
					show_bbox=show_bbox,
					show_gray_cover=show_gray_cover,
				)
				print(f"Image saved to: {output_img_path}")
			except Exception as e:
				raise RuntimeError(f"Image output failed for {xml_path.name}: {e}") from e
			if not output_img_path.exists():
				raise RuntimeError(f"Image output failed for {xml_path.name}: {output_img_path} was not created")

	return board_info


def main(
	xml_file_path,
	output_folder_path=None,
	draw_img=False,
	show_bbox=False,
	show_gray_cover=True,
	start_from_file=None,
):
	"""
	Main function to process Eagle XML file(s) and extract board information.
	
	Args:
		xml_file_path: Path to the Eagle XML PCB file OR folder containing .brd files
		output_folder_path: Optional folder path to save the extracted data as JSON.
							If provided, output filename is automatically generated from XML filename.
		start_from_file: Optional filename to resume folder batch processing from.
	
	Returns:
		Dictionary containing netlist, board boundary, and IC dimensions (for single file)
		OR None (for folder batch processing)
	"""
	xml_path = Path(xml_file_path)
	
	if not xml_path.exists():
		raise FileNotFoundError(f"File not found: {xml_file_path}")
	
	# Handle folder input (batch processing)
	if xml_path.is_dir():
		brd_files = sorted(path for path in xml_path.iterdir() if path.is_file() and path.suffix.lower() == ".brd")
		if not brd_files:
			return None

		if start_from_file:
			start_name = Path(start_from_file).name
			start_index = next((i for i, p in enumerate(brd_files) if p.name == start_name), None)
			if start_index is None:
				raise FileNotFoundError(f"Start file not found in folder: {start_name}")
			print(f"Resuming from: {brd_files[start_index].name} ({start_index + 1}/{len(brd_files)})")
			brd_files = brd_files[start_index:]

		total = len(brd_files)
		errors = []
		success_count = 0
		for idx, brd_file in enumerate(brd_files, start=1):
			print(f"[{idx}/{total}] Processing: {brd_file.name}")
			try:
				_process_single_file(
					str(brd_file),
					output_folder_path,
					draw_img=draw_img,
					show_bbox=show_bbox,
					show_gray_cover=show_gray_cover,
				)
				success_count += 1
			except Exception as e:
				error_msg = str(e)
				print(f"  ✗ Error: {error_msg}")
				errors.append({
					"file": brd_file.name,
					"path": str(brd_file),
					"error": error_msg,
				})
				continue

		if errors:
			print(f"\nBatch completed with {len(errors)} error(s) and {success_count} success(es).")
			if output_folder_path is not None:
				errors_path = Path(output_folder_path) / "batch_errors.json"
				with open(errors_path, "w", encoding="utf-8") as f:
					json.dump(errors, f, indent=2, ensure_ascii=False)
				print(f"Batch errors saved to: {errors_path}")
		return None

	# Handle single file input
	return _process_single_file(
		xml_file_path,
		output_folder_path,
		draw_img=draw_img,
		show_bbox=show_bbox,
		show_gray_cover=show_gray_cover,
	)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Convert locally held EAGLE XML boards to graph JSON.")
    parser.add_argument("input", help="A .brd file or directory containing .brd files")
    parser.add_argument("--output-dir", required=True, help="Writes graph/ and optional img/ subdirectories")
    parser.add_argument("--draw-img", action="store_true")
    parser.add_argument("--show-bbox", action="store_true")
    parser.add_argument("--no-gray-cover", action="store_true")
    parser.add_argument("--start-from", help="Optional filename to resume a directory conversion")
    args = parser.parse_args()
    main(args.input, args.output_dir, draw_img=args.draw_img,
         show_bbox=args.show_bbox, show_gray_cover=not args.no_gray_cover,
         start_from_file=args.start_from)
