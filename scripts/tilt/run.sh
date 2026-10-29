#!/usr/bin/env bash
# Starts Tilt for a preset: `tilt ci` for the unattended presets (ci, soak), `tilt up` for dev.
# Usage: run.sh <ci|dev|soak> [tilt args...]. Exit codes: tilt's own, 2 usage.
set -euo pipefail
cd "$(dirname "$0")/../.."

preset=${1:-}
[ $# -gt 0 ] && shift
case $preset in
    ci | soak) exec tilt ci "$@" -- --profile "$preset" ;;
    dev) exec tilt up "$@" -- --profile dev ;;
    *) echo "usage: run.sh <ci|dev|soak> [tilt args...]" >&2; exit 2 ;;
esac
