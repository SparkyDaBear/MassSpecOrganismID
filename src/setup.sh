#!/usr/bin/env bash
###############################################################################
# setup.sh — one-shot environment bootstrap for MassSpecOrganismID
#
# Sets up everything a new user needs to run the organism-ID pipeline:
#   1. Initializes git submodules (RunAssessor, Peptonizer2000)
#   2. Applies the tracked Peptonizer2000 pandas-compatibility patch
#   3. Creates the three conda environments (meti_env, casanovo_env, cascadia_env)
#   4. pip-installs the (patched) peptonizer package into meti_env
#
# This script is idempotent: re-running it skips any step that's already done
# (submodules already initialized, patch already applied, envs that already
# exist), so it's safe to re-run after a partial/failed run.
#
# Usage (run from the repo root):
#   ./src/setup.sh [options]
#
# Options:
#   --envs-dir <path>     Base directory under which the three conda envs are
#                         created, as <envs-dir>/{meti_env,casanovo_env,cascadia_env}.
#                         Default: <repo_root>/envs.
#                         IMPORTANT: pick a location with real free space/quota.
#                         A per-directory disk quota (separate from raw
#                         filesystem capacity) can silently break `pip install`
#                         and `conda create` with "Disk quota exceeded" even
#                         when `df -h` shows plenty of space free.
#   --resolved            Use the *_resolved.yml pinned snapshots instead of the
#                         plain, version-flexible *.yml files.
#   --conda-exe <path>    Path to the conda executable to use.
#                         Default: $CONDA_EXE, or whatever `conda` resolves to
#                         on PATH.
#   --use-mamba           Use `mamba` instead of `conda` for env creation, if
#                         available (faster solver). Auto-detected if present
#                         unless --no-mamba is also passed.
#   --no-mamba            Force use of `conda` even if `mamba` is on PATH.
#   --skip-envs           Skip conda environment creation entirely (only do
#                         submodules + patch).
#   --skip-submodules     Skip submodule init/patch entirely (only do envs).
#   --only-meti           Only create meti_env (skip casanovo_env/cascadia_env).
#                         Useful if you already have casanovo_env/cascadia_env
#                         from elsewhere (e.g. a companion HAMLET checkout).
#   --skip-large-assets   Skip downloading the NCBI taxonomy dumps (~500 MB) and
#                         the Cascadia model checkpoint (~558 MB). These are too
#                         large for git, so by default this script fetches them
#                         (NCBI FTP taxdump; gdown from Cascadia's public
#                         Google Drive). Use this flag if you don't have network
#                         access to those sources here, or already have the files.
#   -h, --help            Show this help and exit.
#
# Examples:
#   ./src/setup.sh --envs-dir /storage/group/myproject/envs
#   ./src/setup.sh --envs-dir /scratch/$USER/envs --resolved
#   ./src/setup.sh --skip-envs                      # just set up submodules/patch
#   ./src/setup.sh --only-meti --envs-dir ~/envs    # reuse existing casanovo/cascadia envs
###############################################################################
set -euo pipefail

###############################################################################
# Argument parsing
###############################################################################
# This script lives at <repo_root>/src/setup.sh, so the repo root is one
# directory up from here.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"

ENVS_DIR="${REPO_ROOT}/envs"
USE_RESOLVED=false
CONDA_EXE="${CONDA_EXE:-}"
USE_MAMBA="auto"
SKIP_ENVS=false
SKIP_SUBMODULES=false
ONLY_METI=false
SKIP_LARGE_ASSETS=false

print_help() {
    sed -n '2,/^###/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --envs-dir)
            ENVS_DIR="$2"; shift 2 ;;
        --resolved)
            USE_RESOLVED=true; shift ;;
        --conda-exe)
            CONDA_EXE="$2"; shift 2 ;;
        --use-mamba)
            USE_MAMBA=true; shift ;;
        --no-mamba)
            USE_MAMBA=false; shift ;;
        --skip-envs)
            SKIP_ENVS=true; shift ;;
        --skip-submodules)
            SKIP_SUBMODULES=true; shift ;;
        --only-meti)
            ONLY_METI=true; shift ;;
        --skip-large-assets)
            SKIP_LARGE_ASSETS=true; shift ;;
        -h|--help)
            print_help; exit 0 ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Run '$0 --help' for usage." >&2
            exit 1 ;;
    esac
