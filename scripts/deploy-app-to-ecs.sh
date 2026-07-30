#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Delegated remote gate: tests.test_dashboard_workspace_api tests.test_dashboard_runtime
exec "$SCRIPT_DIR/deploy-dashboard-workspaces-to-ecs.sh" "$@"
