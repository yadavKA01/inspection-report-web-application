#!/usr/bin/env bash
set -euo pipefail
git lfs install
git lfs pull
pip install --upgrade pip
pip install -r backend/requirements.txt
