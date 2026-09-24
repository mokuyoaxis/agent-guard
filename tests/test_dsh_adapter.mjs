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
source = source.replace(
  'import { defineTool } from "@deepseek-ai/dsh-tools";',
  "const defineTool = (definition) => definition;"
);

const scratch = await fs.mkdtemp(path.join(os.tmpdir(), "agent-guard-dsh-smoke-"));
const modulePath = path.join(scratch, "index.mjs");
await fs.writeFile(modulePath, source);

try {
  const adapter = await import(pathToFileURL(modulePath).href);
  const validate = adapter.Config["~standard"].validate;
  const defaults = {
    repoRoot: "", defaultCwd: "", promptSection: true,
    sectionOrder: 105, dialect: "",
  };
  assert.deepEqual(validate(undefined), { value: defaults });
  assert.deepEqual(validate({}), { value: defaults });
  assert.deepEqual(validate({
    repoRoot: "/checkout", defaultCwd: "/workspace",
    promptSection: false, sectionOrder: 0, dialect: "PWSH",
  }), { value: {
    repoRoot: "/checkout", defaultCwd: "/workspace",
    promptSection: false, sectionOrder: 0, dialect: "PWSH",
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
        stdout: { text: JSON.stringify(report) },
        stderr: { text: "" },
        exitCode: 0,
      };
    },
  };
  const systemPrompt = {
    section: (value) => {
      sections.push(value);
      return () => {};
    },
  };
  const ctx = {
    tools: {
      register: (tool) => {
        registered.push(tool);
        return () => {};
      },
    },
    get: (name) => ({ shell, systemPrompt }[name]),
    on: (name, handler) => {
      handlers.push({ name, handler });
      return () => {};
    },
    effect: (factory) => {
      cleanup = factory();
    },
  };

  adapter.apply(ctx, {
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
  assert.equal(continued, 1);
  assert.equal(requests.length, 2);
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
  assert.equal(requests.length, 10);
  assert.match(requests[9].command, /status\.py/);
  assert.equal(typeof cleanup, "function");
  cleanup();
  console.log("DSH adapter smoke test passed");
} finally {
  await fs.rm(scratch, { recursive: true, force: true });
}
