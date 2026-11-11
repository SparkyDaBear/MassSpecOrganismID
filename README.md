# MassSpecOrganismID

A standalone, non-Nextflow slice of the [HAMLET](https://github.com/CompOmics/HAMLET) pipeline that answers one
question for a given PRIDE project (PXD accession): **which organism(s) produced this mass spec data?**

The pipeline has two stages, run as two separate Python scripts:

1. **[src/FetchPXD.py](src/FetchPXD.py)** — downloads a PXD's `.raw` files from PRIDE, converts them to `.mzML`
   (via ThermoRawFileParser), runs [RunAssessor](https://github.com/RunAssessor/RunAssessor) to characterize each
   run, and determines whether the data is DDA or DIA (`detected_params.json`).
2. **[src/OrganismID.py](src/OrganismID.py)** — runs de novo peptide sequencing (Casanovo for DDA, Cascadia for
   DIA — chosen automatically from `detected_params.json`), filters high-confidence peptides, and runs
   [Peptonizer2000](submodules/peptonizer2000) against frequency-ranked taxid blocks to predict the source organism(s).

## Repository layout

| Path | Purpose |
|---|---|
| `src/FetchPXD.py` | Stage 1: fetch + convert + RunAssessor + acquisition-type detection |
| `src/OrganismID.py` | Stage 2: de novo sequencing + Peptonizer2000 organism prediction |
| `src/determine_acquisition_params.py` | Decides DIA vs DDA from RunAssessor/LLM/PRIDE evidence |
| `src/PipelineLogger.py`, `src/pmc_client.py`, `src/cascadia_wrapper.py` | Shared helper modules |
| `submodules/peptonizer2000/` | [Peptonizer2000](https://github.com/unipept/Peptonizer2000) Snakemake workflow (taxonomic inference) |
| `submodules/runassessor/` | [RunAssessor](https://github.com/RunAssessor/RunAssessor) CLI used by `FetchPXD.py` |
| `patches/peptonizer2000/pandas_compat.patch` | Small compatibility patch applied to the Peptonizer2000 submodule (see below) |
| `assets/taxid_lists/AllPRIDEtaxids.csv` | Frequency-ranked taxid blocks queried by Peptonizer2000 |
| `assets/taxonomy/{nodes.dmp,names.dmp}` | NCBI taxonomy dumps (species-level de-duplication) — **not in git**, fetched by `setup.sh` |
| `assets/casanovo_cpu.config`, `assets/casanovo_gpu_4_3.config` | Casanovo 4.3.0-compatible configs for CPU-only vs GPU nodes |
| `assets/casanovo_gpu.config` | Casanovo 5.x-compatible GPU config — only use if your `casanovo_env` has casanovo 5.x, not 4.3.0 |
| `assets/cascadia.ckpt` | Pretrained Cascadia model checkpoint — **not in git**, fetched by `setup.sh` |
| `data/FASTA/UniversalContaminats.fasta` | Contaminant FASTA used to filter de novo peptides |
| `data/taxid_prediction.csv` | Full benchmark: predicted vs. ground-truth taxid for 4,114 raw files across 1,324 PXDs |
| `data/organism_id_failure_cohorts.csv` | The non-perfect subset of the above (1,203 rows / 452 PXDs), categorized by failure mode — **start here** to pick PXDs to test/tune on (see dedicated section below) |
| `conda_envs/` | `.yml` definitions for the three conda environments this pipeline needs |
| `src/setup.sh` | One-shot bootstrap: submodules, patch, conda envs, `peptonizer` install, large-asset downloads |

## Large assets not stored in git

Three files exceed GitHub's 100 MB per-file limit and are **gitignored**, not committed:

| File | Size | Source |
|---|---|---|
| `assets/taxonomy/nodes.dmp` | ~219 MB | [NCBI taxonomy FTP](https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz) (`taxdump.tar.gz`, extracted) |
| `assets/taxonomy/names.dmp` | ~298 MB | same archive as above |
| `assets/cascadia.ckpt` | ~558 MB | [Cascadia's public Google Drive folder](https://drive.google.com/drive/folders/1UTrZIrCdUqYqscbqga_KdX8kc8ZjMMfr) |

`src/setup.sh` fetches all three automatically (NCBI taxonomy via `curl`; the Cascadia checkpoint via `gdown`,
installed into `meti_env` if needed). Pass `--skip-large-assets` to skip this and fetch them manually later —
the exact commands setup.sh would have run are printed as a fallback if a download fails. Missing `nodes.dmp`/
`names.dmp` degrades (doesn't block) organism ID: `OrganismID.py` falls back to simple taxid counting without
species-level de-duplication. A missing `cascadia.ckpt` only matters for DIA (Cascadia) runs.

## Quick start: `setup.sh`

For a fresh clone, the fastest path is:

```bash
git clone --recurse-submodules <this-repo-url>
cd MassSpecOrganismID
./src/setup.sh --envs-dir /path/with/real/free/space/envs
```

This initializes the submodules, applies the Peptonizer2000 patch, creates all three conda environments,
pip-installs `peptonizer` into `meti_env`, and downloads the large gitignored assets (NCBI taxonomy dumps from
NCBI's own FTP; the Cascadia model checkpoint via `gdown` from its public Google Drive folder — see **Large
assets not stored in git** below). It's idempotent — safe to re-run if a step fails partway through. Run
`./src/setup.sh --help` for all options (`--resolved` for pinned versions, `--only-meti` to reuse existing
casanovo/cascadia envs, `--skip-envs`/`--skip-submodules`/`--skip-large-assets` to do just part of it, etc.).

The rest of this section documents what `setup.sh` does manually, in case you need to debug a step or prefer to
run it by hand.

## Cloning this repo

Peptonizer2000 and RunAssessor are git submodules (external projects maintained upstream, not vendored copies),
so clone recursively:

```bash
git clone --recurse-submodules <this-repo-url>
# or, if already cloned:
git submodule update --init --recursive
```

Peptonizer2000's Python package needs one small, tracked compatibility patch (two files, fixing a pandas
Arrow-backed-dtype assignment error) applied on top of the upstream checkout:

```bash
cd submodules/peptonizer2000 && git apply ../../patches/peptonizer2000/pandas_compat.patch && cd ../..
```

Then install it (editable, so it tracks the patched submodule in place) into `meti_env` — no PYTHONPATH tricks,
no `PEPTONIZER2000_HOME` export needed, and nothing is vendored into this repo's own git history:

```bash
/path/to/envs/meti_env/bin/pip install --no-deps -e submodules/peptonizer2000/peptonizer
```

(`--no-deps` because `meti_env.yml` already provides everything `peptonizer`'s `setup.py` requires: numpy, scipy,
networkx, pandas, rbo, matplotlib, requests.)

`OrganismID.py`'s `--peptonizer_home` flag defaults to `submodules/peptonizer2000/` relative to the repo root, so
the Snakefile/scripts are found automatically — override it only if you keep the submodule somewhere else.

### Peptide filtering and taxonomy patches

Setup also applies `unipept_nontryptic_species_rollup.patch` and
`pride_genus_rollup.patch`, in that order after `pandas_compat.patch`. For an
existing environment, apply these two additional patches in the Peptonizer2000
submodule before running organism identification:

```bash
git -C submodules/peptonizer2000 apply ../../patches/peptonizer2000/unipept_nontryptic_species_rollup.patch
git -C submodules/peptonizer2000 apply ../../patches/peptonizer2000/pride_genus_rollup.patch
```

Unipept lookup now permits nontryptic matches. Taxonomy filtering expands query
taxids to their requested-rank ancestors and can recover unmatched peptide taxa
at that rank. OrganismID additionally passes PRIDE taxids through
`PEPTONIZER_GENUS_ROLLUP_TAXA`: when exactly one PRIDE target at the requested rank
exists within a genus, peptide matches from that genus can also support that
target. This is metadata-guided species assignment, not independent species-level
peptide evidence; account for it when benchmarking predictions against PRIDE.

Contaminant matching treats I and L as equivalent. For samples whose PRIDE taxids
do not explicitly include `9606`, it also removes peptides matching anywhere in
skin/hair contaminant sequences, including the additional
`data/FASTA/SkinHairContaminants.fasta` next to the selected contaminants FASTA.
For PRIDE-listed human samples, this extra substring rule is disabled; the
standard tryptic contaminant filter still applies.

Run the focused offline regression tests in `meti_env`:

```bash
python -m unittest discover -s tests -p 'test_organism_id_pr_changes.py' -v
```

## Prerequisites: three conda environments

Casanovo and Cascadia (the two de novo sequencing tools) have **mutually incompatible** PyTorch/Lightning version
requirements (Casanovo needs `torch>=2.1`+, Cascadia needs `torch<2.1`), so they cannot be installed into the same
environment. This pipeline therefore uses **three** separate conda environments, matching HAMLET's own setup:

| Env | Used by | Installs |
|---|---|---|
| `meti_env` | `FetchPXD.py`, `OrganismID.py` orchestration, RunAssessor, snakemake/Peptonizer2000 | pandas, pyteomics, requests, snakemake, aria2, wget, ThermoRawFileParser, pypdf, `peptonizer` (pip, editable from the submodule), etc. |
| `casanovo_env` | Casanovo (DDA de novo sequencing) | `casanovo` + pinned `torch`/`lightning` |
| `cascadia_env` | Cascadia (DIA de novo sequencing) | `cascadia` + pinned `torch`/`lightning` |

`.yml` definitions are provided in [conda_envs/](conda_envs):
- `meti_env.yml`, `casanovo_env.yml`, `cascadia_env.yml` — minimal, version-flexible definitions (let conda/pip
  resolve the latest compatible versions).
- `*_resolved.yml` — exact pinned snapshots exported from a known-working install (`conda env export`), for fully
  reproducible builds.

### Creating the environments

```bash
conda env create -f conda_envs/meti_env.yml -p /path/to/envs/meti_env
conda env create -f conda_envs/casanovo_env.yml -p /path/to/envs/casanovo_env
conda env create -f conda_envs/cascadia_env.yml -p /path/to/envs/cascadia_env
```

Use the `*_resolved.yml` files instead of the plain ones if you want the exact versions known to work rather than
whatever conda/pip resolve as "latest" at install time. After creating `meti_env`, also run the `pip install
--no-deps -e submodules/peptonizer2000/peptonizer` step above.

> **Avoid quota-constrained filesystems.** Pick an env location with real headroom (`conda create -p
> /path/with/space/envs/meti_env ...`). A per-directory disk quota (not raw filesystem capacity — `df -h` can
> show plenty free while writes still fail) will silently break `pip install`/env creation with
> `OSError: [Errno 122] Disk quota exceeded`, as happened to us on `/storage/work/ims86`.

### Already have working envs? Reuse them

If you (or a companion checkout of HAMLET) already have working `meti_env`, `casanovo_env`, and `cascadia_env`
environments on this machine, there is no need to rebuild them — just point `--*_env_path` at the existing
prefixes (see **Running the pipeline** below). On this cluster, verified working environments are:

```
meti_env:     /storage/group/epo2/default/ims86/envs/meti_env
casanovo_env: /storage/group/epo2/default/ims86/envs/casanovo_env  # casanovo 4.3.0, torch 2.1.2+cu118
cascadia_env: /storage/group/epo2/default/ims86/envs/cascadia_env  # cascadia 0.0.7
```

These are clones (`conda create --clone`) of previously-working copies, migrated to `/storage/group/epo2` to
avoid the `/storage/work/ims86` quota issue above (which had also broken an earlier `casanovo_env` copy there,
e.g. missing Pillow and unable to reinstall it).

### Known issue: `GLIBCXX` version errors when calling casanovo/cascadia directly

If you invoke `casanovo` or `cascadia` directly from their env's `bin/` (e.g. for a manual smoke test) on an
older host OS, you may see `ImportError: .../libstdc++.so.6: version 'GLIBCXX_3.4.x' not found`. This happens
because the system `libstdc++` is older than what the conda packages were built against. Fix by prepending the
env's own `lib/` to `LD_LIBRARY_PATH`:

```bash
LD_LIBRARY_PATH="/path/to/envs/casanovo_env/lib:$LD_LIBRARY_PATH" /path/to/envs/casanovo_env/bin/casanovo version
```

`OrganismID.py` already does this automatically for its own subprocess calls (see `_prepend_env_lib_to_ld_library_path`
in [src/OrganismID.py](src/OrganismID.py)), so this only matters if you call the tools manually.

## Running the pipeline

### Stage 1 — Fetch, convert, and characterize a PXD

```bash
conda run -p /path/to/envs/meti_env --no-capture-output python src/FetchPXD.py \
    --PXD PXD000000 \
    --central_mzml_dir /path/to/spectral_files \
    --use_aria2c
```

This downloads the PXD's `.raw` files, converts them to `.mzML`, runs RunAssessor, and writes
`<central_mzml_dir>/PXD000000/detected_params.json` recording the DDA/DIA call.

#### `FetchPXD.py` flags

| Flag | Default | Description |
|---|---|---|
| `--PXD <ID> [<ID> ...]` | — | One or more PXD accessions to fetch. Effectively required — if omitted, the script prints a message and exits without doing anything. |
| `--central_mzml_dir <path>` | current directory | Root directory where a `<PXD>/` subfolder (raw/mzML/metadata) is created and persisted. Checked on every rerun to decide what still needs fetching. |
| `--download_dir <path>` | `data/PRIDE_downloads` | Deprecated alias for `--central_mzml_dir`; ignored if `--central_mzml_dir` is also given. |
| `--use_aria2c` | off (uses `wget`) | Use `aria2c` (multi-threaded) instead of `wget` for HTTPS/FTP downloads. Needs `aria2c` on PATH (provided by `meti_env`). |
| `--aria2c_threads <N>` | `4` | Threads per file when `--use_aria2c` is set. |
| `--max_raw_files <N>` | all files | Cap on how many `.raw` files to download/convert for this PXD. Handy for a quick smoke test on a large dataset. |
| `--globus` | off | Transfer `.raw` files via Globus instead of HTTPS/FTP/aria2c. |
| `--globus_source_collection <uuid>` | PRIDE's public Globus collection | Source Globus collection UUID. |
| `--globus_destination_collection <uuid>` | the local Globus endpoint's own id | Destination Globus collection UUID. |
| `--globus_destination_base <path>` | — | Destination path; should correspond to `--central_mzml_dir`. |
| `--skip_run_assessor` | off | Skip RunAssessor + `determine_acquisition_params.py` entirely. **Not recommended**: without this step, `detected_params.json` is never written, and `OrganismID.py` will silently default to Casanovo/DDA regardless of the data's actual acquisition type. |
| `--log_file <path>` | none | Path to a `.jsonl` file for structured pipeline-event logging (see **Output files** below). |

### Stage 2 — De novo sequencing + organism prediction

```bash
conda run -p /path/to/envs/meti_env --no-capture-output python src/OrganismID.py \
    --input_dir /path/to/spectral_files/PXD000000 \
    --output_dir organism_results/PXD000000 \
    --contaminants_fasta data/FASTA/UniversalContaminats.fasta \
    --taxid_blocks_file assets/taxid_lists/AllPRIDEtaxids.csv \
    --casanovo_env_path /path/to/envs/casanovo_env \
    --casanovo_config_path assets/casanovo_gpu_4_3.config \
    --cascadia_env_path /path/to/envs/cascadia_env \
    --cascadia_model_path assets/cascadia.ckpt \
    --snakemake_env_path /path/to/envs/meti_env \
    --results_base_dir results
```

`OrganismID.py` itself must be launched from an env containing `pandas`/`pyteomics` (`meti_env` works). It then
shells out to `casanovo_env` or `cascadia_env` for the actual de novo sequencing step. **Both
`--casanovo_env_path` and `--cascadia_env_path` are required**, regardless of which tool ends up being used for
this PXD — the choice between them is made internally by reading `<input_dir>/detected_params.json` (written by
Stage 1), not by any environment variable, so both paths must already be available up front.

> **Casanovo config/version must match.** `assets/casanovo_cpu.config` and `assets/casanovo_gpu_4_3.config` use the
> older config schema matching Casanovo **4.3.0** (the version in the verified working `casanovo_env` on this
> cluster — old-style residue names like `C+57.021`, no database-search section). `assets/casanovo_gpu.config` is
> written for Casanovo **5.x**'s newer schema (ProForma-style residue names like `[Carbamidomethyl]`,
> `max_peptide_len`, `lance_dir`, etc.) and will not work correctly against a 4.3.0 install. Check
> `pip show casanovo` in your `casanovo_env` before picking a config.

#### `OrganismID.py` flags

| Flag | Default | Description |
|---|---|---|
| `--input_dir <path>` | — | **Required.** PXD directory to scan for `.mzML` files — normally Stage 1's `<central_mzml_dir>/<PXD>`. |
| `--output_dir <path>` | `OrganismID` | Directory for this run's de novo + Peptonizer2000 outputs. |
| `--contaminants_fasta <path>` | `data/FASTA/UniversalContaminats.fasta` | FASTA of known contaminant sequences; de novo peptides matching these are removed before Peptonizer2000. |
| `--taxid_blocks_file <path>` | `assets/taxid_lists/AllPRIDEtaxids.csv` | CSV of frequency-ranked taxid blocks (`taxid`, `block` columns). Peptonizer2000 is tried block-by-block, most-frequent-organisms-first, stopping at the first block whose best taxon score exceeds `--peptonizer_score_threshold`. |
| `--peptonizer_score_threshold <float>` | `0.8` | Stop iterating taxid blocks once a block produces a taxon score *strictly above* this value. |
| `--denovo_threshold <int>` | `80` | De novo confidence threshold, as a percent (0–100). Converted to a 0–1 cutoff on Casanovo's `search_engine_score[1]` (or Cascadia's score); peptides below this cutoff, or ≤10 residues long, are filtered out before Peptonizer2000. Also passed directly to Cascadia's own `-t` score-threshold flag. See the filename suffix `_filtered<N>pct` in **Output files** below. |
| `--min_peptides_for_peptonizer <int>` | `100` | Minimum number of filtered peptides required before Peptonizer2000 is even attempted for a given file. |
| `--casanovo_env_path <path>` | — | **Required.** Conda env prefix containing `casanovo`. Required even if this PXD turns out to be DIA (Cascadia), since the DDA/DIA choice is only known at runtime. |
| `--casanovo_config_path <path>` | Casanovo's own built-in default | Casanovo YAML config — see the version-matching note above. |
| `--cascadia_env_path <path>` | — | **Required.** Conda env prefix containing `cascadia`. Required even if this PXD turns out to be DDA (Casanovo). |
| `--cascadia_model_path <path>` | `$CASCADIA_MODEL` env var, else `/opt/cascadia/models/cascadia.ckpt` | Path to the Cascadia model checkpoint (`assets/cascadia.ckpt` in this repo). Only used if Cascadia is selected. |
| `--conda_exe <path>` | `$CONDA_EXE`, else `conda` on PATH | conda executable used for this script's own internal `conda run -p ...` subprocess calls. |
| `--snakemake_env_path <path>` | current env | Conda env prefix containing `snakemake` (used to run the Peptonizer2000 workflow). |
| `--peptonizer_container <path>` | none | Path to a `peptonizer2000.sif` Singularity/Apptainer image. If set, Peptonizer2000 runs inside that container instead of via `conda run`. |
| `--peptonizer_home <path>` | `submodules/peptonizer2000/` (relative to the repo root) | Path to the Peptonizer2000 checkout (Snakefile + scripts). Only needed if you've placed that submodule somewhere else. |
| `--src_dir <path>` | none | Path to a HAMLET `src/` directory; if set, uses `casanovoBolt`/`cascadiaBolt` source instead of the installed `casanovo`/`cascadia` packages. Not applicable to this standalone repo. |
| `--results_base_dir <path>` | `results` | Base directory checked for an already-completed run of this PXD (see caching note below), and where this run's results are copied once complete. |
| `--pxd <ID>` | basename of `--input_dir` | PXD accession, used for caching/logging. Auto-detected if omitted. |
| `--llm_results <path>` | none | Path to an `llm_results/` directory (GPT_Extraction output); used only to count unique LLM-reported taxids, which feeds the representative-file vs. all-files decision below. `FetchPXD.py` doesn't produce this in this repo, so it has no effect unless you supply it yourself. |
| `--organism_id_all` | off | Force de novo + Peptonizer2000 on *every* mzML file, even when PRIDE/LLM metadata implies a single organism (which would otherwise trigger "representative file" mode — see below). |
| `--log_file <path>` | none | Path to a `.jsonl` file for structured pipeline-event logging (see **Output files** below). |
| `--status_file <path>` | none | Path to write a small JSON summary of whether usable Peptonizer2000 results were produced (see **Output files** below). |

There's also a hidden, deprecated `--casanovo_thresholds` alias for `--denovo_threshold` (accepts a comma-separated
string and uses the first value) — kept only for backwards compatibility with older call sites; use
`--denovo_threshold` instead.

### Workflow integration options

For HAMLET v3.0.1, `FetchPXD.py --assessor_threads N` runs assessment with the
allocated threads; `--force_run_assessor` refreshes an existing assessment.
`OrganismID.py` also accepts:

- `--detected_params FILE`: use a workflow-supplied acquisition JSON rather than
  the preliminary JSON inside the input directory. Boolean strings are parsed
  explicitly, and invalid acquisition values fail.
- `--skin_contaminants_fasta FILE` and `--taxonomy_dir DIR`: explicit reference
  locations, independent of the task's current directory.
- `--cores N`: Snakemake CPU limit.
- `--force`: bypass prior prediction and taxonomy caches when the workflow
  requests a rerun.

The Snakemake environment must import `peptonizer` from the checkout selected by
`--peptonizer_home`; mismatched installations fail explicitly.
The legacy Cascadia wrapper honors `UNIMOD_XML` for offline Unimod lookup.
These options do not change the existing taxid-block or search-selection policy.

#### Representative-file mode

If PRIDE project metadata (and/or `--llm_results`) implies the whole PXD contains only a single organism (after
species-level de-duplication via the bundled NCBI taxonomy), `OrganismID.py` runs the full de novo +
Peptonizer2000 pipeline on just **one** randomly-chosen "representative" mzML file, then **copies** that file's
`peptonizer_result.csv` (and its iteration manifest) into every other mzML file's expected output location —
it does not re-run Casanovo/Cascadia/Peptonizer2000 for them. This is why you may see several
`<PXD>_<stem>/Peptonizer2000_data/.../peptonizer_result.csv` files with identical content, but only one of those
`<stem>/` directories actually contains a `.mztab`/`.casanovo.log`. Pass `--organism_id_all` to disable this and
force an independent run per file.

## Output files

### From `FetchPXD.py`, under `<central_mzml_dir>/<PXD>/`

| File/directory | Description |
|---|---|
| `<PXD>_PRIDEmetadata.json` | Raw PRIDE API response for this PXD (project metadata + full file listing). Re-fetched live on every run. |
| `fetch_inventory.json` | Bookkeeping: which `.raw` files were selected (`selected_raw_files`), their expected mzML stems, and whether the PXD is `complete` (all expected mzMLs present and valid). Drives the "what still needs downloading" decision on reruns. |
| `*.mzML` | One per downloaded raw file, converted via ThermoRawFileParser. The source `.raw` files are deleted once all expected mzMLs are confirmed valid (to save disk space) — only the `.mzML` files remain afterward. |
| `runAssessor/study_metadata.json` | RunAssessor's full structured output (per-file acquisition type, fragmentation, labeling, isolation-window statistics, etc.) — the primary evidence source for DDA/DIA detection. |
| `runAssessor/study_metadata.txt`, `runAssessor/study_metadata.file_summary.tsv` | Human-readable companions RunAssessor also writes alongside the JSON (narrative text / summary table). |
| `detected_params.json` | The DDA/DIA + labeling/modification call `OrganismID.py` reads to pick Casanovo vs. Cascadia. Top-level keys: `detected_params` (`DIA`, `acquisition_type`, `acquisition_source`, `acquisition_evidence`, `labeling`, `fragmentation_type`, `high_accuracy_precursors`, `confidence`), `modifications` (`sage_mods`, `diann_mods`, `reporter_ions`, `quantification_type`), `labeling_scores`. |
| `pmc_json/<PXD>_<PMID>.json` | Full-text BioC JSON pulled from PubMed Central, if the PRIDE project links to a published PMID. Best-effort — silently skipped if there's no PMID or no PMC full-text record. |
| `globus_transfer.json` | Present only when `--globus` is used; records the Globus transfer task details. |
| `<--log_file path>` | Structured JSONL pipeline-event log — one JSON object per line: `timestamp`, `pxd`, `process`, `level`, `category`, `message`, optional `details`. |

### From `OrganismID.py`, under `--output_dir` (and mirrored into `--results_base_dir/<PXD>/organism_results/` on completion)

For each processed mzML file, under `{CasanovoSequence,CascadiaSequence}/<PXD>_<raw-file-stem>/`:

| File | Description |
|---|---|
| `<...>.mztab` | Raw Casanovo de novo sequencing output (mzTab format). For Cascadia, the equivalent is a `.ssl` file internally converted to this same `.mztab` layout. |
| `<...>.casanovo.log` (+ `.stderr`), `<...>.log` | The de novo tool's own run log. |
| `<...>_processed.tsv` | The mzTab reparsed into a flat TSV — adds a `stripped_sequence` column (modifications removed) and an averaged per-residue confidence column. |
| `<...>_filtered<N>pct.tsv` | Peptides passing the `--denovo_threshold` score cutoff (`N`%) and length filter, with contaminants removed. |
| `<...>_filtered<N>pct_slim.tsv` | Just `peptide`/`score` columns — this is the file actually handed to Peptonizer2000. |
| `Peptonizer2000_data/<...>_filtered<N>pct_slim/` | Peptonizer2000's working directory for this file — see table below. |
| `Peptonizer2000_logs/<...>_filtered<N>pct_slim/` | Peptonizer2000's own log output (e.g. `get_taxonomy_from_unipept.log`). |

Inside each `Peptonizer2000_data/<...>_filtered<N>pct_slim/` (one of these exists per **attempted** taxid block,
but only the winning block's directory is kept — see `peptonizer_iteration_manifest.json`):

| File | Description |
|---|---|
| `peptonizer_result.csv` | **The final answer.** Columns: `taxon_name,taxon_id,score`, one row per candidate taxon, ascending by score — the last row is the top prediction. |
| `peptonizer_result.png` | Bar chart of the top-scoring taxa, rendered from `peptonizer_result.csv`. |
| `peptonizer_iteration_manifest.json` | **The most useful file for understanding what happened.** Records every taxid block attempted, in frequency order, each block's best taxid/score, and which block (if any) passed `--peptonizer_score_threshold` (`passed_threshold`, `selected_block`, `selected_taxid`, `selected_score`, full `attempts` list). |
| `<...>_config.yaml` | The generated Peptonizer2000 Snakemake config for the winning block (taxon query list, grid-search ranges, etc.). |
| `peptide_taxa.json` | Raw Unipept peptide→taxon mapping results. |
| `taxa_weights_dataframe.csv`, `clustered_taxa_weights_dataframe.csv` | Per-taxon peptide-count weights, before and after clustering taxa by peptidome similarity. |
| `sequence_scores_dataframe.csv` | Per-peptide scores feeding the graphical model. |
| `pepgm_full_graph.graphml` | The full bipartite peptide–taxon factor graph (viewable in Cytoscape/Gephi). |
| `best_parameters.csv` | The `(alpha, beta, prior)` grid-search combination selected as best by Peptonizer2000's internal metric. |
| `prior<p>/pepgm_results_a<alpha>_b<beta>_p<prior>.{csv,png}` | One result per `alpha`×`beta`×`prior` grid-search combination (fixed grid: `alpha∈[0.7,0.8,0.9,0.99]`, `beta∈[0.6,0.7,0.8,0.9]`, `prior∈[0.1,0.3,0.5]`) — intermediate files, not meant to be read directly; `best_parameters.csv` points at the winner. |

Top-level, under `--output_dir` (and `--results_base_dir/<PXD>/`):

| File | Description |
|---|---|
| `<--status_file path>` | One-shot JSON summary written at the end of the run: `status` (`usable_results`/`no_usable_results`), `reason`, `cached`, `run_all_files`, `total_mzml_files`, `processed_mzml_files`, `representative_mzml`, and `usable_result_files` (every `peptonizer_result.csv` produced). |
| `<--log_file path>` | Structured JSONL pipeline-event log, same format as Stage 1's. |

### Caching across reruns

`OrganismID.py` checks `--results_base_dir/<PXD>/organism_results/` for a prior completed run **before** doing
any work; if found, it's copied straight to `--output_dir` and the run exits immediately (see `cached: true` in
`--status_file`'s output). Delete that directory (or point `--results_base_dir` elsewhere) to force a fresh run.

## Picking PXDs to test and tune the pipeline on

`data/organism_id_failure_cohorts.csv` and its parent dataset `data/taxid_prediction.csv` come from a prior
benchmark run of this pipeline across **1,324 real PRIDE PXDs** with known ground-truth organisms, evaluated
file-by-file. They are the fastest way to find a PXD with a known expected answer, rather than guessing blind —
whether you want an easy sanity check or a specific known failure mode to debug.

### `data/taxid_prediction.csv` — the full benchmark (4,114 rows)

One row per raw file actually processed, regardless of outcome.

| Column | Description |
|---|---|
| `pxd` | PRIDE accession. |
| `raw_file` | The specific raw-file-derived sample name within that PXD (not a literal filename — matches the `<PXD>_<stem>` naming used in `OrganismID.py`'s output directories). |
| `result_file_path` | Path to the `peptonizer_result.csv` this row was scored from, relative to that benchmark run's output root. |
| `peptonizer_threshold`, `peptonizer_top_n`, `peptonizer_call_rule` | Evaluation methodology: predictions were taken as the top `peptonizer_top_n` taxa from `peptonizer_result.csv` (`peptonizer_top_n` = number of ground-truth taxids for that file — the `"best (top N; ground-truth count)"` rule), **not** a fixed score cutoff. |
| `n_ground_truth`, `ground_truth_taxids` | How many organisms were actually in the sample, and their taxid(s) (`;`-separated when more than one, e.g. `"562; 9606"`). |
| `n_predicted`, `predicted_taxids` | What the pipeline predicted, same format. |
| `tp`, `fp`, `fn`, `recall`, `precision`, `f1`, `balanced_accuracy` | Standard set-overlap metrics between predicted and ground-truth taxids for that file. |
| `n_scored_taxa` | How many candidate taxa Peptonizer2000 assigned a nonzero score to for this file — a rough proxy for how much peptide evidence was available. |

`f1 == 1.0` (2,911 of 4,114 rows) means a perfect call — a good source of "known good" PXDs for a quick smoke
test or a regression check that a change hasn't broken anything. `f1 < 1.0` (1,203 rows) is exactly the set
exported into `organism_id_failure_cohorts.csv` below, with failure-mode analysis added.

### `data/organism_id_failure_cohorts.csv` — the non-perfect subset, categorized (1,203 rows / 452 PXDs)

Same `pxd`/`raw_file`/`f1`/`ground_truth_taxids`/`predicted_taxids`/`n_ground_truth`/`n_scored_taxa` columns as
above, plus:

| Column | Values | Meaning |
|---|---|---|
| `cohort` | `classified` (943 rows) / `unresolved` (260 rows) | Whether this failure has been attributed to a known, recurring cause (`classified`) or has no identified explanation (`unresolved`). `unresolved` rows always have `f1 == 0.0` and both other columns below set to `none` — these are the most interesting rows to investigate if you're trying to actually improve the pipeline, since there's no known confound to explain away the miss. |
| `exclusion_reasons` | `none`, `ecoli_expression_host_non_ecoli_poi`, `multi_organism_mapping`, or both | A data-quality caveat suggesting the "ground truth" label itself is questionable for this file, independent of whether the pipeline's answer was reasonable. |
| `subset_tags` | `none`, `highscore_competition`, `low_maximum_peptonizer_score`, `multi_organism_mapping`, `ecoli_expression_host_non_ecoli_poi` (`;`-separated combinations) | One or more tags describing *why* the call likely went wrong — see below. |

Tag meanings, with what to expect in `ground_truth_taxids`/`predicted_taxids`/`n_scored_taxa` for each:

- **`highscore_competition`** (437 rows, the most common) — a closely related organism out-scored the true one
  (e.g. ground truth `4530`, predicted `4565` — two closely related species whose tryptic peptides overlap
  heavily). The pipeline's answer is plausible and close, just not an exact taxid match. Good cases for
  evaluating whether a scoring/taxid-block change improves discrimination between close relatives.
- **`low_maximum_peptonizer_score`** (153 rows) — `n_scored_taxa` is unusually low (median 8, vs. ~22 for
  everything else), meaning little peptide evidence was available at all, so even the "best" prediction was
  low-confidence. Good cases for testing whether loosening `--min_peptides_for_peptonizer` or the de novo
  threshold recovers more signal.
- **`multi_organism_mapping`** (199 rows) — `ground_truth_taxids` genuinely lists more than one organism (e.g.
  `"562; 9606"`); a single top-N call can only partially overlap by construction. Good cases for testing
  multi-organism handling specifically.
- **`ecoli_expression_host_non_ecoli_poi`** (247 rows) — a recombinant protein expressed in *E. coli*, where the
  bulk proteomic background is dominated by the *E. coli* expression host, but the "true" label is the protein
  of interest's actual source organism. The pipeline correctly detects the dominant background organism; the
  ground-truth label itself is arguably the wrong target here. Not very useful for tuning organism-ID accuracy
  (the data doesn't actually support resolving the labeled target), but good for testing contaminant handling.

### Suggested workflow

1. **Smoke test / regression check**: pick any PXD with `f1 == 1.0` in `taxid_prediction.csv` — the pipeline
   should still call it correctly after your change.
2. **Targeted debugging**: filter `organism_id_failure_cohorts.csv` to `cohort == 'unresolved'` for failures with
   no known confound, or to a specific `subset_tags` value to probe one failure mode at a time.
3. **Run it**: take the `pxd` value and feed it straight into Stage 1/Stage 2 as described above — e.g.
   `--PXD PXD000357` — then compare your `peptonizer_result.csv` against that row's `ground_truth_taxids`.
   `raw_file` tells you which specific file within a multi-file PXD was scored, matching `OrganismID.py`'s
   `<PXD>_<raw-file-stem>` output directory naming.
