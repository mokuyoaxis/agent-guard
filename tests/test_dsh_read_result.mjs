import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { scanReadValue, blockRead, MAX_INPUT_BYTES, verifyReadRuntime, pinReadBinding } from "../adapters/dsh/lib/read_result_guard.js";

const workspace = process.cwd();
const value = { path: "config.ini", offset: 1, lines: [{ number: 1, text: "# 普通配置 PORT=8080" }], totalLines: 1 };
assert.deepEqual(await scanReadValue(value, workspace), { kind: "accept", value });
const oversized = { ...value, lines: [{ number: 1, text: "a".repeat(MAX_INPUT_BYTES) }] };
assert.deepEqual(await scanReadValue(oversized, workspace), blockRead("READ_RESULT_SIZE"));
const cancelled = new AbortController();
cancelled.abort();
assert.deepEqual(await scanReadValue(value, workspace, cancelled.signal), blockRead("READ_RESULT_CANCELLED"));
let command;
const start = (code) => (executable, args, options) => {
  command = { executable, args };
  const child = spawn(process.execPath, ["-e", code], options);
  child.stdin.resume();
  return child;
};
assert.deepEqual(await scanReadValue(value, workspace, undefined, {
  spawnProcess: start("process.stdin.resume();setInterval(()=>{},1000)"), timeoutMs: 100,
}), blockRead("READ_RESULT_TIMEOUT"));
assert.equal(command.executable, "python3");
assert.deepEqual(command.args.slice(0, 1), ["-I"]);
assert.equal(command.args.length, 2);
assert.ok(!JSON.stringify(command).includes(value.lines[0].text));
for (const code of ["process.stdin.resume();process.exitCode=1", "process.stdout.write('invalid reply');process.stdin.resume()"] ) {
  assert.deepEqual(await scanReadValue(value, workspace, undefined, { spawnProcess: start(code) }), blockRead("READ_RESULT_SCAN_FAILED"));
}
assert.deepEqual(await scanReadValue(value, workspace, undefined, {
  spawnProcess: start("process.stdout.write('x'.repeat(2097153));process.stdin.resume()"),
}), blockRead("READ_RESULT_SIZE"));
assert.deepEqual(await scanReadValue(value, workspace, undefined, {
  spawnProcess: () => { throw new Error("PRIVATE_WORKER_DIAGNOSTIC"); },
}), blockRead("READ_RESULT_SCAN_FAILED"));
const controller = new AbortController();
const pending = scanReadValue(value, workspace, controller.signal, {
  spawnProcess: start("process.stdin.resume();setInterval(()=>{},1000)"),
});
controller.abort();
assert.deepEqual(await pending, blockRead("READ_RESULT_CANCELLED"));
await assert.rejects(verifyReadRuntime("relative", {}), /READ_GUARD_STARTUP_REFUSED/);
await assert.rejects(verifyReadRuntime(workspace, {}), /READ_GUARD_STARTUP_REFUSED/);
assert.throws(() => pinReadBinding({ get: () => undefined }), /READ_GUARD_STARTUP_REFUSED/);
console.log("DSH read result worker smoke passed (no model)");
