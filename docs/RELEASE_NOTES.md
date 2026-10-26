# Release notes

## Initial public release scope

This repository presents the project's reusable code with the processed board-graph dataset distributed as a separate download.

The `v0.1.0` dataset comprises 1,681 existing `*_graph.json` files totaling 187,642,615 uncompressed bytes. The ZIP download is 9,570,204 bytes (approximately 9.13 MiB). The release preserves graph contents and names. Inventory and integrity metadata accompany the dataset in `manifest.json` and `summary.json`; the archive also contains `LICENSE_DATA.md`.

Original EAGLE `.brd` files are excluded. Local experiment outputs, model responses, raw-data archives, cache files, notebooks containing local execution state, credentials, and machine-specific diagnostics are outside the public release.

## Dataset boundaries

The initial dataset release supplies PCB geometry, reference placements, package/pad geometry, and net connectivity. It does not include schematic images or schematic symbol annotations, a published train/validation/test partition, or a separate spatially weighted graph collection. See [the dataset documentation](DATASET.md) for field definitions and exact record counts.

The 1,681 graphs match the paper's design count. The paper's broader schematic-coupled benchmark description should not be read as a claim that every project artifact is included in this initial graph-only release.

## Reproducibility

Use the supplied download and integrity checks to obtain the graph collection. Keep the original graph IDs when writing predictions so evaluation can match each prediction to its reference graph. Record the code revision, dataset release/version, model identifier, prompt settings, and evaluation selection when reporting results.

The existing processed graph representation has unequal placement and geometry coverage: 77,236 `ic_position` entries versus 55,762 entries in each of `ic_library` and `ic_dimension`. Evaluation code and downstream users must account for that difference explicitly.

## Licensing and attribution

The code is released under the MIT License. Dataset distribution rights and upstream hardware attribution are separate; consult `LICENSE_DATA.md` for the current dataset status. An official citation entry and evaluation split are not supplied in this release.