done

###############################################################################
# Logging helpers
###############################################################################
log_section() { printf '\n=== %s ===\n' "$1"; }
log_info()    { printf '  %s\n' "$1"; }
log_warn()    { printf '  WARNING: %s\n' "$1" >&2; }
log_error()   { printf '  ERROR: %s\n' "$1" >&2; }

###############################################################################
# Sanity checks
###############################################################################
log_section "Checking prerequisites"

if ! command -v git >/dev/null 2>&1; then
    log_error "git is required but not found on PATH."
    exit 1
fi
log_info "git: $(command -v git)"

if [[ -z "${CONDA_EXE}" ]]; then
    if command -v conda >/dev/null 2>&1; then
        CONDA_EXE="$(command -v conda)"
    else
        log_error "conda is required but not found on PATH and \$CONDA_EXE is unset."
        log_error "Install Miniconda/Miniforge first, or pass --conda-exe /path/to/conda."
        exit 1
    fi
fi
log_info "conda: ${CONDA_EXE}"

CONDA_BUILDER="${CONDA_EXE}"
if [[ "${USE_MAMBA}" != "false" ]]; then
    MAMBA_CANDIDATE="$(dirname "${CONDA_EXE}")/mamba"
    if command -v mamba >/dev/null 2>&1; then
        CONDA_BUILDER="$(command -v mamba)"
        log_info "mamba: ${CONDA_BUILDER} (faster solver, used for env creation)"
    elif [[ -x "${MAMBA_CANDIDATE}" ]]; then
        CONDA_BUILDER="${MAMBA_CANDIDATE}"
        log_info "mamba: ${CONDA_BUILDER} (faster solver, used for env creation)"
    elif [[ "${USE_MAMBA}" == "true" ]]; then
        log_error "--use-mamba was passed but mamba was not found."
        exit 1
    else
        log_info "mamba not found; falling back to conda for env creation (slower solve)."
    fi
fi

###############################################################################
# Step 1: git submodules (RunAssessor, Peptonizer2000)
###############################################################################
if [[ "${SKIP_SUBMODULES}" == "true" ]]; then
    log_section "Skipping submodule setup (--skip-submodules)"
else
    log_section "Initializing git submodules"
    cd "${REPO_ROOT}"
    git submodule update --init --recursive
    log_info "submodules/runassessor and submodules/peptonizer2000 are ready."

    log_section "Applying Peptonizer2000 patches"
    PEPTONIZER_DIR="${REPO_ROOT}/submodules/peptonizer2000"
    PATCH_FILES=(
        "${REPO_ROOT}/patches/peptonizer2000/pandas_compat.patch"
        "${REPO_ROOT}/patches/peptonizer2000/unipept_nontryptic_species_rollup.patch"
    )

    cd "${PEPTONIZER_DIR}"
    for PATCH_FILE in "${PATCH_FILES[@]}"; do
        if [[ ! -f "${PATCH_FILE}" ]]; then
            log_error "Patch file not found: ${PATCH_FILE}"
            exit 1
        fi
        if git apply --reverse --check "${PATCH_FILE}" 2>/dev/null; then
            log_info "Patch already applied; skipping: ${PATCH_FILE}"
        elif git apply --check "${PATCH_FILE}" 2>/dev/null; then
            git apply "${PATCH_FILE}"
            log_info "Patch applied: ${PATCH_FILE}"
        else
            log_warn "Patch did not apply cleanly (submodule commit may have changed): ${PATCH_FILE}"
            log_warn "Inspect manually: cd ${PEPTONIZER_DIR} && git apply ${PATCH_FILE}"
        fi
    done
    cd "${REPO_ROOT}"
fi

