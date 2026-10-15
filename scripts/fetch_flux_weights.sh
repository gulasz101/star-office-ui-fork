#!/usr/bin/env bash
# Fetch the FLUX.2-klein-4B weights + pixel-art LoRA for the SOU art pipeline.
#
# Why the flags:
#   HF_HUB_DISABLE_XET=1 — the Xet transfer backend hung at 0 KB/s on this host
#     (process alive, 2.6 GB RSS, zero bytes moving). Plain HTTPS gives a steady
#     10 MB/s. Without this flag the download looks like a dead network.
#   --include …          — the repo also ships a duplicate 7.75 GB single-file
#     safetensors at its root (ComfyUI layout). diffusers only needs the
#     subfolders, so skipping it saves 7.75 GB of the ~24 GB repo.
#
# And why the progress wrapper: `hf download` renders a progress bar ONLY when
# stdout is a TTY. Redirected to a log it prints nothing until it finishes, so a
# healthy download and a dead one look identical. This script samples the cache
# directory instead, so the log always shows real movement.
set -euo pipefail

VENV="${VENV:-$HOME/.venvs/sou-flux}"
LOG="${LOG:-$HOME/.hermes/profiles/szarik/cache/scratch/sou-flux-download.log}"
MODEL="black-forest-labs/FLUX.2-klein-4B"
LORA="Limbicnation/pixel-art-lora"
CACHE="$HOME/.cache/huggingface/hub/models--${MODEL//\//--}"
TOTAL_MB=16200   # transformer 7.75GB + Qwen3 text encoder 8.05GB + VAE 0.17GB

export HF_HUB_DISABLE_XET=1

progress_loop() {
  while sleep 30; do
    [ -d "$CACHE" ] || continue
    mb=$(du -sm "$CACHE" 2>/dev/null | awk '{print $1}')
    printf '[dl] %s  %s MB / ~%s MB\n' "$(date +%T)" "${mb:-0}" "$TOTAL_MB"
  done
}

progress_loop & REPORTER=$!
trap 'kill $REPORTER 2>/dev/null || true' EXIT

{
  echo "[dl] start $(date +%T)  venv=$VENV  xet=disabled"
  "$VENV/bin/hf" download "$MODEL" --max-workers 4 \
    --include "model_index.json" \
    --include "transformer/*" \
    --include "text_encoder/*" \
    --include "vae/*" \
    --include "tokenizer/*" \
    --include "scheduler/*"
  echo "[dl] klein done $(date +%T)"
  "$VENV/bin/hf" download "$LORA" pytorch_lora_weights.safetensors
  echo "[dl] lora done $(date +%T)"
  du -sh "$CACHE"
  echo "DOWNLOAD_OK"
} >> "$LOG" 2>&1
