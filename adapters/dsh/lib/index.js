/**
 * agent-guard - DSH host adapter (Decision Protocol).
 *
 * A Cordis plugin for the DeepSeek Harness launcher composition. Three
 * default contributions:
 *
 *  1. Interception: a `tools/pre-execute` waterfall runs every destructive-
 *     looking bash command through the shared Python core (check.py
 *     --enforce) BEFORE execution, and maps the Decision Protocol onto
 *     PreToolDecision: ALLOW -> run (compensation already applied),
 *     ASK -> native ask (degrades to deny on harnesses without ask),
 *     BLOCK -> deny with explanation and remediation.
 *  2. Model tools: agent_guard_safe_delete / agent_guard_restore /
 *     agent_guard_status - the supported path is also the easiest path.
 *  3. Prompt guidance: deletion discipline as a system-prompt section.
 *  An optional pinned text-read result guard delegates to the shared exfil
 *  Core and replaces native values so content and metadata regenerate.
 *
 * The decision rules are NOT implemented here. This adapter only translates;
 * the single rule engine lives in skills/delete-guard/scripts/check.py,
 * shipped inside this package and resolved relative to this module unless
 * config.repoRoot points at a live checkout (development mode).
 *
 * Positioning: automatic recovery system with human escalation - reliability
 * infrastructure, not a security sandbox. See docs/design/threat-model.md.
 */

import fs from "node:fs";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const runFile = promisify(execFile);

export const name = "agent_guard";

/** Services that must resolve before apply() runs. */
export const inject = ["tools", "shell", "systemPrompt"];

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// lib -> adapters/dsh -> repository root (where core/ and skills/ live).
const PACKAGE_ROOT = path.resolve(__dirname, "..", "..", "..");
// Keep the configuration boundary aligned with core/dialects.py. Tool- or
// environment-supplied names still go to Core for a BLOCK_DIALECT_* verdict.
const CONFIG_DIALECTS = new Set([
  "posix", "sh", "bash", "zsh", "cmd", "cmd.exe", "batch", "bat",
  "powershell", "pwsh", "ps",
]);
const CONFIG_FIELDS = new Set([
  "repoRoot", "defaultCwd", "promptSection", "sectionOrder", "dialect",
  "readResultGuard", "readGuardDshRoot",
]);

// Keep the adapter installable without importing a host-private helper. DSH's
// public registry accepts plain Tool Definition objects; defineTool() only
// compiles this small authoring schema and wraps argument validation. Doing the
// same locally avoids coupling package resolution to DSH's internal dependency
// tree while retaining fail-closed validation at the tool boundary.
function compileParameters(spec) {
  const properties = {};
  const required = [];
  for (const [key, value] of Object.entries(spec)) {
    const { required: isRequired, ...schema } = value;
    properties[key] = schema;
    if (isRequired === true) required.push(key);
  }
  return {
    type: "object",
    properties,
    ...(required.length > 0 ? { required } : {}),
    additionalProperties: true,
  };
}

function assertToolArgs(name, spec, args) {
  if (args === null || typeof args !== "object" || Array.isArray(args)) {
    throw new TypeError(`${name}: arguments must be an object`);
  }
  for (const [key, schema] of Object.entries(spec)) {
    const value = args[key];
    if (value === undefined) {
      if (schema.required === true) {
        throw new TypeError(`${name}: missing required argument ${key}`);
      }
      continue;
    }
    if (schema.type === "string" && typeof value !== "string") {
      throw new TypeError(`${name}: ${key} must be a string`);
    }
    if (schema.type === "array") {
      if (!Array.isArray(value)) {
        throw new TypeError(`${name}: ${key} must be an array`);
      }
      if (schema.items?.type === "string" &&
          value.some((item) => typeof item !== "string")) {
        throw new TypeError(`${name}: ${key} items must be strings`);
      }
    }
  }
}

