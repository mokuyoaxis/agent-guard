// Mock Anthropic Messages API. Advances the script only when the client
// returns a tool_result, so one scripted turn == one harness turn.
import http from "node:http";
import fs from "node:fs";

const PORT = Number(process.env.MOCK_PORT || 8899);
const SCRIPT = JSON.parse(fs.readFileSync(process.env.MOCK_SCRIPT, "utf8"));
const LOG = process.env.MOCK_LOG || "/tmp/ccharness/requests.jsonl";
fs.writeFileSync(LOG, "");

let turn = 0, seen = 0;
const isToolResult = (body) => {
  const msgs = body.messages || [];
  for (let i = msgs.length - 1; i >= 0; i--) {
    const c = msgs[i].content;
    if (Array.isArray(c) && c.some((b) => b.type === "tool_result")) return true;
  }
  return false;
};

const server = http.createServer((req, res) => {
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    let body = {};
    try { body = JSON.parse(raw); } catch {}
    const n = ++seen;
    const tool_res = isToolResult(body);
    fs.appendFileSync(LOG, JSON.stringify({
      n, path: req.url, ts: new Date().toISOString(), model: body.model,
      tool_result: tool_res, messages: body.messages,
      system: typeof body.system === "string" ? body.system.slice(0, 2000) : body.system,
    }) + "\n");

    const reply = SCRIPT[Math.min(turn, SCRIPT.length - 1)];
    if (turn < SCRIPT.length - 1 && tool_res) turn++;

    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      id: "msg_mock_" + n, type: "message", role: "assistant",
      model: body.model || "claude-mock", content: reply.content,
      stop_reason: reply.stop_reason || "end_turn", stop_sequence: null,
      usage: { input_tokens: 100, output_tokens: 50 },
    }));
  });
});
server.listen(PORT, "127.0.0.1", () => console.log("mock ready " + PORT));
