import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = path.join(repoRoot, "adapters", "dsh", "lib", "index.js");
const manifest = JSON.parse(await fs.readFile(path.join(repoRoot, "package.json"), "utf8"));
const patch = await fs.readFile(path.join(repoRoot, "adapters", "dsh", "cordis.patch.yml"), "utf8");
assert.match(patch, new RegExp(`\\bname:\\s*['\"]?${manifest.name}['\"]?(?:\\s|$)`));
let source = await fs.readFile(sourcePath, "utf8");
assert.doesNotMatch(source, /from ["']@deepseek-ai\/dsh-tools["']/);

const scratch = await fs.mkdtemp(path.join(os.tmpdir(), "agent-guard-dsh-smoke-"));
const modulePath = path.join(scratch, "index.mjs");
await fs.writeFile(modulePath, source);

try {
  const adapter = await import(pathToFileURL(modulePath).href);
  const validate = adapter.Config["~standard"].validate;
  const defaults = {
    repoRoot: "", defaultCwd: "", promptSection: true,
    sectionOrder: 105, dialect: "",
    readResultGuard: false, readGuardDshRoot: "",
  };
  assert.deepEqual(validate(undefined), { value: defaults });
  assert.deepEqual(validate({}), { value: defaults });
  assert.deepEqual(validate({
    repoRoot: "/checkout", defaultCwd: "/workspace",
    promptSection: false, sectionOrder: 0, dialect: "PWSH",
    readResultGuard: false, readGuardDshRoot: "",
  }), { value: {
    repoRoot: "/checkout", defaultCwd: "/workspace",
    promptSection: false, sectionOrder: 0, dialect: "PWSH",
    readResultGuard: false, readGuardDshRoot: "",
  } });
  for (const value of [null, false, 4, "posix", []]) {
    const result = validate(value);
    assert.ok(result.issues?.length, `invalid config accepted: ${String(value)}`);
    assert.equal(result.value, undefined);
  }
  for (const [key, values] of Object.entries({
    repoRoot: [null, 5], defaultCwd: [false, null],
    promptSection: ["false", 0], sectionOrder: ["105", NaN, Infinity, -Infinity],
    dialect: [null, 42, " ", "bogus"],
    readResultGuard: ["false", 0], readGuardDshRoot: [null, 42],
  })) {
    for (const value of values) {
      const result = validate({ [key]: value });
      assert.ok(result.issues?.some((issue) => issue.path?.[0] === key),
        `${key}=${String(value)} silently defaulted`);
      assert.equal(result.value, undefined);
    }
  }
  const multiError = validate({ dialect: 42, promptSection: "yes" });
  assert.deepEqual(multiError.issues.map((issue) => issue.path[0]),
    ["promptSection", "dialect"]);
  assert.deepEqual(validate({ dialetc: "cmd" }).issues[0].path, ["dialetc"]);
  assert.ok(validate({ readResultGuard: true }).issues?.length);
  assert.equal(validate({ readResultGuard: true, readGuardDshRoot: "/host/dsh", defaultCwd: "/workspace" }).value.readResultGuard, true);

  const registered = [];
  const handlers = [];
  const sections = [];
  const requests = [];
  let guardReport = {
    decision: "BLOCK",
    code: "BLOCK_PROTECTED_PATH",
    explanation: "protected workspace root",
    reasons: ["protected: workspace-root (.)"],
  };
  let guardThrows = false;
  let guardExitCode = 0;
  let guardTimedOut = false;
  const statusReport = { workspace: "/synthetic/workspace", mode: "NORMAL", usage: { bytes: 0 } };
  let statusOutput = JSON.stringify(statusReport);
  let cleanup;

  const shell = {
    resolve: (request) => request,
    run: async (request) => {
      requests.push(request);
      if (guardThrows && request.command.includes("check.py")) {
        throw new Error("synthetic guard failure");
      }
      const report = request.command.includes("check.py")
        ? guardReport
        : {};
      return {
        stdout: { text: request.command.includes("status.py") ? statusOutput : JSON.stringify(report) },
        stderr: { text: "" },
        exitCode: guardExitCode,
        timedOut: guardTimedOut,
      };
    },
  };
  const systemPrompt = {
    section: (value) => {
      sections.push(value);
      return () => {};
    },
  };
  let sandboxPolicy;
  const ctx = {
    tools: {
      register: (tool) => {
        registered.push(tool);
        return () => {};
      },
    },
    get: (name) => ({ shell, systemPrompt, sandboxPolicy }[name]),
    on: (name, handler) => {
      handlers.push({ name, handler });
      return () => {};
    },
    effect: (factory) => {
      cleanup = factory();
    },
  };

  await adapter.apply(ctx, {
    repoRoot,
    defaultCwd: "",
    promptSection: true,
    sectionOrder: 0,
  });

  assert.equal(registered.length, 3);
  assert.deepEqual(
    registered.map((tool) => tool.name).sort(),
    ["agent_guard_restore", "agent_guard_safe_delete", "agent_guard_status"]
  );
  assert.equal(handlers.length, 1);
  assert.equal(handlers[0].name, "tools/pre-execute");
  assert.equal(sections.length, 1);
  assert.equal(sections[0].order, 0);
  assert.match(sections[0].text, /Never circumvent the guard/);

  let continued = 0;
  const next = () => {
    continued += 1;
    return { kind: "continued" };
  };
  const benign = await handlers[0].handler(
    { name: "bash", arguments: { command: "git status" } },
    next
  );
  assert.equal(benign.kind, "continued");
  const blocked = await handlers[0].handler(
    { name: "bash", arguments: { command: "rm -rf ." } },
    next
  );
  assert.equal(blocked.kind, "deny");
  assert.match(blocked.reason, /BLOCK_PROTECTED_PATH/);
  const windowsVerb = await handlers[0].handler(
    { name: "bash", arguments: { command: "del /s /q build" } },
    next
  );
  assert.equal(windowsVerb.kind, "deny");
  const gitWithGlobalOption = await handlers[0].handler(
    { name: "bash", arguments: { command: "git -C . reset --hard" } },
    next
  );
  assert.equal(gitWithGlobalOption.kind, "deny");
  const stdinScript = await handlers[0].handler(
    { name: "bash", arguments: { command: "printf 'rm -rf .' | sh" } },
    next
  );
  assert.equal(stdinScript.kind, "deny");
  assert.equal(continued, 1);
  assert.equal(requests.length, 4);
  assert.match(requests[0].command, /check\.py/);
  assert.match(requests[1].command, /del \/s \/q build/);

  guardReport = { decision: "ASK", code: "COMPOUND_CWD_DELETE",
    explanation: "split the command" };
  const asked = await handlers[0].handler(
    { name: "bash", arguments: { command: "cd src && rm item" } }, next
  );
  assert.equal(asked.kind, "ask");
  assert.match(asked.reason, /COMPOUND_CWD_DELETE/);
  assert.equal(continued, 1);

  guardReport = { decision: "ALLOW", compensations: [{ txid: "fixture" }] };
  const allowed = await handlers[0].handler(
    { name: "bash", arguments: { command: "rm build" } }, next
  );
  assert.equal(allowed.kind, "continued");
  assert.equal(continued, 2);

  const marker = "private_marker_7392_do_not_copy";
  const messages = [];
  const originalLog = console.log;
  const originalError = console.error;
  console.log = (...parts) => messages.push(parts.join(" "));
  console.error = (...parts) => messages.push(parts.join(" "));
  try {
    await handlers[0].handler(
      { name: "bash", arguments: { command: "rm build && printf " + marker } },
      () => ({ kind: "continued" })
    );
    guardReport = {};
    await handlers[0].handler(
      { name: "bash", arguments: { command: "rm " + marker } },
      () => ({ kind: "continued" })
    );
    guardThrows = true;
    await handlers[0].handler(
      { name: "bash", arguments: { command: "rm " + marker } },
      () => ({ kind: "continued" })
    );
  } finally {
    console.log = originalLog;
    console.error = originalError;
    guardThrows = false;
  }
  assert.ok(messages.length >= 3);
  assert.ok(messages.every((message) => !message.includes(marker)));

  guardReport = {};

  const malformed = await handlers[0].handler(
    { name: "bash", arguments: { command: "rm build" } }, next
  );
  assert.equal(malformed.kind, "deny");
  assert.match(malformed.reason, /fail-closed/);
  guardThrows = true;
  const failed = await handlers[0].handler(
    { name: "bash", arguments: { command: "rm build" } }, next
  );
  assert.equal(failed.kind, "deny");
  assert.match(failed.reason, /fail-closed/);
  assert.equal(continued, 2);
  guardThrows = false;

  const statusTool = registered.find(
    (tool) => tool.name === "agent_guard_status"
  );
  const toolResult = await statusTool.execute({}, {});
  assert.equal(toolResult.ok, true);
  assert.equal(toolResult.exitCode, 0);
  assert.deepEqual(toolResult.adapter, {
    startup_check: "PASSED", startup_scope: "plugin_process",
    hook_registered: true, last_checker_result: "CHECKER_EXECUTION_FAILED",
    host_interception: "UNVERIFIED",
    note: "Startup checks the plugin process Python only. Hook registration and a local status result do not verify live host interception or every shell environment.",
  });
  assert.equal(requests.length, 12);
  assert.match(requests[11].command, /status\.py/);
  const restoreTool = registered.find(
    (tool) => tool.name === "agent_guard_restore"
  );
  assert.equal(restoreTool.parameters.type, "object");
  assert.equal(restoreTool.parameters.properties.force, undefined);
  await restoreTool.execute({ txid: "fixture", force: true }, {});
  assert.equal(requests.length, 13);
  assert.doesNotMatch(requests[12].command, /--force/);
  for (const failure of [
    { exitCode: 0, timedOut: true, output: JSON.stringify(statusReport), code: "STATUS_TIMEOUT" },
    { exitCode: 127, output: "private_status_marker", code: "STATUS_PROCESS_FAILED" },
    ...["private_status_marker", "null", "[]", "{}", '{"decision":"ALLOW"}'].map(
      (output) => ({ exitCode: 0, output, code: "STATUS_INVALID_OUTPUT" })
    ),
  ]) {
    guardExitCode = failure.exitCode;
    guardTimedOut = failure.timedOut || false;
    statusOutput = failure.output;
    const unavailable = await statusTool.execute({}, {});
    assert.equal(unavailable.ok, false);
    assert.equal(unavailable.diagnostic, failure.code);
    assert.equal(unavailable.report, null);
    assert.equal(unavailable.adapter.host_interception, "UNVERIFIED");
    assert.equal(unavailable.adapter.last_checker_result, "CHECKER_EXECUTION_FAILED");
    assert.doesNotMatch(JSON.stringify(unavailable), /private_status_marker/);
  }
  guardExitCode = 0;
  guardTimedOut = false;
  statusOutput = JSON.stringify(statusReport);
  assert.equal((await statusTool.execute({}, {})).ok, true);
  const safeDeleteTool = registered.find(
    (tool) => tool.name === "agent_guard_safe_delete"
  );
  const longReport = { targets: ["x".repeat(6000)],
    storage_notice: "Retained data still uses disk space.",
    gc: { plan_command: "review-plan", purge_eligible_command: "explicit-purge" } };
  const preview = safeDeleteTool.output.render({}, { report: longReport })[0].text;
  assert.match(preview, /Retained data still uses disk space/);
  assert.match(preview, /review-plan/);
  const statusPreview = statusTool.output.render({}, {
    report: longReport, adapter: toolResult.adapter,
  })[0].text;
  assert.match(statusPreview, /hook_registered/);
  assert.match(statusPreview, /UNVERIFIED/);
  assert.deepEqual(safeDeleteTool.parameters.required, ["paths"]);
  await assert.rejects(
    safeDeleteTool.execute({}, {}),
    /missing required argument paths/
  );
  await assert.rejects(
    safeDeleteTool.execute({ paths: ["valid", 42] }, {}),
    /paths items must be strings/
  );
  // Native DSH 0.2 has execute()/result(), with no run(). Verify completion
  // and failures at both stages, preserving the caller's execution policy.
  const legacyRun = shell.run;
  delete shell.run;
  const nativeRequests = [];
  let nativeFailure;
  shell.execute = async (spec) => {
    nativeRequests.push(spec);
    if (nativeFailure === "prepare") throw new Error("synthetic prepare failure");
    return { result: async () => {
      if (nativeFailure === "result") throw new Error("synthetic spawn failure");
      return {
        stdout: { text: JSON.stringify(spec.command.includes("status.py") ? statusReport :
          { decision: "BLOCK", code: "BLOCK_PROTECTED_PATH" }) },
        stderr: { text: "" }, exitCode: 0,
      };
    } };
  };
  const nativeSignal = new AbortController().signal;
  const nativeExec = { name: "bash", signal: nativeSignal,
    arguments: { command: "rm -rf .", workdir: "/synthetic/workspace" } };
  const nativeBlocked = await handlers[0].handler(nativeExec, next);
  assert.equal(nativeBlocked.kind, "deny");
  assert.match(nativeBlocked.reason, /BLOCK_PROTECTED_PATH/);
  assert.equal(nativeRequests[0].signal, nativeSignal);
  assert.equal(nativeRequests[0].workdir, "/synthetic/workspace");
  assert.equal(nativeRequests[0].timeoutMs, 90000);
  // Resolve the calling session's policy afresh for root/child invocations;
  // a child must not inherit a cached policy from an earlier root call.
  const rootSession = { id: "synthetic-root-session" };
  const childSession = { id: "synthetic-child-session" };
  const policies = new Map([
    [rootSession, { mode: "read-only", workspaceRoot: "/synthetic/root" }],
    [childSession, { mode: "workspace-write", workspaceRoot: "/synthetic/child" }],
  ]);
  const resolvedSessions = [];
  sandboxPolicy = { resolve: ({ session }) => {
    resolvedSessions.push(session);
    return policies.get(session);
  } };
  for (const session of [rootSession, childSession, rootSession]) {
    const scopedExec = { ...nativeExec, agent: { session } };
    const result = await handlers[0].handler(scopedExec, next);
    assert.equal(result.kind, "deny");
    const request = nativeRequests.at(-1);
    assert.equal(request.sandboxPolicy, policies.get(session));
    assert.equal(request.signal, nativeSignal);
  }
  assert.deepEqual(resolvedSessions, [rootSession, childSession, rootSession]);
  sandboxPolicy = undefined;
  const nativeStatus = await statusTool.execute({}, {});
  assert.equal(nativeStatus.ok, true);
  // A legacy method must never be retried after the new execution started.
  shell.run = () => { throw new Error("legacy fallback must not run"); };
  for (const stage of ["prepare", "result"]) {
    nativeFailure = stage;
    const rejected = await handlers[0].handler(nativeExec, next);
    assert.equal(rejected.kind, "deny");
    assert.match(rejected.reason, /fail-closed/);
    const unavailable = await statusTool.execute({}, {});
    assert.equal(unavailable.ok, false);
    assert.equal(unavailable.diagnostic, "STATUS_EXECUTION_FAILED");
    assert.equal(unavailable.report, null);
    assert.equal(unavailable.adapter.last_checker_result, "CHECKER_EXECUTION_FAILED");
    assert.doesNotMatch(JSON.stringify(unavailable), /synthetic .* failure/);
  }
  assert.equal(continued, 2);
  delete shell.execute;
  shell.run = legacyRun;
  guardReport = { decision: "ALLOW", code: "ALLOW_NOOP" };
  guardExitCode = 127;
  const processFailure = await handlers[0].handler(nativeExec, next);
  assert.equal(processFailure.kind, "deny");
  assert.match(processFailure.reason, /CHECKER_PROCESS_FAILED/);
  guardExitCode = 0;
  guardTimedOut = true;
  const timedOut = await handlers[0].handler(nativeExec, next);
  assert.equal(timedOut.kind, "deny");
  assert.match(timedOut.reason, /CHECKER_TIMEOUT/);
  guardTimedOut = false;
  assert.equal(continued, 2);
  const failedStatus = await statusTool.execute({}, {});
  assert.equal(failedStatus.adapter.last_checker_result, "CHECKER_TIMEOUT");
  assert.equal(failedStatus.adapter.host_interception, "UNVERIFIED");
  assert.equal(typeof cleanup, "function");
  cleanup();
  assert.equal((await statusTool.execute({}, {})).adapter.hook_registered, false);
  const counts = [registered.length, handlers.length];
  await assert.rejects(adapter.apply({ get: () => undefined }, {
    repoRoot,
  }), /GUARD_STARTUP_FAILED.*shell service.*Other host tools may still run/);
  await assert.rejects(adapter.apply(ctx, {
    repoRoot: path.join(scratch, "missing_private_path"),
  }), (error) => error.message.includes("GUARD_STARTUP_FAILED") &&
    error.message.includes("check.py was not found") &&
    !error.message.includes("missing_private_path"));
  const originalPath = process.env.PATH;
  try {
    process.env.PATH = "";
    await assert.rejects(adapter.apply(ctx, { repoRoot }),
      /GUARD_STARTUP_FAILED.*Python checker could not run/);
  } finally {
    process.env.PATH = originalPath;
  }
  const brokenRoot = path.join(scratch, "broken");
  const brokenScripts = path.join(brokenRoot, "skills/delete-guard/scripts");
  await fs.mkdir(brokenScripts, { recursive: true });
  const brokenChecker = path.join(brokenScripts, "check.py");
  await fs.writeFile(brokenChecker, 'raise RuntimeError("private_startup_marker")\n');
  await assert.rejects(adapter.apply(ctx, { repoRoot: brokenRoot }),
    (error) => error.message.includes("Python checker could not run") &&
      !error.message.includes("private_startup_marker"));
  await fs.writeFile(brokenChecker, 'print("private_startup_marker" + "x" * 70000)\n');
  await assert.rejects(adapter.apply(ctx, { repoRoot: brokenRoot }),
    (error) => error.message.includes("startup check exceeded its output limit") &&
      !error.message.includes("private_startup_marker"));
  await fs.writeFile(brokenChecker, "import time\ntime.sleep(30)\n");
  await assert.rejects(adapter.apply(ctx, { repoRoot: brokenRoot }),
    /GUARD_STARTUP_FAILED.*startup check timed out/);
  assert.deepEqual([registered.length, handlers.length], counts);
  await assert.rejects(adapter.apply({ get: () => undefined }, {
    repoRoot, readResultGuard: true,
  }), /READ_GUARD_STARTUP_REFUSED/);
  console.log("DSH adapter smoke test passed");
} finally {
  await fs.rm(scratch, { recursive: true, force: true });
}
