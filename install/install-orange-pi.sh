#!/bin/bash
# Installe Meshpass sur Orange Pi (Armbian ou Debian).
set -euo pipefail
cd "$(dirname "$0")"
export MESH_BOARD="Orange Pi"
export MESH_EXTRA_PACKAGES=""
export MESH_PORT="${MESH_PORT:-8080}"
exec bash ./linux-common.sh
