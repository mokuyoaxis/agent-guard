// Experimental, default-off policy boundary for one pinned native text read.
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

export const MAX_INPUT_BYTES = 1024 * 1024;
const MAX_OUTPUT_BYTES = 2 * MAX_INPUT_BYTES;
const WORKER = fileURLToPath(new URL("./read_result_guard.py", import.meta.url));
const LEGACY_PINNED = {
  "dsh-tools": "aaba52bf5d0149355407642b3965c06977d1e9143f5c61bc19429abbe6a11c5d",
  "dsh-tool-fs": "57cbfdf103d663e169cc4df3c089c4a8a260f5a05f2f99c9a97f177f7e059639",
  "dsh-fs-local": "7ea33bc933daf5a60600dce74915510355a605473dc935041280be6a16fdd34b",
};
const RUNTIMES = {
  "0.1.5-rc.1": { dependencyVersion: "0.1.5-rc.2", artifacts: LEGACY_PINNED },
  "0.2.0-rc.2": { dependencyVersion: "0.2.0-rc.2", artifacts: {
    "dsh-tools": "40f47709337c3c205d4e09e647f8588f4977e66f6d52f019b3cc7ef81159d84f",
    "dsh-tool-fs": "66742231de99695b98400cdbc7bcf47b8ef2bf24600cf86f66bb35591981427f",
    "dsh-fs-local": "63fbb41d2c33e07111884b798be507e68c2752acab8249c821c20ade436e894f",
    "dsh-fs-sandbox": "cac65e21a0f0895b073cb9a447196ac265f2a171cc7f67c7434a6b3b7782caf5",
  } },
};
const CODES = new Set([
  "READ_RESULT_SCHEMA", "READ_RESULT_INCOMPLETE", "READ_RESULT_POLICY",
  "READ_RESULT_SCAN_FAILED", "READ_RESULT_SIZE", "READ_RESULT_TIMEOUT",
  "READ_RESULT_CANCELLED", "READ_RESULT_BINDING", "READ_RESULT_CONTEXT",
]);

export function blockRead(code) {
  const safe = CODES.has(code) ? code : "READ_RESULT_SCAN_FAILED";
  return { kind: "block", feedback: [{ type: "text", text: `[agent-guard] ${safe}: text read withheld.` }] };
}

// Fixed executable and script, no shell or file payload. The worker does not
// read the target file; the native filesystem provider remains its owner.
export function scanReadValue(value, workspace, signal, options = {}) {
  let payload;
  try {
    payload = Buffer.from(JSON.stringify({ workspace, value }), "utf8");
    if (payload.length > MAX_INPUT_BYTES) return Promise.resolve(blockRead("READ_RESULT_SIZE"));
  } catch { return Promise.resolve(blockRead("READ_RESULT_SCHEMA")); }
  if (signal?.aborted) return Promise.resolve(blockRead("READ_RESULT_CANCELLED"));
  return new Promise((resolve) => {
    let child, timer, done = false, size = 0;
    const chunks = [];
    const finish = (decision) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      resolve(decision);
    };
    const stop = (code) => {
      child?.kill("SIGKILL");
      finish(blockRead(code));
    };
    const abort = () => stop("READ_RESULT_CANCELLED");
    try {
      child = (options.spawnProcess ?? spawn)("python3", ["-I", WORKER], {
        stdio: ["pipe", "pipe", "pipe"], windowsHide: true,
      });
      timer = setTimeout(() => stop("READ_RESULT_TIMEOUT"), options.timeoutMs ?? 10000);
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) abort();
      child.on("error", () => stop("READ_RESULT_SCAN_FAILED"));
      child.stdin.on("error", () => stop("READ_RESULT_SCAN_FAILED"));
      // Suppress worker diagnostics, including interpreter/import failures.
      child.stderr.on("data", () => {});
      child.stdout.on("data", (chunk) => {
        if (done) return;
        size += chunk.length;
        if (size > MAX_OUTPUT_BYTES) return stop("READ_RESULT_SIZE");
        chunks.push(chunk);
      });
      child.on("close", (code) => {
        if (done) return;
        if (code !== 0) return finish(blockRead("READ_RESULT_SCAN_FAILED"));
        try {
          const reply = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (reply.kind === "block") return finish(blockRead(reply.code));
          if (reply.kind !== "accept" || !["ALLOW", "SANITIZE"].includes(reply.decision) ||
              !Number.isSafeInteger(reply.redactions) || reply.redactions < 0 || !reply.value) {
            return finish(blockRead("READ_RESULT_SCAN_FAILED"));
          }
          finish({ kind: "accept", value: reply.value });
        } catch { finish(blockRead("READ_RESULT_SCAN_FAILED")); }
      });
      child.stdin.end(payload);
    } catch { stop("READ_RESULT_SCAN_FAILED"); }
  });
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}
const schema = {
  type: "object", additionalProperties: false,
  properties: {
    path: { type: "string" }, offset: { type: "integer" }, totalLines: { type: "integer" },
    lines: { type: "array", items: {
      type: "object", additionalProperties: false,
      properties: { number: { type: "integer" }, text: { type: "string" } }, required: ["number", "text"],
    } },
  }, required: ["path", "offset", "lines", "totalLines"],
};

