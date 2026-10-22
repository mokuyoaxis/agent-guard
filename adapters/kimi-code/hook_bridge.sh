#!/bin/sh
# Kimi treats hook failures other than exit 2 as allow. Keep this POSIX shell
# bridge alive long enough to turn a failed Python launch into an explicit 2.
# A missing/unmatched hook or a host timeout is still fail-open.

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    printf '%s\n' '[agent-guard] Kimi hook bridge needs one absolute Python path and optional --stop-feedback' >&2
    exit 2
fi

hook_script=pre_tool_use.py
refusal='refusing tool call'
if [ "$#" -eq 2 ]; then
    case "$2" in
        --stop-feedback)
            hook_script=stop_feedback.py
            refusal='recovery feedback unavailable; inspect restore.py list'
            ;;
        *)
            printf '%s\n' '[agent-guard] Kimi hook bridge: unknown option' >&2
            exit 2
            ;;
    esac
fi

case "$1" in
    /*) python_bin=$1 ;;
    *)
        printf '%s\n' '[agent-guard] Kimi hook Python path must be absolute' >&2
        exit 2
        ;;
esac

bridge_dir=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd -P) || {
    printf '%s\n' '[agent-guard] Kimi hook bridge directory unavailable' >&2
    exit 2
}

"$python_bin" "$bridge_dir/$hook_script"
status=$?
case "$status" in
    0|2) exit "$status" ;;
    *)
        printf '[agent-guard] Kimi hook process failed (exit %s); %s\n' "$status" "$refusal" >&2
        exit 2
        ;;
esac
