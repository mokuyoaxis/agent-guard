#!/bin/sh
# Claude Code does not block a PreToolUse call for most hook errors other than
# exit 2. Keep this bridge alive long enough to translate Python launch/runtime
# failures into an explicit refusal. Missing hooks and host timeouts remain
# outside the bridge's control.

if [ "$#" -ne 1 ]; then
    printf '%s\n' '[agent-guard] Claude hook bridge needs one absolute Python path' >&2
    exit 2
fi

case "$1" in
    /*) python_bin=$1 ;;
    *)
        printf '%s\n' '[agent-guard] Claude hook Python path must be absolute' >&2
        exit 2
        ;;
esac

bridge_dir=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd -P) || {
    printf '%s\n' '[agent-guard] Claude hook bridge directory unavailable' >&2
    exit 2
}

"$python_bin" "$bridge_dir/pre_tool_use.py"
status=$?
case "$status" in
    0|2) exit "$status" ;;
    *)
        printf '[agent-guard] Claude hook process failed (exit %s); refusing tool call\n' "$status" >&2
        exit 2
        ;;
esac