/**
 * Composition-row configuration (cordis.patch.yml).
 *
 * The loader validates this export through the Standard Schema interface
 * (`Config["~standard"].validate`), so a plain property bag cannot be used
 * here - resolveConfig would read `undefined.validate` and the whole plugin
 * tree would fail to load. Kept dependency-free on purpose: the guard ships
 * with no third-party imports, so the tiny standard-schema surface is
 * written by hand rather than pulling in schemastery.
 */
export const Config = {
  "~standard": {
    version: 1,
    vendor: "agent-guard",
    validate(value) {
      // Only an absent config gets defaults. An explicit null or wrong field
      // must not quietly turn a requested Windows dialect into POSIX.
      const input = value === undefined ? {} : value;
      if (input === null || typeof input !== "object" || Array.isArray(input)) {
        return { issues: [{ message: "agent-guard config must be an object" }] };
      }
      const issues = [];
      const config = {
        repoRoot: "",
        defaultCwd: "",
        promptSection: true,
        sectionOrder: 105,
        dialect: "",
        readResultGuard: false,
        readGuardDshRoot: "",
      };
      for (const key of Object.keys(input)) {
        if (!CONFIG_FIELDS.has(key)) {
          issues.push({ message: `unknown agent-guard config field: ${key}`, path: [key] });
        }
      }
      for (const key of ["repoRoot", "defaultCwd", "readGuardDshRoot"]) {
        if (!Object.prototype.hasOwnProperty.call(input, key)) continue;
        if (typeof input[key] !== "string") {
          issues.push({ message: `${key} must be a string`, path: [key] });
        } else {
          config[key] = input[key];
        }
      }
      if (Object.prototype.hasOwnProperty.call(input, "promptSection")) {
        if (typeof input.promptSection !== "boolean") {
          issues.push({ message: "promptSection must be a boolean", path: ["promptSection"] });
        } else {
          config.promptSection = input.promptSection;
        }
      }
      if (Object.prototype.hasOwnProperty.call(input, "sectionOrder")) {
        if (typeof input.sectionOrder !== "number" || !Number.isFinite(input.sectionOrder)) {
          issues.push({ message: "sectionOrder must be a finite number", path: ["sectionOrder"] });
        } else {
          config.sectionOrder = input.sectionOrder;
        }
      }
      if (Object.prototype.hasOwnProperty.call(input, "readResultGuard")) {
        if (typeof input.readResultGuard !== "boolean") {
          issues.push({ message: "readResultGuard must be a boolean", path: ["readResultGuard"] });
        } else {
          config.readResultGuard = input.readResultGuard;
        }
      }
      if (config.readResultGuard && !path.isAbsolute(config.readGuardDshRoot)) {
        issues.push({ message: "readGuardDshRoot must be an absolute pinned DSH package root", path: ["readGuardDshRoot"] });
      }
      if (config.readResultGuard && !path.isAbsolute(config.defaultCwd)) {
        issues.push({ message: "defaultCwd must be an absolute workspace for readResultGuard", path: ["defaultCwd"] });
      }
      if (Object.prototype.hasOwnProperty.call(input, "dialect")) {
        const requested = input.dialect;
        if (typeof requested !== "string" ||
            (requested !== "" && !CONFIG_DIALECTS.has(requested.trim().toLowerCase()))) {
          issues.push({ message: "dialect must name a supported shell dialect", path: ["dialect"] });
        } else {
          config.dialect = requested;
        }
      }
      return issues.length ? { issues } : { value: config };
    },
  },
};

// Aligned with core/classifier.py vocabulary (V1: Linux/macOS).
const DESTRUCTIVE_RE = new RegExp(
  "\\b(rm|rmdir|unlink|shred)\\b" +
    "|\\bfind\\b[^\\n|;&]*-delete\\b" +
    "|\\bgit\\b[^\\n|;&]*\\b(clean|reset|restore|checkout|push)\\b"
);