export function pinReadBinding(tools) {
  const definition = tools.get("read");
  if (!definition || definition.finalizeContent !== undefined || definition.projectContent !== undefined ||
      typeof definition.execute !== "function" || typeof definition.output?.render !== "function" ||
      typeof definition.output?.presentationMeta !== "function" ||
      JSON.stringify(canonical(definition.output.schema)) !== JSON.stringify(canonical(schema))) {
    throw new Error("[agent-guard] READ_GUARD_STARTUP_REFUSED");
  }
  const execute = definition.execute, render = definition.output.render, meta = definition.output.presentationMeta;
  const schemaSnapshot = JSON.stringify(canonical(definition.output.schema));
  // Conformance check against the pinned read projectors. Plugin composition
  // is trusted; this check is not authentication of arbitrary plugin code.
  const value = { path: "guard-probe.ini", offset: 1, lines: [{ number: 1, text: "PORT=8080" }], totalLines: 1 };
  const rendered = render({ file_path: value.path }, value);
  const expected = "<path>guard-probe.ini</path>\n<type>file</type>\n<content>\n1: PORT=8080\n\n(End of file - total 1 lines)\n</content>";
  const projected = meta({ file_path: value.path }, value);
  if (JSON.stringify(rendered) !== JSON.stringify([{ type: "text", text: expected }]) ||
      JSON.stringify(canonical(projected)) !== JSON.stringify(canonical({ ...value, lang: "ini" }))) {
    throw new Error("[agent-guard] READ_GUARD_STARTUP_REFUSED");
  }
  return (exec) => {
    const current = tools.get("read", exec.agent);
    return current === definition && current.execute === execute && current.output.render === render &&
      current.output.presentationMeta === meta && current.finalizeContent === undefined &&
      current.projectContent === undefined && JSON.stringify(canonical(current.output.schema)) === schemaSnapshot;
  };
}

export async function verifyReadRuntime(root, ctx) {
  try {
    if (!path.isAbsolute(root)) throw new Error();
    const selected = fs.realpathSync(root);
    const host = JSON.parse(fs.readFileSync(path.join(selected, "package.json"), "utf8"));
    const runtime = Object.hasOwn(RUNTIMES, host.version) ? RUNTIMES[host.version] : undefined;
    if (host.name !== "@deepseek-ai/dsh" || !runtime ||
        Number(process.versions.node.split(".")[0]) !== 22) throw new Error();
    const url = (name) => pathToFileURL(path.join(selected, "node_modules", "@deepseek-ai", name, "lib/index.js")).href;
    for (const [name, hash] of Object.entries(runtime.artifacts)) {
      const directory = path.join(selected, "node_modules", "@deepseek-ai", name);
      const manifest = JSON.parse(fs.readFileSync(path.join(directory, "package.json"), "utf8"));
      if (manifest.name !== "@deepseek-ai/" + name || manifest.version !== runtime.dependencyVersion ||
          createHash("sha256").update(fs.readFileSync(path.join(directory, "lib/index.js"))).digest("hex") !== hash) {
        throw new Error();
      }
    }
    const { ToolRuntime } = await import(url("dsh-tools"));
    const { LocalFileSystem } = await import(url("dsh-fs-local"));
    if (!(ctx.get("tools") instanceof ToolRuntime) || !(ctx.get("fs") instanceof LocalFileSystem)) throw new Error();
    if (host.version === "0.2.0-rc.2") {
      const { SandboxedFileSystem } = await import(url("dsh-fs-sandbox"));
      // Cordis wraps service methods, including constructor; the prototype
      // retains the owning provider's identity without invoking a method.
      if (![LocalFileSystem.prototype, SandboxedFileSystem.prototype].includes(Object.getPrototypeOf(ctx.get("fs")))) throw new Error();
    }
  } catch { throw new Error("[agent-guard] READ_GUARD_STARTUP_REFUSED"); }
}

export async function installReadResultGuard(ctx, config, repoRoot) {
  try {
    // The worker and Core must belong to the same checkout/package.
    const ownRoot = fs.realpathSync(path.resolve(path.dirname(WORKER), "..", "..", ".."));
    if (fs.realpathSync(repoRoot) !== ownRoot || !path.isAbsolute(config.defaultCwd)) throw new Error();
    const workspace = fs.realpathSync(config.defaultCwd);
    await verifyReadRuntime(config.readGuardDshRoot, ctx);
    const tools = ctx.get("tools");
    const matches = pinReadBinding(tools);
    const readiness = await scanReadValue({ path: "guard-probe.ini", offset: 1, lines: [], totalLines: 0 }, workspace);
    if (readiness.kind !== "accept") throw new Error();
    ctx.effect(() => {
      const disposeGuard = tools.guard((exec) => {
        if (exec.name !== "read") return;
        if (exec.parent !== undefined || !matches(exec)) return "[agent-guard] READ_RESULT_BINDING";
      });
      const disposePost = ctx.on("tools/post-execute", async (exec, result, next) => {
        if (exec.name !== "read") return next();
        try {
          const decision = await next();
          if (!matches(exec) || exec.parent !== undefined) return blockRead("READ_RESULT_BINDING");
          if (decision.kind === "block") return blockRead("READ_RESULT_POLICY");
          const emptyContexts = (contexts) => contexts === undefined || (Array.isArray(contexts) && contexts.length === 0);
          if (decision.kind !== "accept" || Object.hasOwn(decision, "content") ||
              !emptyContexts(decision.additionalContexts) || !emptyContexts(result.additionalContexts)) {
            return blockRead("READ_RESULT_CONTEXT");
          }
          if (exec.agent?.session?.header?.cwd && fs.realpathSync(exec.agent.session.header.cwd) !== workspace) {
            return blockRead("READ_RESULT_BINDING");
          }
          if (result.isError) return blockRead("READ_RESULT_SCAN_FAILED");
          return await scanReadValue(Object.hasOwn(decision, "value") ? decision.value : result.value, workspace, exec.signal);
        } catch { return blockRead("READ_RESULT_SCAN_FAILED"); }
      }, { prepend: true });
      return () => { disposePost(); disposeGuard(); };
    });
  } catch { throw new Error("[agent-guard] READ_GUARD_STARTUP_REFUSED"); }
}
