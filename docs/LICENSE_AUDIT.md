# Dataset source and license audit

## Distribution status

The graph dataset download was withdrawn on 2026-10-26 while its sources,
licenses, and attribution requirements are reviewed. The `v0.1.0` release is an
unpublished draft. Raw `.brd` and `.sch` files remain private; no raw source is
added to the software repository. The code remains available under MIT.

The initial public archive was published before the source-by-source audit was
complete. The previous dataset notice did not establish redistribution rights.
The draft archive is retained for review; making it a draft cannot recall files
or temporary download links that recipients already received.

## Evidence inventory

### Initial audit findings, 2026-10-26

| Measure | Graphs |
| --- | ---: |
| Graph IDs inventoried | 1,681 |
| Matching local board and schematic filenames available | 1,681 |
| At least one official upstream source lead recorded | 1,173 |
| Local raw board matches an upstream Git blob exactly | 415 |
| No official upstream source lead established yet | 508 |
| Multiple distinct local board revisions need resolution | 27 |
| Cleared for redistribution | **0** |

These counts overlap. The 415 exact raw-source matches are a subset of the
1,173 source leads. They establish matching raw file contents, not reproduction
of the converted graph or clearance to distribute it. The remaining source
leads include structural and filename candidates with explicitly weaker status.

The original collection log is unavailable. Local `sources.txt` records document
processing lineage for 1,643 graphs, but do not supply a complete record of
upstream permissions. Embedded-library-only notice signals occur for 617
graphs and are not treated as licenses for the full board.

Upstream declarations include CC BY-SA 3.0, CC BY-SA 4.0, and CERN-OHL-P 2.0;
some sources have competing declarations or no unambiguous hardware license.
For example, the pinned [Adafruit SMT Breakout notice](https://github.com/adafruit/Adafruit-SMT-Breakout-PCBs/blob/dbd7a1928c2b668a9edf3d53277063eac4c3e938/license.txt)
identifies CC BY-SA 3.0, while the pinned [ProtoCentral ADS1262 notice](https://github.com/Protocentral/ProtoCentral_ads1262/blob/b4806e264269c2ed8650c22b3543dad286cdf78b/LICENSE.md)
assigns CERN-OHL-P v2 to hardware and separate terms to software/documentation.
These examples are source evidence, not a license grant for the collection.

`data/provenance.json` contains one record for each of the 1,681 graph IDs.
`data/provenance_summary.json` reports coverage and unresolved review states.
The graph hashes and inventory in `data/manifest.json` continue to identify the
processed files. Audit evidence is metadata only, not permission to redistribute.

Local source file matches, upstream source matches, and license clearance are
separate review steps. An exact upstream file hash is stronger source evidence
than a matching filename, but neither grants a new license by itself. Embedded
component-library notices are scoped to those libraries and cannot be treated
as a blanket license for a board design.

Detailed upstream evidence is recorded in
[`data/license_evidence_adafruit.json`](../data/license_evidence_adafruit.json)
and [`data/license_evidence_other_sources.json`](../data/license_evidence_other_sources.json).
The [summary](../data/provenance_summary.json) and
[per-graph inventory](../data/provenance.json) link those records to the graph
hashes. Source license candidates in those evidence files are not newly
assigned licenses for the extracted graphs.

### Reproduce or extend the inventory

Run the local scan against a private working copy that contains `datasets/`:

```sh
python scripts/audit_dataset_provenance.py --source-root /path/to/private/working-copy
```

The script records hashes, safe relative paths, normalized notice signals, and
public URLs. It does not copy raw designs. After updating upstream evidence,
refresh the links and summary without rescanning private source files:

```sh
python scripts/audit_dataset_provenance.py --refresh-external-evidence
```

The upstream evidence records are a snapshot of repository research, with
source revisions and file hashes. They require review and maintenance when
sources or release membership change. The scan does not automatically grant
rights or mark a graph cleared for publication.

## Requirements before a graph is included in a future release

1. Trace the graph to a specific locally processed source version and resolve
   conflicting source copies.
2. Record its authoritative upstream repository or download URL and the exact
   source path and revision. Record the strength of the source match.
3. Verify the license applicable to that board design, including the version,
   copyright holders, attribution, and any ShareAlike obligations. Distinguish
   hardware terms from firmware or embedded-library terms.
4. Prepare the required source notices and an accurate description of the
   extraction and processing changes. Do not replace upstream attribution with
   a citation to this project.
5. Record the review outcome. Unresolved designs remain excluded. A collection
   license must not contradict the retained source terms.
6. Build a newly versioned archive with its cleared membership, provenance,
   notices, and checksums, then explicitly approve publication.

## License policy

The project does not currently assert a blanket dataset license. The intent is
to retain the applicable source license for source-derived material. CC BY 4.0
may be considered for original material the authors own and can license, with
that scope clearly identified. No new data license is granted by this audit.

Creative Commons explains that providers must establish the rights needed to
license database contents, and that license conditions can continue to apply
when material is converted to another format:

- [Applying a CC legal tool to a database](https://creativecommons.org/faq/#how-do-i-apply-a-cc-legal-tool-to-a-database)
- [Using CC-licensed material in a different format](https://creativecommons.org/faq/#can-i-take-a-cc-licensed-work-and-use-it-in-a-different-format)
- [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
