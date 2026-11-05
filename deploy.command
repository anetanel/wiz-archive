#!/bin/bash
# Double-click wrapper for macOS Finder; the logic lives in deploy.sh.
exec "$(dirname "$0")/deploy.sh" "$@"
