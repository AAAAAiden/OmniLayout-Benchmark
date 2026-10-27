# Dataset licensing and provenance

## Current distribution status

As of 2026-10-26, the graph dataset release is temporarily withdrawn and retained
as an unpublished draft while source licenses and attribution are reviewed.
The software repository remains public. The downloader blocks public downloads
before making a network request. Existing copies and previously issued download
URLs cannot be recalled from recipients.

See `docs/LICENSE_AUDIT.md`, `data/provenance.json`, and
`data/provenance_summary.json` for the audit inventory and evidence status.

The software license in `LICENSE` applies to the project code. It does not
relicense the PCB designs or the processed graph dataset.

The graph files were derived from third-party EAGLE PCB designs. A complete,
per-design mapping of source URLs, copyright holders, applicable licenses, and
required attribution has not yet been supplied with this release. Original
design names are retained in the graph filenames for identification.

No blanket dataset license is asserted here. Each source design's applicable
terms, copyright holders, license versions, and attribution requirements must be
identified before that design is cleared for redistribution. Filename matches,
brand names, public repository visibility, and license notices for embedded
component libraries do not independently establish board-level permission.

CC BY 4.0 may be considered for original annotations or metadata owned by the
OmniLayout authors where upstream terms permit it. This notice does not apply
CC BY 4.0 to source-derived graphs or change existing rights. Converting a
source board to JSON does not itself establish permission to relicense it.

Future release candidates must record the upstream source revision, applicable
license, required attribution and change notices, and the review outcome for
every included graph. Designs with unresolved rights stay excluded. Any new
archive should be versioned and carry the appropriate source notices.

The retained dataset archive contains processed graph JSON only. Original
`.brd` and `.sch` files are excluded. Its existing licensing notice is preserved
for review; the current audit status in this repository supersedes any earlier
description of the archive as publicly available.
