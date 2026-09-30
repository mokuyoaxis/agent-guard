// Research probe against the installed pinned DSH runtime. No model or shell.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const root = process.argv[2];
const marker = "DSH_LAB_SYNTHETIC_RESULT";
let context;
let stage = "package";
try {
  if (!root || process.argv.length !== 3) throw new Error("select a DSH package root");
  const selected = fs.realpathSync(root);
  const host = JSON.parse(fs.readFileSync(path.join(selected, "package.json"), "utf8"));
  const dependencyVersion = { "0.1.5-rc.1": "0.1.5-rc.2", "0.2.0-rc.2": "0.2.0-rc.2" }[host.version];
  if (host.name !== "@deepseek-ai/dsh" || !dependencyVersion) {
    throw new Error("unsupported host version");
  }
  const runtimeManifest = JSON.parse(fs.readFileSync(path.join(selected, "node_modules", "@deepseek-ai", "dsh-tools", "package.json"), "utf8"));
  if (runtimeManifest.version !== dependencyVersion) throw new Error("unsupported tool runtime version");
  const moduleUrl = (name) => pathToFileURL(path.join(selected, "node_modules", "@deepseek-ai", name, "lib/index.js")).href;
  const { Context } = await import(moduleUrl("cordis"));
  const { SystemPrompt } = await import(moduleUrl("dsh-system-prompt"));
  const { ToolRuntime } = await import(moduleUrl("dsh-tools"));
  stage = "services";
  context = new Context();
  await context.plugin(SystemPrompt, { includeRuntimeContext: false });
  await context.plugin(ToolRuntime, {});
  const tools = context.get("tools");
  if (!tools) throw new Error("tool service unavailable");
  let postCalls = 0;
  const notified = [];
  context.on("tools/post-execute", async (exec, _result, next) => {
    postCalls += 1;
    const decision = await next();
    if (decision.kind === "block") return decision;
    if (exec.name === "lab_value") return { kind: "accept", value: "[synthetic replacement]" };
    return { kind: "accept", content: [{ type: "text", text: "[synthetic replacement]" }] };
  });
  context.on("tools/result", (_exec, result) => notified.push(result));
  function register(name, finalizer) {
    tools.register({
      name, description: "Synthetic result transformation probe",
      parameters: { type: "object", properties: {}, additionalProperties: false },
      output: {
        schema: { type: "string" },
        render: (_args, value) => [{ type: "text", text: value }],
        presentationMeta: (_args, value) => ({ syntheticPrivate: value }),
      },
      execute: async () => marker,
      ...(finalizer ? { finalizeContent: () => [{ type: "text", text: marker }] } : {}),
    });
  }
  stage = "registration";
  register("lab_plain", false);
  register("lab_finalizer", true);
  register("lab_value", false);
  const signal = new AbortController().signal;
  stage = "execution";
  const plain = await tools.execute({ name: "lab_plain", callId: "lab-plain", arguments: {}, signal });
  const finalized = await tools.execute({ name: "lab_finalizer", callId: "lab-finalizer", arguments: {}, signal });
  const replaced = await tools.execute({ name: "lab_value", callId: "lab-value", arguments: {}, signal });
  const beforeFailure = postCalls;
  await tools.execute({ name: "lab_plain", callId: "lab-invalid", arguments: { invalid: 1n }, signal });
  const result = {
    harness_version: host.version, node_version: process.version,
    tool_runtime_version: runtimeManifest.version,
    model_calls: 0, shell_calls: 0,
    post_execute_replaces_content: !JSON.stringify(plain.content).includes(marker),
    presentation_meta_keeps_original: JSON.stringify(plain.meta).includes(marker),
    value_replacement_regenerates_meta: !JSON.stringify(replaced).includes(marker),
    later_finalizer_can_reintroduce_content: JSON.stringify(finalized.content).includes(marker),
    invalid_args_skip_post_execute: postCalls === beforeFailure,
    authoritative_result_observed: notified.includes(plain) && notified.includes(finalized),
    result_is_frozen: Object.isFrozen(plain), post_execute_calls: postCalls,
  };
  stage = "contract";
  if (!Object.entries(result).filter(([, value]) => typeof value === "boolean").every(([, value]) => value)) {
    process.stdout.write(JSON.stringify(result) + "\n");
    throw new Error("pinned result contract changed");
  }
  process.stdout.write(JSON.stringify(result) + "\n");
} catch {
  process.stderr.write("DSH result-hook probe refused or failed at " + stage + "; raw diagnostics suppressed\n");
  process.exitCode = 1;
} finally {
  if (context) {
    try { await context.fiber.dispose(); } catch { process.exitCode = 1; }
  }
}
