#!/usr/bin/env bash
# 试运行：八人格 ai_analysis_14d 导入 somni_ai_insights（只统计删/插，不写库）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
exec python scripts/insert_data/insert_ai_analysis_14d_to_mongo.py --dry-run "$@"
