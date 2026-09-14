/**
 * agent-guard - DSH host adapter (Decision Protocol).
 *
 * A Cordis plugin for the DeepSeek Harness launcher composition. Three
 * contributions:
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
 *
 * The decision rules are NOT implemented here. This adapter only translates;
 * the single rule engine lives in skills/delete-guard/scripts/check.py,
 * shipped inside this package and resolved relative to this module unless
 * config.repoRoot points at a live checkout (development mode).
 *
 * Positioning: automatic recovery system with human escalation - reliability
 * infrastructure, not a security sandbox. See docs/threat-model.md.
 */

import { defineTool } from "@deepseek-ai/dsh-tools";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const name = "agent_guard";

/** Services that must resolve before apply() runs. */
export const inject = ["tools", "shell", "systemPrompt"];

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// lib -> adapters/dsh -> repository root (where core/ and skills/ live).
const PACKAGE_ROOT = path.resolve(__dirname, "..", "..", "..");

/** Composition-row configuration (cordis.patch.yml). */
export const Config = {
  /** Absolute path to an agent-guard checkout; empty = use this package. */
  repoRoot: { type: "string", default: "" },
  /** Default working directory for guard invocations; empty = session cwd. */
  defaultCwd: { type: "string", default: "" },
  /** Register the deletion-discipline prompt section. */
  promptSection: { type: "boolean", default: true },
  /** System-prompt section order (persona 0, tool guidance 100-199). */
  sectionOrder: { type: "number", default: 105 },
  /** Default shell dialect for guard invocations; empty = posix. */
  dialect: { type: "string", default: "" },
};

// Aligned with core/classifier.py vocabulary (V1: Linux/macOS).
const DESTRUCTIVE_RE = new RegExp(
  "(^|[\\s;&|(\\/])(rm|rmdir|unlink|shred)\\b" +
    "|\\bfind\\b[^\\n|;&]*-delete\\b" +
    "|\\bgit\\s+(clean|reset|restore|checkout|push)\\b"
);

// Windows-native vocabulary (cmd / PowerShell). Used only when the session
// requested a non-posix dialect: `ri build -r -fo` never matches the POSIX
// regex, so a Windows session would otherwise skip the guard entirely.
const DESTRUCTIVE_RE_WINDOWS = new RegExp(
  "(^|[\\s;&|(\\\\/])(rm|ri|rd|rmdir|del|erase|remove-item)\\b" +
    "|-\\s?(recurse|force|whatif|literalpath)\\b" +
    "|\\bgit\\s+(clean|reset|restore|checkout|push)\\b",
  "i"
);

// Dialect selectors recognised by core/dialects.py. Kept here only to pick
// the prefilter and to decide whether to pass `--dialect`; the authoritative
// validation (and the BLOCK for an unusable selector) stays in check.py.
const POSIX_ALIASES = new Set(["posix", "sh", "bash", "zsh"]);

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

function isPosixDialect(selector) {
  if (selector === undefined) return true;
  return POSIX_ALIASES.has(String(selector).trim().toLowerCase());
}

/**
 * Build the adapter runtime for one apply() invocation.
 * @param ctx - registrant context carrying injected services.
 * @param config - validated composition-row configuration.
 */
function buildRuntime(ctx, config, shell, systemPrompt) {
  const repoRoot = config.repoRoot || PACKAGE_ROOT;
  const scriptsDir = path.join(repoRoot, "skills", "delete-guard", "scripts");
  const pythonReady = fs.existsSync(path.join(scriptsDir, "check.py"));

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

  function runScript(scriptName, argString, workdir, timeoutMs, exec) {
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
    return shell.run(shell.resolve(request));
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
    pythonReady,
    shQuote,
    runScript,
    outText,
    parseJson,
  };
}

