// Complete the reviewed host's native legacy import without a model driver.
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

let shutdown, stage = "arguments";
try {
  const [root, ...patchFiles] = process.argv.slice(2);
  if (!root || !path.isAbsolute(root) || patchFiles.length < 2) throw new Error();
  const manifest = JSON.parse(await fs.readFile(path.join(root, "package.json"), "utf8"));
  if (manifest.name !== "@deepseek-ai/dsh" || manifest.version !== "0.2.0-rc.2") throw new Error();
  const settingsUrl = pathToFileURL(path.join(root, "node_modules/@deepseek-ai/dsh-settings/lib/index.js"));
  const { SettingsForms } = await import(settingsUrl.href);
  stage = "settings-contract";
  const original = SettingsForms.prototype.importLegacyDocument;
  const originalUpdate = SettingsForms.prototype.update;
  if (typeof original !== "function" || typeof originalUpdate !== "function") throw new Error();
  let importPromise, calls = 0, importFailures = 0;
  // The host catches errors per section. Observe rejected updates as well,
  // so quiet logging cannot turn a partial import into successful bootstrap.
  SettingsForms.prototype.update = async function (...args) {
    try { return await originalUpdate.apply(this, args); }
    catch (error) { importFailures += 1; throw error; }
  };
  // Observe completion of the host's own import. Do not duplicate the import
  // or infer completion from its early rename/settings.yaml disappearance.
  SettingsForms.prototype.importLegacyDocument = function (...args) {
    calls += 1;
    importPromise = original.apply(this, args);
    return importPromise;
  };
  const { runProfile } = await import(pathToFileURL(path.join(root, "lib/profile-boot.js")).href);
  const { loadLayeredEnv } = await import(pathToFileURL(path.join(root, "node_modules/@deepseek-ai/dsh-app-boot/lib/index.js")).href);
  stage = "boot";
  const runtime = await runProfile({ environment: loadLayeredEnv("dsh"), profile: "headless", patchFiles, args: [] });
  shutdown = runtime.shutdown;
  stage = "loader";
  await runtime.ctx.root.loader.await();
  stage = "import";
  if (calls !== 1 || !importPromise) throw new Error();
  await importPromise;
  if (importFailures !== 0) throw new Error();
  await shutdown.shutdown(0);
} catch {
  if (shutdown) await shutdown.shutdown(1);
  process.stderr.write(`DSH Lab bootstrap refused: ${stage}.\n`);
  process.exitCode = 1;
}
