#!/usr/bin/env bash
set -eu
plugin_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
project_dir="$(dirname -- "$plugin_dir")"
cd -- "$project_dir"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    command -v python >/dev/null 2>&1 || { printf 'Python is required in Windows PATH.\n' >&2; exit 1; }
    entry="$(cygpath -w "$plugin_dir/tools/entry.py")"
    exec python -B "$entry" "$@"
    ;;
  *) exec /usr/bin/python3 -B "$plugin_dir/tools/entry.py" "$@" ;;
esac
