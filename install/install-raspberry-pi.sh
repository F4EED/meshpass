#!/bin/bash
# Installe Meshpass sur Raspberry Pi OS (Debian).
set -euo pipefail
cd "$(dirname "$0")"
export MESH_BOARD="Raspberry Pi"
export MESH_EXTRA_PACKAGES="bluez"
export MESH_PORT="${MESH_PORT:-8080}"
exec bash ./linux-common.sh
