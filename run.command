#!/bin/bash
# Double-click wrapper for macOS Finder; the logic lives in run.sh.
exec "$(dirname "$0")/run.sh" "$@"
