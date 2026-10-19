import crypto from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

const MAX_RECORDS = 50; // per project; the oldest are deleted first
const ID_PATTERN = /^\d{13}-[a-f0-9]{8}$/; // ids become file names: never accept anything else

/**
 * Stores AI change sets (what each file looked like before and after) so they can be rolled back,
 * even after the app restarts. A record is two files: <id>.meta.json (small, listed) and
 * <id>.data.json (the file contents). The meta file is written last, so it marks a complete record.
 */
export class HistoryStore {
  #base;

  constructor(baseDir) {
    this.#base = baseDir;
  }

  async #folder(root) {
    const normalized = path.resolve(root);
    const key = crypto.createHash("sha1").update(process.platform === "win32" ? normalized.toLowerCase() : normalized).digest("hex").slice(0, 12);
    const dir = path.join(this.#base, key);
    await fs.mkdir(dir, { recursive: true });
    return dir;
  }

  static async #writeAtomic(file, text) {
    const tmp = `${file}.${process.pid}.tmp`;
    await fs.writeFile(tmp, text, "utf8");
    await fs.rename(tmp, file); // a crash can never leave a half-written record
  }

  async save(root, input) {
    if (!input || typeof input.instruction !== "string" || !Array.isArray(input.files) || input.files.length === 0 || input.files.length > 50) {
      throw new Error("Invalid change set");
    }
    for (const f of input.files) {
      const okStatus = f.status === "modified" || f.status === "created";
      const okBefore = f.status === "created" ? f.before === null : typeof f.before === "string";
      if (typeof f.path !== "string" || !okStatus || !okBefore || typeof f.after !== "string") throw new Error("Invalid change set file");
    }
    const dir = await this.#folder(root);
    const id = `${Date.now()}-${crypto.randomBytes(4).toString("hex")}`;
    const meta = {
      id,
      createdAt: new Date().toISOString(),
      root: path.resolve(root),
      instruction: input.instruction.slice(0, 500),
      source: input.source === "file" ? "file" : "project",
      files: input.files.map((f) => ({ path: f.path, status: f.status, rolledBack: false })),
    };
    const data = { files: input.files.map((f) => ({ path: f.path, before: f.before, after: f.after })) };
    await HistoryStore.#writeAtomic(path.join(dir, `${id}.data.json`), JSON.stringify(data));
    await HistoryStore.#writeAtomic(path.join(dir, `${id}.meta.json`), JSON.stringify(meta));
    await this.#prune(dir);
    return id;
  }

  /** Newest first. Unreadable records are skipped, never fatal. */
  async list(root) {
    const dir = await this.#folder(root);
    const out = [];
    for (const name of await fs.readdir(dir)) {
      if (!name.endsWith(".meta.json")) continue;
      try {
        out.push(JSON.parse(await fs.readFile(path.join(dir, name), "utf8")));
      } catch {
        // corrupt record: ignore
      }
    }
    return out.sort((a, b) => (a.id < b.id ? 1 : -1));
  }

  async get(root, id) {
    if (!ID_PATTERN.test(id)) throw new Error("Invalid history id");
    const dir = await this.#folder(root);
    const meta = JSON.parse(await fs.readFile(path.join(dir, `${id}.meta.json`), "utf8"));
    const data = JSON.parse(await fs.readFile(path.join(dir, `${id}.data.json`), "utf8"));
    return { ...meta, data: data.files };
  }

  async markRolledBack(root, id, paths) {
    if (!ID_PATTERN.test(id)) throw new Error("Invalid history id");
    const file = path.join(await this.#folder(root), `${id}.meta.json`);
    const meta = JSON.parse(await fs.readFile(file, "utf8"));
    const done = new Set(paths);
    for (const f of meta.files) if (done.has(f.path)) f.rolledBack = true;
    await HistoryStore.#writeAtomic(file, JSON.stringify(meta));
  }

  async #prune(dir) {
    const ids = (await fs.readdir(dir)).filter((n) => n.endsWith(".meta.json")).map((n) => n.slice(0, -10)).sort().reverse();
    for (const id of ids.slice(MAX_RECORDS)) {
      await fs.rm(path.join(dir, `${id}.meta.json`), { force: true });
      await fs.rm(path.join(dir, `${id}.data.json`), { force: true });
    }
  }
}