###############################################################################
# Step 2: conda environments
###############################################################################
create_env_if_missing() {
    local name="$1"
    local yml_suffix="$2"   # "" or "_resolved"
    local env_path="${ENVS_DIR}/${name}"
    local yml_file="${REPO_ROOT}/conda_envs/${name}${yml_suffix}.yml"

    if [[ ! -f "${yml_file}" ]]; then
        log_error "Env definition not found: ${yml_file}"
        return 1
    fi

    if [[ -d "${env_path}/conda-meta" ]]; then
        log_info "${name} already exists at ${env_path}; skipping creation."
        return 0
    fi

    log_info "Creating ${name} at ${env_path} from $(basename "${yml_file}") ..."
    mkdir -p "${ENVS_DIR}"
    "${CONDA_BUILDER}" env create -f "${yml_file}" -p "${env_path}"
}

if [[ "${SKIP_ENVS}" == "true" ]]; then
    log_section "Skipping conda environment creation (--skip-envs)"
else
    log_section "Creating conda environments under ${ENVS_DIR}"
    log_warn "Make sure this location has real free space/quota (see header comment above)."

    YML_SUFFIX=""
    if [[ "${USE_RESOLVED}" == "true" ]]; then
        YML_SUFFIX="_resolved"
        log_info "Using pinned *_resolved.yml definitions."
    fi

    create_env_if_missing "meti_env" "${YML_SUFFIX}"

    if [[ "${ONLY_METI}" == "true" ]]; then
        log_info "--only-meti set; skipping casanovo_env/cascadia_env creation."
    else
        create_env_if_missing "casanovo_env" "${YML_SUFFIX}"
        create_env_if_missing "cascadia_env" "${YML_SUFFIX}"
    fi
fi

###############################################################################
# Step 3: pip-install the (patched) peptonizer package into meti_env
###############################################################################
if [[ "${SKIP_ENVS}" == "true" || "${SKIP_SUBMODULES}" == "true" ]]; then
    log_section "Skipping peptonizer pip install (--skip-envs and/or --skip-submodules set)"
    log_info "Run this manually once both steps are done:"
    log_info "  <meti_env>/bin/pip install --no-deps -e submodules/peptonizer2000/peptonizer"
else
    log_section "Installing peptonizer (editable, --no-deps) into meti_env"
    METI_PIP="${ENVS_DIR}/meti_env/bin/pip"
    if [[ ! -x "${METI_PIP}" ]]; then
        log_error "pip not found at ${METI_PIP}; did meti_env get created correctly?"
        exit 1
    fi
    "${METI_PIP}" install --no-deps -e "${REPO_ROOT}/submodules/peptonizer2000/peptonizer"

    log_info "Verifying import..."
    "${ENVS_DIR}/meti_env/bin/python" -c "import peptonizer; print('  peptonizer OK:', peptonizer.__file__)"
fi

###############################################################################
# Step 4: verify small bundled (git-tracked) assets are present
###############################################################################
log_section "Checking small bundled assets"
declare -a REQUIRED_ASSETS=(
    "data/FASTA/UniversalContaminats.fasta"
    "assets/taxid_lists/AllPRIDEtaxids.csv"
    "assets/casanovo_cpu.config"
    "assets/casanovo_gpu.config"
    "assets/casanovo_gpu_4_3.config"
)
MISSING_ASSETS=false
for asset in "${REQUIRED_ASSETS[@]}"; do
    if [[ -f "${REPO_ROOT}/${asset}" ]]; then
        log_info "OK: ${asset}"
    else
        log_warn "Missing: ${asset} (should have come from git clone — check your checkout)"
        MISSING_ASSETS=true
    fi
done
if [[ "${MISSING_ASSETS}" == "true" ]]; then
    log_warn "One or more bundled assets are missing. See README.md for where to obtain them."
fi

###############################################################################
# Step 5: fetch large assets that are too big for git (gitignored)
###############################################################################
if [[ "${SKIP_LARGE_ASSETS}" == "true" ]]; then
    log_section "Skipping large asset downloads (--skip-large-assets)"
