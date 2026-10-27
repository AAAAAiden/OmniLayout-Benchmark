# OmniLayout Benchmark

Processed PCB placement graphs and code for preprocessing, rendering, model-based placement, and geometric evaluation.

**Dataset downloads are temporarily withdrawn pending a source-by-source license and attribution review.** The code remains public under MIT. See the [license audit](docs/LICENSE_AUDIT.md) for the current status and review requirements.

The dataset inventory contains **1,681 graph JSON files** derived from EAGLE board designs. The intended data release contains processed graphs; original `.brd` files are excluded. Any future dataset distribution will be separate from the code and limited to designs with documented redistribution permission and required notices.

## Install

Use Python 3.10 or later:

```sh
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in Windows PowerShell or `source .venv/bin/activate` on macOS/Linux. Then install the base dependencies:

```sh
python -m pip install -r requirements.txt
```

For model-based placement, install the optional provider dependencies:

```sh
python -m pip install -r requirements-llm.txt
```

## Dataset availability

The `v0.1.0` dataset release has been converted to an unpublished draft. There is currently no public dataset download. The command below reports the review status and exits before making a network request:

```sh
python scripts/download_dataset.py
```

If you already hold an archive and have the necessary rights to use it, the offline integrity-checking path remains available:

```sh
python scripts/download_dataset.py --archive /path/to/omnilayout-graphs-v0.1.0.zip
```

Use `--output` to choose a different extraction directory. Local verification checks the archive and graph hashes; it does not establish redistribution permission. The code repository retains the inventory and summary under [`data/`](data/) so the review can be tracked.

The withheld archive is `omnilayout-graphs-v0.1.0.zip`, **9,570,204 bytes** (approximately 9.13 MiB). [`data/release.json`](data/release.json) records its hash, size, and withheld status. The download URL is disabled until the review is complete.

## Render and evaluate a graph

The following commands illustrate the graph schema using an existing local dataset file. They require your own lawfully obtained graph data while public downloads are withdrawn:

```sh
python omnilayout.py render "datasets/graphs/graph/0.5mm 4-pin##1_graph.json" --output-dir output/rendered
python omnilayout.py evaluate "datasets/graphs/graph/0.5mm 4-pin##1_graph.json" --output output/reference_metrics.json
```

Without `--prediction`, evaluation uses the reference placement as a smoke check. To evaluate a generated placement, supply its JSON:

```sh
python omnilayout.py evaluate "datasets/graphs/graph/0.5mm 4-pin##1_graph.json" --prediction "output/placements/json/0.5mm 4-pin##1_graph.json" --output output/prediction_metrics.json
```

Evaluation reports HPWL, net crossings, net separation, normalized overlap, runtime, and out-of-boundary measurements. Reference designs can themselves have nonzero geometric metrics; evaluating a reference against itself is not a claim of a perfect manufactured design.

Run `python omnilayout.py --help` or add `--help` after a subcommand for the available options.

## Generate a placement

Configure credentials for the selected provider in your environment:

| Provider | Environment variable |
| --- | --- |
| `gemini` | `GEMINI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |

Then run:

```sh
python omnilayout.py place "datasets/graphs/graph/0.5mm 4-pin##1_graph.json" --provider gemini --model MODEL_ID --output-dir output/placements
```

The alternative provider is `openrouter`. Model identifiers are supplied explicitly so experiments can record the actual model used. These commands call the selected external API and require the corresponding account and credentials.

## Process a private board

You can run the preprocessing code on your own EAGLE XML board file:

```sh
python omnilayout.py preprocess /path/to/private.brd --output-dir output/preprocessed
```

Add `--draw-img` to request a drawing during preprocessing. Processing a private source file locally does not add that file to the released dataset.

## Dataset scope

Each graph contains `netlist`, `board_boundary`, `ic_library`, `ic_position`, and `ic_dimension`. Across the collection, there are 77,236 position records, 55,830 nets, and 168,815 surface-mount pad records. The uncompressed graph files occupy 187,642,615 bytes, approximately 179 MiB.

Placement and geometry arrays have different coverage: `ic_library` and `ic_dimension` each contain 55,762 entries. Join entries by component name and check coverage before computing metrics.

This initial release contains the board graphs. Schematic images, schematic symbol annotations, schematic-to-board pairing tables, separate spatially weighted graph annotations, and official train/validation/test splits are not included. See [`docs/DATASET.md`](docs/DATASET.md) for the field guide and [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md) for release boundaries.

## Licensing

The code is available under the [MIT License](LICENSE). Dataset rights are separate; see [`LICENSE_DATA.md`](LICENSE_DATA.md) for the dataset status. No blanket license is asserted over the third-party hardware designs from which the processed annotations were derived.
