// Installed native read + actual AgentLoop + JSONL persistence; synthetic
// deterministic stream only, zero external model, network or shell calls.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import * as adapter from "../../adapters/dsh/lib/index.js";
import { verifyReadRuntime, pinReadBinding } from "../../adapters/dsh/lib/read_result_guard.js";

const root = process.argv[2];
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
let stage = "arguments", context, evidence;
const saved = { log: console.log, warn: console.warn, error: console.error };
// Host failures can contain tool values. Emit stage + counts only.
console.log = console.warn = console.error = () => {};
try {
  if (!root || ![3, 4].includes(process.argv.length)) throw new Error();
  const sandboxed = process.argv[3] === "sandbox";
  if (process.argv.length === 4 && !sandboxed) throw new Error();
  const selected = await fs.realpath(root);
  const url = (name) => pathToFileURL(path.join(selected, "node_modules", "@deepseek-ai", name, "lib/index.js")).href;
  const host = JSON.parse(await fs.readFile(path.join(selected, "package.json"), "utf8"));
  const dependencyVersion = { "0.1.5-rc.1": "0.1.5-rc.2", "0.2.0-rc.2": "0.2.0-rc.2" }[host.version];
  assert.ok(dependencyVersion);
  if (sandboxed) assert.equal(host.version, "0.2.0-rc.2");
  stage = "fixtures";
  const parent = path.join(process.env.XDG_STATE_HOME || path.join(process.env.HOME, ".local", "state"), "agent-guard-lab");
  await fs.mkdir(parent, { recursive: true, mode: 0o700 });
  evidence = await fs.mkdtemp(path.join(parent, "dsh-read-redaction-20260930-"));
  const workspace = path.join(evidence, "fixture");
  const fixture = spawnSync("python3", ["-I", path.join(repoRoot, "adapters/dsh/harness/read_fixture.py"), "--directory", workspace], { encoding: "utf8", timeout: 15000 });
  assert.equal(fixture.status, 0);
  const control = JSON.parse(await fs.readFile(path.join(evidence, "fixture.control.json"), "utf8"));
  const protectedValues = control.protected_values;
  const hasProtected = (value) => protectedValues.some((secret) => JSON.stringify(value).includes(secret));
  const hasAllProtected = (value) => protectedValues.every((secret) => JSON.stringify(value).includes(secret));
  const [{ Context, Service }, { SystemPrompt }, { ToolRuntime }, { LocalFileSystem }, fsTools] = await Promise.all([
    import(url("cordis")), import(url("dsh-system-prompt")), import(url("dsh-tools")), import(url("dsh-fs-local")), import(url("dsh-tool-fs")),
  ]);
  stage = "native-services";
  context = new Context();
  await context.plugin(SystemPrompt, { includeRuntimeContext: false });
  await context.plugin(ToolRuntime, { mode: "native" });
  if (sandboxed) {
    const [{ SandboxedFileSystem }, { SandboxPolicyService }, { SessionProjectionRegistry }] = await Promise.all([
      import(url("dsh-fs-sandbox")), import(url("dsh-sandbox-policy")), import(url("dsh-session-projection")),
    ]);
    await context.plugin(SessionProjectionRegistry);
    await context.plugin(SandboxPolicyService, { mode: "workspace-write", workspaceRoot: workspace });
    await context.plugin(SandboxedFileSystem, { cwd: workspace });
  } else await context.plugin(LocalFileSystem, { cwd: workspace });
  await context.plugin(fsTools, {});
  const tools = context.get("tools");
  const signal = new AbortController().signal;
  const execute = (filename, extra = {}) => tools.execute({ name: "read", callId: "read-" + filename, arguments: { file_path: filename, ...extra }, signal });
  stage = "baseline";
  const baseline = await execute("redaction.ini");
  assert.equal(baseline.isError, false);
  assert.ok(hasAllProtected(baseline.content));
  assert.ok(hasAllProtected(baseline.meta));
  const benignBefore = await execute("benign.ini");
  let shellCalls = 0;
  class NoShell extends Service {
    constructor(ctx) { super(ctx, "shell"); }
    resolve() { shellCalls += 1; throw new Error("shell prohibited"); }
    run() { shellCalls += 1; throw new Error("shell prohibited"); }
  }
  await context.plugin(NoShell);
  const earlyPeer = context.on("tools/post-execute", async (exec, _result, next) => {
    const decision = await next();
    if (exec.name !== "read") return decision;
    return { kind: "accept", content: [{ type: "text", text: protectedValues[0] }] };
  });
  stage = "adapter-enabled";
  const guardPlugin = context.plugin(adapter, {
    repoRoot, defaultCwd: workspace, promptSection: false,
    readResultGuard: true, readGuardDshRoot: selected,
  });
  stage = "plugin-loading";
  await guardPlugin;
  stage = "plugin-active";
  assert.equal(guardPlugin.state, 2);
  stage = "early-peer-block";
  const earlyFailure = await execute("redaction.ini");
  assert.equal(earlyFailure.isError, true);
  assert.ok(!hasProtected(earlyFailure));
  earlyPeer();
  stage = "sanitized-result";
  const guarded = await execute("redaction.ini");
  assert.equal(guarded.isError, false);
  assert.ok(!hasProtected(guarded));
  assert.ok(JSON.stringify(guarded.content).includes("<REDACTED>"));
  assert.ok(JSON.stringify(guarded.meta).includes("<REDACTED>"));
  assert.ok(JSON.stringify(guarded).includes(control.ordinary_marker));
  assert.deepEqual(guarded.value.lines.map((line) => line.number), baseline.value.lines.map((line) => line.number));
  assert.deepEqual((await execute("benign.ini")), benignBefore);
  stage = "native-failures";
  const failures = [earlyFailure];
  failures.push(await execute("redaction.ini", { limit: 2 }));
  failures.push(await execute("redaction.ini", { offset: 2 }));
  failures.push(await execute("absent.ini"));
  await fs.writeFile(path.join(workspace, "overflow.ini"), ("PORT=8080 " + "a".repeat(80) + "\n").repeat(20000), { mode: 0o600 });
  stage = "native-failure-overflow";
  const overflow = await execute("overflow.ini");
  // New native reads cap output at 50 KiB before the worker's IPC bound.
  assert.match(overflow.content[0].text, host.version === "0.2.0-rc.2" ? /READ_RESULT_INCOMPLETE/ : /READ_RESULT_SIZE/);
  failures.push(overflow);
  await fs.writeFile(path.join(workspace, "long-line.ini"), "a".repeat(15000), { mode: 0o600 });
  stage = "native-failure-long-line";
  const longLine = await execute("long-line.ini");
  assert.match(longLine.content[0].text, /READ_RESULT_INCOMPLETE/);
  failures.push(longLine);
  const originalCap = process.env.AGENT_GUARD_EXFIL_MAX_BYTES;
  stage = "native-failure-worker-limit";
  try {
    process.env.AGENT_GUARD_EXFIL_MAX_BYTES = "1";
    failures.push(await execute("redaction.ini"));
  } finally {
    if (originalCap === undefined) delete process.env.AGENT_GUARD_EXFIL_MAX_BYTES;
    else process.env.AGENT_GUARD_EXFIL_MAX_BYTES = originalCap;
  }
  // Unknown peer text and contexts must not preserve unscanned metadata.
  stage = "native-failure-peers";
  const peer = context.on("tools/post-execute", async (exec, _result, next) => {
    const decision = await next();
    if (exec.name !== "read") return decision;
    return { kind: "accept", content: [{ type: "text", text: protectedValues[0] }] };
  });
  failures.push(await execute("redaction.ini"));
  peer();
  const deferred = context.on("tools/post-execute", async (exec, _result, next) => {
    const decision = await next();
    if (exec.name !== "read") return decision;
    return { ...decision, additionalContexts: [{ content: [{ type: "text", text: protectedValues[0] }] }] };
  });
  failures.push(await execute("redaction.ini"));
  deferred();
  const throwing = context.on("tools/post-execute", async (exec, _result, next) => {
    if (exec.name === "read") throw new Error(protectedValues[0]);
    return next();
  });
  failures.push(await execute("redaction.ini"));
  throwing();
  for (const failed of failures) {
    stage = "native-failure-clean-result";
    assert.equal(failed.isError, true);
    assert.equal(failed.meta, undefined);
    assert.equal(failed.value, undefined);
    assert.ok(!hasProtected(failed));
    assert.match(failed.content[0].text, /READ_RESULT_/);
  }
  stage = "finalizer-and-version-refusal";
  const read = tools.get("read");
  // Temporarily inspect a copied definition; leave the live native tool intact.
  assert.throws(() => pinReadBinding({ get: () => ({ ...read, finalizeContent: () => [] }) }), /READ_GUARD_STARTUP_REFUSED/);
  const drift = path.join(evidence, "unsupported-runtime");
  await fs.mkdir(drift, { mode: 0o700 });
  await fs.writeFile(path.join(drift, "package.json"), JSON.stringify({ name: "@deepseek-ai/dsh", version: "0.1.5-rc.2" }), { mode: 0o600 });
  await assert.rejects(verifyReadRuntime(drift, context), /READ_GUARD_STARTUP_REFUSED/);
  // Dependency drift is independent of the CLI version.
  await fs.writeFile(path.join(drift, "package.json"), JSON.stringify({ name: "@deepseek-ai/dsh", version: host.version }), { mode: 0o600 });
  const driftTools = path.join(drift, "node_modules/@deepseek-ai/dsh-tools");
  await fs.mkdir(path.join(driftTools, "lib"), { recursive: true, mode: 0o700 });
  await fs.writeFile(path.join(driftTools, "package.json"), JSON.stringify({ name: "@deepseek-ai/dsh-tools", version: "0.1.5-rc.3" }), { mode: 0o600 });
  await assert.rejects(verifyReadRuntime(drift, context), /READ_GUARD_STARTUP_REFUSED/);
  await fs.writeFile(path.join(driftTools, "package.json"), JSON.stringify({ name: "@deepseek-ai/dsh-tools", version: dependencyVersion }), { mode: 0o600 });
  await fs.writeFile(path.join(driftTools, "lib/index.js"), "// synthetic artifact drift\n", { mode: 0o600 });
  await assert.rejects(verifyReadRuntime(drift, context), /READ_GUARD_STARTUP_REFUSED/);
  const refusedPlugin = context.plugin(adapter, {
    repoRoot, defaultCwd: workspace, promptSection: false,
    readResultGuard: true, readGuardDshRoot: drift,
  });
  await assert.rejects(Promise.resolve(refusedPlugin), /READ_GUARD_STARTUP_REFUSED/);
  await refusedPlugin.dispose();
  stage = "agent-loop-services";
  const [sessions, projections, agents, persistence, loop, llm] = await Promise.all([
    import(url("dsh-session")), import(url("dsh-session-projection")), import(url("dsh-agent")),
    import(url("dsh-session-persistence-jsonl")), import(url("dsh-agent-loop")), import(url("dsh-llm")),
  ]);
  let syntheticStreams = 0, projectedSecret = false, nextRequestObserved = false;
  class SyntheticLlm extends Service {
    constructor(ctx) { super(ctx, "llm"); }
    async prepareCall(config) {
      return { config, stream: (request) => this.stream(request), systemPromptUpdate: "in-history" };
    }
    async *stream(request) {
      syntheticStreams += 1;
      projectedSecret ||= hasProtected(request.messages);
      if (syntheticStreams === 2) {
        const projected = JSON.stringify(request.messages);
        nextRequestObserved = projected.includes("ghp_<REDACTED>") &&
          projected.includes(control.ordinary_marker) && projected.includes("FEATURE_ENABLED=true");
      }
      const block = syntheticStreams === 1
        ? { type: "tool-call", id: "native-read", name: "read", arguments: JSON.stringify({ file_path: "redaction.ini" }) }
        : { type: "text", text: "Synthetic read validation completed." };
      yield { type: "block-start", index: 0, blockType: block.type };
      yield { type: "block-end", index: 0, block };
      yield { type: "finish", reason: { kind: "stop" } };
    }
  }
  await context.plugin(sessions.SessionStore);
  if (!context.get("sessionProjections")) await context.plugin(projections.SessionProjectionRegistry);
  await context.plugin(agents.AgentRegistry);
  await context.plugin(persistence.default, { root: path.join(evidence, "sessions"), compression: "none" });
  await context.plugin(SyntheticLlm);
  await context.plugin(loop.AgentLoop, { agents: [] });
  stage = "actual-agent-loop";
  const agent = await context.get("agentLoop").create("read-guard-native", { provider: "synthetic", model: "zero-network" }, { cwd: workspace });
  let shadowBodyCalled = false;
  const shadow = agent.ctx.tools.register({ ...read, execute: async () => {
    shadowBodyCalled = true;
    return baseline.value;
  } });
  const shadowResult = await tools.execute({ name: "read", callId: "scope-shadow", arguments: { file_path: "redaction.ini" }, signal, agent });
  assert.equal(shadowResult.isError, true);
  assert.equal(shadowBodyCalled, false);
  assert.ok(!hasProtected(shadowResult));
  shadow();
  agent.followup(llm.createUserMessage({ content: [{ type: "text", text: "Read redaction.ini for local configuration validation." }], source: { kind: "user" } }));
  await agent.whenIdle();
  assert.equal(syntheticStreams, 2);
  assert.equal(projectedSecret, false);
  assert.equal(nextRequestObserved, true);
  stage = "durable-session";
  const backend = context.get("sessionPersistence");
  await backend.flush();
  const listing = await backend.list();
  assert.equal(listing.length, 1);
  const paths = [];
  async function collect(dir) {
    for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
      const filename = path.join(dir, entry.name);
      if (entry.isDirectory()) await collect(filename);
      else paths.push(filename);
    }
  }
  await collect(path.join(evidence, "sessions"));
  const sessionPath = paths.find((filename) => filename.endsWith(".jsonl"));
  assert.ok(sessionPath);
  const rawLog = await fs.readFile(sessionPath, "utf8");
  assert.ok(!hasProtected(rawLog));
  const rows = rawLog.trim().split("\n").map((row) => JSON.parse(row));
  const toolRows = rows.filter((row) => row.type === "tool/result");
  assert.equal(toolRows.length, 1);
  assert.ok(!hasProtected(toolRows[0].data.meta));
  assert.ok(JSON.stringify(toolRows[0].data.meta).includes("<REDACTED>"));
  assert.ok(JSON.stringify(toolRows[0].data.message).includes("<REDACTED>"));
  assert.equal(shellCalls, 0);
  stage = "plugin-disposal";
  await guardPlugin.dispose();
  const afterDisposal = await execute("redaction.ini");
  assert.ok(hasProtected(afterDisposal.content));
  assert.ok(hasProtected(afterDisposal.meta));
  const report = {
    schema: 1, fixture: control.fixture, harness_version: host.version, tool_runtime_version: dependencyVersion,
    filesystem_provider: sandboxed ? "sandbox" : "local",
    node_version: process.version, model_calls: 0, network_calls: 0, shell_calls: shellCalls,
    synthetic_streams: syntheticStreams, baseline_content_exposed: true, baseline_meta_exposed: true,
    guarded_content_redacted: true, guarded_meta_redacted: true, ordinary_marker_preserved: true,
    benign_unchanged: true, line_numbers_preserved: true, failures_blocked: failures.length,
    finalizer_refused: true, version_drift_refused: true, next_request_redacted: true,
    dependency_drift_refused: true, artifact_drift_refused: true, scoped_shadow_denied: true,
    plugin_load_verified: true, plugin_disposal_verified: true,
    plugin_startup_refusal_verified: true,
    next_request_observed: true,
    durable_content_redacted: true, durable_meta_redacted: true, full_durable_log_redacted: true,
    evidence_directory: evidence,
  };
  await fs.writeFile(path.join(evidence, "report.json"), JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
  process.stdout.write(JSON.stringify(report) + "\n");
} catch {
  process.stderr.write("DSH read-redaction probe failed at " + stage + "; raw diagnostics suppressed" + (evidence ? "; evidence retained at " + evidence : "") + "\n");
  process.exitCode = 1;
} finally {
  if (context) { try { await context.fiber.dispose(); } catch { process.exitCode = 1; } }
  Object.assign(console, saved);
}