else
    log_section "Fetching NCBI taxonomy database (nodes.dmp / names.dmp, ~500 MB)"
    NODES_DMP="${REPO_ROOT}/assets/taxonomy/nodes.dmp"
    NAMES_DMP="${REPO_ROOT}/assets/taxonomy/names.dmp"
    if [[ -f "${NODES_DMP}" && -f "${NAMES_DMP}" ]]; then
        log_info "Already present: assets/taxonomy/{nodes.dmp,names.dmp}"
    else
        mkdir -p "${REPO_ROOT}/assets/taxonomy"
        TAXDUMP_URL="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"
        TAXDUMP_FILE="${REPO_ROOT}/assets/taxonomy/taxdump.tar.gz"
        log_info "Downloading ${TAXDUMP_URL} ..."
        if curl -fL --progress-bar "${TAXDUMP_URL}" -o "${TAXDUMP_FILE}"; then
            tar -xzf "${TAXDUMP_FILE}" -C "${REPO_ROOT}/assets/taxonomy" nodes.dmp names.dmp
            rm -f "${TAXDUMP_FILE}"
            log_info "Extracted nodes.dmp and names.dmp."
        else
            log_warn "Failed to download NCBI taxdump. Organism ID will fall back to simple taxid"
            log_warn "counting (no species-level de-duplication). Retry later with:"
            log_warn "  curl -fL ${TAXDUMP_URL} -o ${TAXDUMP_FILE} && tar -xzf ${TAXDUMP_FILE} -C ${REPO_ROOT}/assets/taxonomy nodes.dmp names.dmp"
        fi
    fi

    log_section "Fetching Cascadia model checkpoint (cascadia.ckpt, ~558 MB)"
    CASCADIA_CKPT="${REPO_ROOT}/assets/cascadia.ckpt"
    if [[ -f "${CASCADIA_CKPT}" ]]; then
        log_info "Already present: assets/cascadia.ckpt"
    else
        METI_PIP="${ENVS_DIR}/meti_env/bin/pip"
        GDOWN="gdown"
        if [[ -x "${METI_PIP}" ]]; then
            "${METI_PIP}" install --quiet gdown || true
            GDOWN="${ENVS_DIR}/meti_env/bin/gdown"
        fi
        log_info "Attempting download via gdown from Cascadia's public Google Drive folder..."
        if command -v "${GDOWN}" >/dev/null 2>&1 || [[ -x "${GDOWN}" ]]; then
            "${GDOWN}" "1G4lkGajGtFEz0dzbrovla6LJ5EUyx8hP" -O "${CASCADIA_CKPT}" || true
        fi
        if [[ -f "${CASCADIA_CKPT}" ]]; then
            log_info "Downloaded cascadia.ckpt."
        else
            log_warn "Could not download cascadia.ckpt automatically. DIA (Cascadia) runs will fail until"
            log_warn "you fetch it manually from:"
            log_warn "  https://drive.google.com/drive/folders/1UTrZIrCdUqYqscbqga_KdX8kc8ZjMMfr"
            log_warn "and place it at: ${CASCADIA_CKPT}"
        fi
    fi
fi

###############################################################################
# Done
###############################################################################
log_section "Setup complete"
cat <<EOF

  meti_env:     ${ENVS_DIR}/meti_env
  casanovo_env: ${ENVS_DIR}/casanovo_env
  cascadia_env: ${ENVS_DIR}/cascadia_env

Next steps (see README.md for full details):

  conda run -p ${ENVS_DIR}/meti_env --no-capture-output python src/FetchPXD.py \\
      --PXD PXD000000 \\
      --central_mzml_dir /path/to/spectral_files \\
      --use_aria2c

  conda run -p ${ENVS_DIR}/meti_env --no-capture-output python src/OrganismID.py \\
      --input_dir /path/to/spectral_files/PXD000000 \\
      --output_dir organism_results/PXD000000 \\
      --casanovo_env_path ${ENVS_DIR}/casanovo_env \\
      --casanovo_config_path assets/casanovo_cpu.config \\
      --cascadia_env_path ${ENVS_DIR}/cascadia_env \\
      --cascadia_model_path assets/cascadia.ckpt \\
      --snakemake_env_path ${ENVS_DIR}/meti_env \\
      --results_base_dir results

EOF
