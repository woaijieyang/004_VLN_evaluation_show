#!/usr/bin/env bash
set -eu
plugin_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
command -v python3 >/dev/null 2>&1 || { printf 'Python 3 is required.\n' >&2; exit 1; }
exec python3 -B "$plugin_dir/tools/entry.py" "$@"
