#!/usr/bin/env bash
# End-to-end through the REAL Claude Code harness with a PATH that has
# `python` but no `python3` - the Windows shape from the issue.
set -uo pipefail

# Repo root resolved from this script, so the harness travels with a checkout.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
NAME="$1"; CMD="$2"; PORT="$3"
RUNS="${HARNESS_OUT:-/tmp/agent-guard-harness}/$NAME"
rm -rf "$RUNS"; mkdir -p "$RUNS/project/.claude"
P="$RUNS/project"
git -C "$P" init -q && git -C "$P" config user.email h@l && git -C "$P" config user.name h && git -C "$P" config commit.gpgsign false
printf 'node_modules/\n*.log\n' > "$P/.gitignore"
mkdir -p "$P/src" "$P/build" && printf "print('base')\n" > "$P/src/main.py" && printf artifact > "$P/build/o.js" && printf log > "$P/app.log"
git -C "$P" add -A && git -C "$P" commit -qm baseline
cat > "$P/.claude/settings.json" <<JSON
{ "hooks": { "PreToolUse": [ { "matcher": "Bash",
  "hooks": [ { "type": "command", "command": "python $REPO_ROOT/adapters/claude/pre_tool_use.py", "timeout": 120 } ] } ] } }
JSON
node -e '
const fs=require("fs");const cmd=process.argv[1];
fs.writeFileSync(process.argv[2], JSON.stringify([
 {content:[{type:"text",text:"Recon."},{type:"tool_use",id:"t1",name:"Bash",input:{command:"ls -A"}}],stop_reason:"tool_use"},
 {content:[{type:"text",text:"Executing."},{type:"tool_use",id:"t2",name:"Bash",input:{command:cmd}}],stop_reason:"tool_use"},
 {content:[{type:"text",text:"Done."}],stop_reason:"end_turn"}]));' "$CMD" "$RUNS/script.json"
setsid nohup env MOCK_SCRIPT="$RUNS/script.json" MOCK_PORT="$PORT" MOCK_LOG="$RUNS/requests.jsonl" \
  node "$REPO_ROOT/adapters/claude/harness/mock_anthropic_api.mjs" > "$RUNS/mock.log" 2>&1 < /dev/null &
MP=$!
for i in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:$PORT/v1/messages" && break; sleep 0.25; done
cd "$P"
# PATH deliberately carries NO python3: the hook itself is invoked via `python`.
timeout 180 env PATH=/tmp/nopy3bin ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT" ANTHROPIC_AUTH_TOKEN=mock-token \
  "$CLAUDE_BIN" -p "Clean up build output." \
  --output-format stream-json --verbose --include-hook-events \
  --model claude-sonnet-4-5 --permission-mode dontAsk \
  > "$RUNS/transcript.jsonl" 2> "$RUNS/stderr.txt"
echo "claude exit=$?" > "$RUNS/exit.txt"
kill $MP 2>/dev/null; wait $MP 2>/dev/null
echo "scenario $NAME done"
