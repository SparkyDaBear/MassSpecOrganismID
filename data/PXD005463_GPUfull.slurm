#!/bin/bash
#SBATCH --job-name=PXD005463_GPU
#SBATCH --partition=standard
#SBATCH --account=ncems_cr_default
#SBATCH --nodes=1
#SBATCH --ntasks=10
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
#SBATCH --time=24:00:00
#SBATCH --output=/storage/group/epo2/default/ims86/git_repos/MassSpecOrganismID/runs/PXD005463_GPU/slurm_%j.log

set -euo pipefail

REPO=/storage/group/epo2/default/ims86/git_repos/MassSpecOrganismID
RUN_DIR=/storage/group/epo2/default/ims86/git_repos/MassSpecOrganismID/runs/PXD005463_GPU
PXD=PXD005463

ENVS_DIR=/storage/group/epo2/default/ims86/envs
META_ENV="$ENVS_DIR/meti_env"
CASANOVO_ENV="$ENVS_DIR/casanovo_env"
CASCADIA_ENV="$ENVS_DIR/cascadia_env"
CONDA_EXE=/storage/group/epo2/default/ims86/miniconda3/condabin/conda

mkdir -p "$RUN_DIR"
cd "$RUN_DIR"

echo "=== Stage 1: FetchPXD.py (fetch, convert, RunAssessor, DIA/DDA detection) ==="
"$CONDA_EXE" run -p "$META_ENV" --no-capture-output python "$REPO/src/FetchPXD.py" \
    --PXD "$PXD" \
    --central_mzml_dir "$RUN_DIR/spectral_files" \
    --use_aria2c \
    --log_file "$RUN_DIR/results/$PXD/fetch_events.jsonl"

echo "=== Stage 2: OrganismID.py (de novo sequencing + Peptonizer2000) ==="
"$CONDA_EXE" run -p "$META_ENV" --no-capture-output python "$REPO/src/OrganismID.py" \
    --input_dir "$RUN_DIR/spectral_files/$PXD" \
    --output_dir "$RUN_DIR/results/$PXD/organism_results" \
    --contaminants_fasta "$REPO/data/FASTA/UniversalContaminats.fasta" \
    --taxid_blocks_file "$REPO/assets/taxid_lists/AllPRIDEtaxids.csv" \
    --casanovo_env_path "$CASANOVO_ENV" \
    --casanovo_config_path "$REPO/assets/casanovo_gpu_4_3.config" \
    --cascadia_env_path "$CASCADIA_ENV" \
    --cascadia_model_path "$REPO/assets/cascadia.ckpt" \
    --conda_exe "$CONDA_EXE" \
    --snakemake_env_path "$META_ENV" \
    --results_base_dir "$RUN_DIR/results" \
    --log_file "$RUN_DIR/results/$PXD/organism_events.jsonl"

echo "=== Done ==="

