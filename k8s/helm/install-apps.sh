#!/bin/bash

# Simple script to install:
# - Marketplace workloads (single Helm chart)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Marketplace chart installation script..."
./marketplace/install.sh

echo "✅ Apps installed successfully (marketplace only)"