// Windows-native vocabulary (cmd / PowerShell). Screen both vocabularies
// regardless of dialect: the selector chooses the lexer, not whether a
// destructive-looking command reaches the guard at all.
const DESTRUCTIVE_RE_WINDOWS = new RegExp(
  "\\b(rm|ri|rd|rmdir|del|erase|remove-item)\\b" +
    "|-\\s?(recurse|force|whatif|literalpath)\\b" +
    "|\\bgit\\b[^\\n|;&]*\\b(clean|reset|restore|checkout|push)\\b",
  "i"
);

function dialectSelector(args, config, env) {
  const fromArgs = args && args.dialect;
  if (typeof fromArgs === "string" && fromArgs.trim()) return fromArgs;
  const fromEnv = env && env.AGENT_GUARD_DIALECT;
  if (typeof fromEnv === "string" && fromEnv.trim()) return fromEnv;
  if (typeof config.dialect === "string" && config.dialect.trim()) {
    return config.dialect;
  }
  return undefined;
}

/**
 * Build the adapter runtime for one apply() invocation.
 * @param ctx - registrant context carrying injected services.
 * @param config - validated composition-row configuration.
 */
function buildRuntime(ctx, config, shell, systemPrompt) {
  const repoRoot = config.repoRoot || PACKAGE_ROOT;
  const scriptsDir = path.join(repoRoot, "skills", "delete-guard", "scripts");
  const diagnostics = {
    startup_check: "NOT_RUN",
    startup_scope: "plugin_process",
    hook_registered: false,
    last_checker_result: "NOT_RUN",
    host_interception: "UNVERIFIED",
  };

  // The caller's sandbox policy travels with the session so guard
  // invocations inherit exactly what the calling agent already has.
  function policyFor(exec) {
    const service = ctx.get ? ctx.get("sandboxPolicy") : undefined;
    const session = exec && exec.agent && exec.agent.session;
    if (!service || session === undefined) return undefined;
    try {
      return service.resolve({ session });
    } catch (_) {
      return undefined;
    }
  }

  function shQuote(value) {
    return "'" + String(value).replace(/'/g, "'\\''") + "'";
  }

  function outText(collected) {
    if (collected === undefined || collected === null) return "";
    if (typeof collected === "string") return collected;
    return collected.text || "";
  }

  async function runScript(scriptName, argString, workdir, timeoutMs, exec) {
    const request = {
      command:
        "python3 " +
        shQuote(path.join(scriptsDir, scriptName)) +
        (argString ? " " + argString : ""),
      timeoutMs: timeoutMs || 45000,
      stdoutMaxBytes: 1048576,
      sandboxPolicy: policyFor(exec),
    };
    const dir = workdir || config.defaultCwd || undefined;
    if (dir) request.workdir = dir;
    if (exec && exec.signal) request.signal = exec.signal;
    const spec = shell.resolve(request);
    // DSH 0.2 returns a live execution handle; await its foreground result.
    // Keep the previously tested 0.1 API for existing installations.
    if (typeof shell.execute === "function") {
      return (await shell.execute(spec)).result();
    }
    return shell.run(spec);
  }

  function parseJson(text) {
    try {
      return JSON.parse(text);
    } catch (_) {
      return undefined;
    }
  }

  return {
    shell,
    systemPrompt,
    repoRoot,
    scriptsDir,
    diagnostics,
    shQuote,
    runScript,
    outText,
    parseJson,
  };
}

// Async functions cannot be mistaken for constructor plugins by Cordis.
// The host must await optional read-guard startup before admitting work.
export async function apply(ctx, config) {
  function refuseStartup(detail) {
    const code = config.readResultGuard ? "READ_GUARD_STARTUP_REFUSED" : "GUARD_STARTUP_FAILED";
    throw new Error(`[agent-guard] ${code}: ${detail} No guard hook or tools were registered. Other host tools may still run.`);
  }
  const shell = ctx.get ? ctx.get("shell") : undefined;
  if (shell === undefined) {
    refuseStartup("The shell service is unavailable.");
  }
  const systemPrompt = ctx.get ? ctx.get("systemPrompt") : undefined;
  const rt = buildRuntime(ctx, config, shell, systemPrompt);

  if (!fs.existsSync(path.join(rt.scriptsDir, "check.py"))) {
    refuseStartup("check.py was not found. Check the package or repoRoot setting.");
  }

  // --help loads Python and the checker imports without assessing a command
  // or writing guard metadata. This checks the plugin process environment;
  // the caller's shell/sandbox environment is only exercised by actual tools.
  try {
    await runFile("python3", [path.join(rt.scriptsDir, "check.py"), "--help"], {
      timeout: 10000,
      maxBuffer: 65536,
    });
  } catch (error) {
    refuseStartup(error.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" ?
      "The local checker startup check exceeded its output limit. Check the package and checker entry point." :
      error.killed ? "The local checker startup check timed out." :
      "The local Python checker could not run. Check python3 on the plugin process PATH and the checker imports.");
  }
  rt.diagnostics.startup_check = "PASSED";

  if (config.readResultGuard) {
    return import("./read_result_guard.js").then(async ({ installReadResultGuard }) => {
      await installReadResultGuard(ctx, config, rt.repoRoot);
      contribute(ctx, config, rt);
    }).catch(() => {
      throw new Error("[agent-guard] READ_GUARD_STARTUP_REFUSED");
    });
  }
  contribute(ctx, config, rt);
}

function contribute(ctx, config, rt) {
  const { systemPrompt } = rt;
  ctx.effect(() => {
    const disposers = [];

    function checkerFailure(code, detail) {
      rt.diagnostics.last_checker_result = code;
      const reason = `[agent-guard] ${code}: ${detail} Command denied (fail-closed).`;
      console.error(reason);
      return { kind: "deny", reason };
    }

    // ---- 1) interception -------------------------------------------------
    disposers.push(
      ctx.on("tools/pre-execute", async (exec, next) => {
        if (!exec || exec.name !== "bash") return next();
        const args = exec.arguments || {};
        const command =
          typeof args.command === "string" ? args.command : "";
        // A dialect selector is still forwarded to the Core for validation.
        const selector = dialectSelector(args, config, process.env);
        if (!command ||
            !(DESTRUCTIVE_RE.test(command) || DESTRUCTIVE_RE_WINDOWS.test(command))) {
          return next();
        }

        // An unusable selector is still forwarded: check.py owns the
        // verdict and BLOCKs it explicitly instead of silently guessing
        // a lexer.
        const dialectFlag =
          selector !== undefined ? "--dialect " + rt.shQuote(selector) : "";

        let result;
        try {
          result = await rt.runScript(
            "check.py",
            "--enforce --json" +
              (dialectFlag ? " " + dialectFlag : "") +
              " -- " +
              rt.shQuote(command),
            typeof args.workdir === "string" ? args.workdir : undefined,
            90000,
            exec
          );
        } catch (_err) {
          return checkerFailure("CHECKER_EXECUTION_FAILED",
            "The checker could not start or finish. Check Python and the caller's shell environment; inspect recovery state before retrying.");
        }
        if (result.timedOut) {
          return checkerFailure("CHECKER_TIMEOUT",
            "The checker timed out. Inspect recovery state before retrying.");
        }
        if (![0, 2, 3].includes(result.exitCode)) {
          return checkerFailure("CHECKER_PROCESS_FAILED",
            "The checker did not complete with a supported exit status. Check the Python runtime and recovery state.");
        }
        const verdict = rt.parseJson(rt.outText(result.stdout));
        if (!verdict || !["ALLOW", "ASK", "BLOCK"].includes(verdict.decision)) {
          return checkerFailure("CHECKER_INVALID_OUTPUT",
            "The checker returned no usable decision. Check the package and recovery state.");
        }
        rt.diagnostics.last_checker_result = "OK";
        if (verdict.decision === "ALLOW") {
          const comps = verdict.compensations || [];
          if (comps.length > 0) {
            console.log(
              "[agent-guard] compensated",
              comps.length,
              "check_id=" + (verdict.check_id || "unknown")
            );
          }
          if (verdict.storage_notice) console.log("[agent-guard]", verdict.storage_notice,
            "Use agent_guard_status for usage and GC commands.");
          return next();
        }
        const reasons = Array.isArray(verdict.reasons)
          ? verdict.reasons.join("; ")
          : "";
        if (verdict.decision === "ASK") {
          return {
            kind: "ask",
            reason:
              "[agent-guard] " +
              verdict.code +
              ": " +
              (verdict.explanation || reasons),
          };
        }
        return {
          kind: "deny",
          reason:
            "[agent-guard] BLOCKED [" +
            verdict.code +
            "] " +
            (verdict.explanation || "") +
            (reasons ? " (" + reasons + ")" : "") +
            " | Restate with explicit workspace-relative paths, or use " +
            "agent_guard_safe_delete. Do NOT circumvent the guard.",
        };
      })
    );
    rt.diagnostics.hook_registered = true;

    // ---- 2) model tools --------------------------------------------------
    function renderJson(_args, value) {
      // Keep diagnostics and storage guidance visible before long target/audit
      // arrays consume the existing model-facing preview budget.
      const display = {
        ...(value.adapter ? { adapter: value.adapter } : {}),
        ...(value.report?.storage_notice ? {
          storage_notice: value.report.storage_notice,
          gc: value.report.gc,
        } : {}),
        ...value,
      };
      return [
        { type: "text", text: JSON.stringify(display, null, 2).slice(0, 4000) },
      ];
    }

    const CWD_PROP = {
      type: "string",
      description:
        "Working directory (repo root) for this operation; defaults to the session workspace.",
    };

    function adapterStatus() {
      return {
        ...rt.diagnostics,
        note: "Startup checks the plugin process Python only. Hook registration and a local status result do not verify live host interception or every shell environment.",
      };
    }

    function statusFailure(diagnostic, detail, exitCode = null) {
      return {
        ok: false,
        exitCode,
        report: null,
        diagnostic,
        stderr: detail,
        adapter: adapterStatus(),
      };
    }

    function makeTool(name, description, parameterSpec, buildArgs) {
      return {
        name,
        description,
        parameters: compileParameters(parameterSpec),
        output: {
          schema: { type: "object", additionalProperties: true },
          render: renderJson,
        },
        execute: async (args, exec) => {
          assertToolArgs(name, parameterSpec, args);
          const plan = buildArgs(args);
          let result;
          try {
            result = await rt.runScript(
              plan.script,
              plan.argString,
              plan.workdir,
              120000,
              exec
            );
          } catch (error) {
            if (name !== "agent_guard_status") throw error;
            return statusFailure("STATUS_EXECUTION_FAILED",
              "Status could not run in the caller's shell environment. Check Python and the package; adapter diagnostics below remain available.");
          }
          const parsed = rt.parseJson(rt.outText(result.stdout));
          if (name === "agent_guard_status") {
            if (result.timedOut) {
              return statusFailure("STATUS_TIMEOUT",
                "Status timed out. No recovery report is available; inspect the workspace before retrying.", result.exitCode);
            }
            if (result.exitCode !== 0) {
              return statusFailure("STATUS_PROCESS_FAILED",
                "Status exited unsuccessfully. Check Python, the package and recovery data.", result.exitCode);
            }
            if (typeof parsed?.workspace !== "string" || typeof parsed?.mode !== "string" ||
                !Number.isFinite(parsed?.usage?.bytes)) {
              return statusFailure("STATUS_INVALID_OUTPUT",
                "Status returned no usable recovery report. Check the package and caller's shell environment.", result.exitCode);
            }
          }
          return {
            ok: result.exitCode === 0,
            exitCode: result.exitCode,
            report: parsed === undefined ? rt.outText(result.stdout) : parsed,
            stderr: rt.outText(result.stderr).slice(0, 2000),
            ...(name === "agent_guard_status" ? {
              adapter: adapterStatus(),
            } : {}),
          };
        },
      };
    }

    disposers.push(
      ctx.tools.register(
        makeTool(
          "agent_guard_status",
          "Show adapter diagnostics, recovery mode, quarantine usage and GC commands. Local status does not verify live host interception.",
          { cwd: CWD_PROP },
          (args) => ({ script: "status.py", argString: "--json", workdir: args.cwd })
        )
      )
    );
    disposers.push(
      ctx.tools.register(
        makeTool(
          "agent_guard_safe_delete",
          "Delete with the existing recovery policy: valuable targets go to quarantine and still use disk space; eligible generated artifacts may be deleted directly. Accepts paths and globs.",
          {
            paths: {
              type: "array",
              items: { type: "string" },
              required: true,
              description: "Paths or globs relative to the workspace root.",
            },
            reason: { type: "string", description: "Why this deletion is needed." },
            cwd: CWD_PROP,
          },
          (args) => {
            const parts = args.paths.map((p) => rt.shQuote(p));
            parts.push("--json");
            if (args.reason) parts.push("--reason " + rt.shQuote(args.reason));
            return { script: "safe_delete.py", argString: parts.join(" "), workdir: args.cwd };
          }
        )
      )
    );
    disposers.push(
      ctx.tools.register(
        makeTool(
          "agent_guard_restore",
          "List quarantine transactions, or restore one by txid. Refuses to overwrite an occupied origin path.",
          {
            txid: { type: "string", description: "Omit to list transactions." },
            cwd: CWD_PROP,
          },
          (args) => {
            if (!args.txid)
              return { script: "restore.py", argString: "list --json", workdir: args.cwd };
            return {
              script: "restore.py",
              argString: "restore " + rt.shQuote(args.txid) + " --json",
              workdir: args.cwd,
            };
          }
        )
      )
    );

    // ---- 3) prompt guidance ----------------------------------------------
    if (systemPrompt !== undefined && config.promptSection !== false) {
      disposers.push(
        systemPrompt.section({
          name: "agent-guard-delete-discipline",
          order: config.sectionOrder ?? 105,
          text: [
            "## Deletion discipline (agent-guard)",
            "",
            "Agent Guard checks supported destructive-looking Bash commands and provides recovery tools. Other tools and scripts may be outside its coverage.",
            "",
            "- To delete files/directories/globs, prefer agent_guard_safe_delete. Valuable targets move to quarantine and still use disk space; eligible generated artifacts may be deleted directly. Pass cwd for the intended repo. Status includes GC planning and explicit purge commands.",
            "- Raw rm / git clean / git reset --hard are intercepted before execution. Most are compensated automatically (relocate or snapshot) and simply proceed; note the txid.",
            "- Shape rules: a command line that cds before deleting (COMPOUND_CWD_DELETE), or creates files and then deletes them (COMPOUND_CREATE_DELETE), triggers a single-execution user authorization. Splitting the deletion into its own standalone command avoids the prompt entirely.",
            "- True effect-uncertainty ($VAR targets, bash -c, find -delete, xargs-fed lists) stays hard-BLOCKED: restating with explicit concrete paths is the only remedy.",
            "- BLOCK_OUT_OF_WORKSPACE, BLOCK_PROTECTED_PATH (.git, workspace root) and BLOCK_FORCE_PUSH are policy violations and never askable: do not retry variants; ask the human if truly needed.",
            "- In RESTRICTED mode only explicit single-file deletes inside the workspace are permitted; only a human can restore NORMAL.",
            "- Never circumvent the guard (no /bin/rm, python os.unlink, node fs.rmSync, relocated scripts). Calls outside the covered path may have no recovery or audit record.",
            "- Undo with agent_guard_restore(txid). Inspect state with agent_guard_status().",
          ].join("\n"),
        })
      );
    }

    console.log("[agent-guard] Hook registered; local Python checker passed. Live host interception is unverified. Inspect agent_guard_status for diagnostics.");
    return () => {
      rt.diagnostics.hook_registered = false;
      while (disposers.length) {
        const d = disposers.pop();
        try {
          d();
        } catch (_) {
          /* best effort */
        }
      }
    };
  }, "agent-guard-contributions");
}
