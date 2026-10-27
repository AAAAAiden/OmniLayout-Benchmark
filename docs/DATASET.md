# Processed graph dataset

**Availability:** public dataset downloads are withdrawn pending source-license
and attribution review. This document describes the retained inventory and
schema, not a currently cleared public dataset. See [the license audit](LICENSE_AUDIT.md).

This release contains the processed PCB placement graphs derived from the original EAGLE board files. The original `.brd` files are excluded.

## Contents

The graph collection contains **1,681 JSON files**, totaling **187,642,615 bytes** before compression. Each file is named `<board_id>_graph.json`. Preserve the board ID, including spaces, punctuation, and `#` characters, when matching inputs and predictions.

The `v0.1.0` dataset archive, `omnilayout-graphs-v0.1.0.zip`, is 9,570,204 bytes (approximately 9.13 MiB). Its SHA-256 is recorded in `data/release.json` and the release's `SHA256SUMS.txt`. It contains:

```text
graph/
  <board_id>_graph.json
manifest.json
summary.json
LICENSE_DATA.md
```

The code repository also includes the manifest and summary under `data/` so the expected release contents can be inspected without downloading the graphs. The manifest records each file's `path`, `bytes`, `sha256`, `components`, `library_records`, `dimension_records`, `nets`, and `smds` under `files`; here, `components` counts `ic_position` entries. The summary records aggregate `position_records`, `library_records`, `dimension_records`, `net_records`, and `smd_records`, together with the graph count, total uncompressed bytes, top-level field names, and dataset version. Raw boards, local intermediate archives, generated model outputs, and machine-specific files are outside this dataset release.

## Verified counts

| Record | Count |
| --- | ---: |
| Board graphs | 1,681 |
| `ic_position` entries | 77,236 |
| `ic_library` entries | 55,762 |
| `ic_dimension` entries | 55,762 |
| Nets (`netlist` entries) | 55,830 |
| Surface-mount pad records (`smd`) | 168,815 |
| Through-hole pad records (`pads`) | 45,797 |
| Mechanical hole records (`holes`) | 5,845 |

These count records in their respective fields. In particular, the surface-mount pad count is separate from through-hole pads. The position, library, and dimension arrays do not have equal lengths. Match components using `element_name`, never by array index, and handle missing geometry explicitly.

The graph count matches the 1,681 designs described in the paper. The rounded figures 77.24K component placements, 168.82K surface-mount pads, and 55.83K nets correspond to the counts above. This release preserves the existing processed graphs; it does not reconstruct missing annotations.

## JSON structure

Every graph has five top-level fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `netlist` | array | Net names and their component/pad endpoints. |
| `board_boundary` | object | Board outline segments and equivalent drawing geometry. |
| `ic_library` | array | Component identity, package geometry, and reference placement information. |
| `ic_position` | array | Component reference positions and rotations. |
| `ic_dimension` | array | Simplified geometric blocks associated with components. |

### Connectivity

Each `netlist` entry contains a string `name` and a `contactref` array. Every contact reference identifies an `element` and a `pad` by name:

```json
{
  "name": "GND",
  "contactref": [
    {"element": "C1", "pad": "1"},
    {"element": "U1", "pad": "2"}
  ]
}
```

This is a net-based representation, not an explicit pairwise graph with edge weights. A net can connect more than two pads. Net and pad names are identifiers; preserve them as strings.

### Board boundary

`board_boundary` contains:

- `segment_details`: entries with `start`, `end`, and `layer`; curved segments can also have a numeric `curve`.
- `unit`: the length unit for the board geometry, `mm` in all 1,681 released graphs.
- `closed`: the stored outline-closure flag.
- `dxf_details`: drawing primitives such as lines and arcs. Depending on primitive type, entries can include endpoints, center, radius, start/end/sweep angles, direction, layer, and `source_index`.

Read the stored unit and geometry instead of assuming a rectangular board. A closure flag describes the stored annotation; it is not an independent geometric-validity guarantee.

### Component library geometry

Each `ic_library` entry includes `element_name`, `library_name`, `package_name`, `position`, `rotation`, `smd`, `pads`, `holes`, and `gt_draw_bbox`.

- `position` is the component's board-coordinate reference position.
- Pad and hole `position` values are local to the component/package. Apply the component transformation to obtain board coordinates.
- `smd` entries include pad `name`, local `position`, `width`, `length`, `rotation`, and `layer`. Optional EAGLE attributes can also be present, such as `roundness`, `stop`, `cream`, and `thermals`.
- `pads` entries include `name`, local `position`, `drill`, and `diameter`, with optional `rot` and `shape`.
- `holes` entries include local `position` and `drill`, with optional `locked`.
- `gt_draw_bbox` contains `front_bbox` and `back_bbox` in board coordinates, ordered `[xmin, ymin, xmax, ymax]`. An empty array indicates that no corresponding box is stored.

Retain the EAGLE rotation strings, including mirroring information, when reading or updating placement. Treat missing or null rotations according to the consuming tool rather than silently changing their interpretation.

### Reference placement

Each `ic_position` entry contains `element_name`, a two-number `position`, and `rotation`, which can be a string or null. These are the stored reference placements extracted from the source designs.

The reference graph also contains placement information in `ic_library`. When constructing a placement prediction task, exclude reference positions and any other reference-derived features from model input as required by the chosen evaluation protocol. The graph itself does not enforce that separation.

### Simplified dimension blocks

Each `ic_dimension` entry contains an `element_name` and a `block_dimension` array. Blocks describe package geometry with fields such as `name`, `type`, `width`, `length`, local `position`, `rotation`, and `layer`. Block types in this release are `smd`, `pad`, and `hole`. Block names can be null.

These blocks are a processed geometric representation. They do not replace a complete manufacturing model or an electrical design-rule check.

## Scope and limitations

- This is a **board-graph release**. Schematic images, schematic symbol/pin annotations, and schematic-to-board pairing tables are not bundled.
- Spatially weighted component/symbol connectivity views described in the broader project are not stored as separate weighted-edge annotations in these five-field graphs.
- No official train, validation, or test split is provided. Any new split should be versioned and documented, including how related source designs are separated.
- Counts of placement and geometry records differ. Consumers must check component coverage before computing metrics.
- The graphs preserve source-derived reference placements. They do not certify routability, electrical correctness, or manufacturing readiness.
- The code is MIT-licensed. Dataset licensing and per-design source attribution are separate, as described in `LICENSE_DATA.md`; this release does not assign a blanket license to the third-party hardware designs.

The release preparation checks parse every graph and verify the inventory. A scan of the graph fields found no embedded EAGLE XML or absolute source-file paths. Source-derived board IDs, library names, package names, net names, and pad names remain in the JSON.