export function apply(ctx, config) {
  const shell = ctx.get ? ctx.get("shell") : undefined;
  if (shell === undefined) {
    console.error("[agent-guard] shell service unavailable; adapter disabled");
    return;
  }
  const systemPrompt = ctx.get ? ctx.get("systemPrompt") : undefined;
  const rt = buildRuntime(ctx, config, shell, systemPrompt);

  if (!rt.pythonReady) {
    console.error("[agent-guard] check.py not found under", rt.repoRoot);
    return;
  }

  ctx.effect(() => {
    const disposers = [];

    // ---- 1) interception -------------------------------------------------
    disposers.push(
      ctx.on("tools/pre-execute", async (exec, next) => {
        if (!exec || exec.name !== "bash") return next();
        const args = exec.arguments || {};
        const command =
          typeof args.command === "string" ? args.command : "";
        // Dialect first: the prefilter cannot see Windows-native syntax.
        const selector = dialectSelector(args, config, process.env);
        const posix = isPosixDialect(selector);
        const prefilter = posix ? DESTRUCTIVE_RE : DESTRUCTIVE_RE_WINDOWS;
        if (!command || !prefilter.test(command)) return next();

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
        } catch (err) {
          console.error("[agent-guard] check.py threw:", err);
          return {
            kind: "deny",
            reason:
              "[agent-guard] guard infrastructure error (fail-closed): " + err,
          };
        }
        const verdict = rt.parseJson(rt.outText(result.stdout));
        if (!verdict || !verdict.decision) {
          console.error(
            "[agent-guard] unparseable guard output:",
            rt.outText(result.stdout),
            rt.outText(result.stderr)
          );
          return {
            kind: "deny",
            reason: "[agent-guard] unparseable guard output (fail-closed)",
          };
        }
        if (verdict.decision === "ALLOW") {
          const comps = verdict.compensations || [];
          if (comps.length > 0) {
            console.log(
              "[agent-guard] compensated",
              JSON.stringify(comps),
              "<-",
              command.slice(0, 120)
            );
          }
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

    // ---- 2) model tools --------------------------------------------------
    function renderJson(_args, value) {
      return [
        { type: "text", text: JSON.stringify(value, null, 2).slice(0, 4000) },
      ];
    }

    const CWD_PROP = {
      type: "string",
      description:
        "Working directory (repo root) for this operation; defaults to the session workspace.",
    };

    function makeTool(name, description, parameters, buildArgs) {
      return defineTool({
        name,
        description,
        parameters,
        output: {
          schema: { type: "object", additionalProperties: true },
          render: renderJson,
        },
        execute: async (args, exec) => {
          const plan = buildArgs(args);
          const result = await rt.runScript(
            plan.script,
            plan.argString,
            plan.workdir,
            120000,
            exec
          );
          const parsed = rt.parseJson(rt.outText(result.stdout));
          return {
            ok: result.exitCode === 0,
            exitCode: result.exitCode,
            report: parsed === undefined ? rt.outText(result.stdout) : parsed,
            stderr: rt.outText(result.stderr).slice(0, 2000),
          };
        },
      });
    }

    disposers.push(
      ctx.tools.register(
        makeTool(
          "agent_guard_status",
          "Show agent-guard state: authorization mode, quarantine usage, retention/GC view, recent decisions.",
          { cwd: CWD_PROP },
          (args) => ({ script: "status.py", argString: "--json", workdir: args.cwd })
        )
      )
    );
    disposers.push(
      ctx.tools.register(
        makeTool(
          "agent_guard_safe_delete",
          "Recoverable delete: globs expanded explicitly, targets quarantined to .agent-trash/ with a manifest. Preferred over raw rm.",
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
          "List quarantine transactions, or restore one by txid (non-destructive: refuses overwrites unless force).",
          {
            txid: { type: "string", description: "Omit to list transactions." },
            force: {
              type: "boolean",
              description: "Overwrite existing origin paths (human decision).",
            },
            cwd: CWD_PROP,
          },
          (args) => {
            if (!args.txid)
              return { script: "restore.py", argString: "list --json", workdir: args.cwd };
            let s = "restore " + rt.shQuote(args.txid) + " --json";
            if (args.force) s += " --force";
            return { script: "restore.py", argString: s, workdir: args.cwd };
          }
        )
      )
    );

    // ---- 3) prompt guidance ----------------------------------------------
    if (systemPrompt !== undefined && config.promptSection !== false) {
      disposers.push(
        systemPrompt.section({
          name: "agent-guard-delete-discipline",
          order: config.sectionOrder || 105,
          text: [
            "## Deletion discipline (agent-guard)",
            "",
            "This workspace runs agent-guard: an automatic recovery system with human escalation - not an approval system. Irreversible destruction is not a default capability.",
            "",
            "- To delete files/directories/globs, prefer the agent_guard_safe_delete tool: targets are quarantined under .agent-trash/ with a manifest and stay restorable. Pass cwd when operating in a repo below the workspace root.",
            "- Raw rm / git clean / git reset --hard are intercepted before execution. Most are compensated automatically (relocate or snapshot) and simply proceed; note the txid.",
            "- Shape rules: a command line that cds before deleting (COMPOUND_CWD_DELETE), or creates files and then deletes them (COMPOUND_CREATE_DELETE), triggers a single-execution user authorization. Splitting the deletion into its own standalone command avoids the prompt entirely.",
            "- True effect-uncertainty ($VAR targets, bash -c, find -delete, xargs-fed lists) stays hard-BLOCKED: restating with explicit concrete paths is the only remedy.",
            "- BLOCK_OUT_OF_WORKSPACE, BLOCK_PROTECTED_PATH (.git, workspace root) and BLOCK_FORCE_PUSH are policy violations and never askable: do not retry variants; ask the human if truly needed.",
            "- In RESTRICTED mode only explicit single-file deletes inside the workspace are permitted; only a human can restore NORMAL.",
            "- Never circumvent the guard (no /bin/rm, python os.unlink, node fs.rmSync, relocated scripts). Every decision is audit-logged; circumvention is a violation.",
            "- Undo with agent_guard_restore(txid). Inspect state with agent_guard_status().",
          ].join("\n"),
        })
      );
    }

    return () => {
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
