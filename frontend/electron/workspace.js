import { promises as fs } from "node:fs";
import path from "node:path";

const MAX_FILE_BYTES = 5 * 1024 * 1024;

function isInside(root, target) {
  const rel = path.relative(root, target);
  return rel === "" || (rel !== ".." && !rel.startsWith(".." + path.sep) && !path.isAbsolute(rel));
}

/**
 * Owns the open project. Every file operation goes through #resolveSafe(), so neither the UI
 * nor (later) the AI can touch anything outside the opened folder, not even through symlinks.
 */
export class WorkspaceService {
  #root = null;

  get info() {
    return this.#root ? { root: this.#root, name: path.basename(this.#root) || this.#root } : null;
  }

  async open(dir) {
    const real = await fs.realpath(dir);
    if (!(await fs.stat(real)).isDirectory()) throw new Error("Not a folder");
    this.#root = real;
    return this.info;
  }

  async #resolveSafe(p) {
    if (!this.#root) throw new Error("No folder is open");
    const abs = path.resolve(p);
    if (!isInside(this.#root, abs)) throw new Error(`Path is outside the opened folder: ${p}`);
    let real;
    try {
      real = await fs.realpath(abs);
    } catch (err) {
      if (err.code !== "ENOENT") throw err;
      real = path.join(await fs.realpath(path.dirname(abs)), path.basename(abs)); // new file: check its parent
    }
    if (!isInside(this.#root, real)) throw new Error(`Path escapes the opened folder: ${p}`);
    return real;
  }

  /** Folders first, then files, each sorted by name. */
  async readDir(p) {
    const dir = await this.#resolveSafe(p);
    const entries = await fs.readdir(dir, { withFileTypes: true });
    return entries
      .map((e) => ({ name: e.name, path: path.join(dir, e.name), isDir: e.isDirectory() }))
      .sort((a, b) => Number(b.isDir) - Number(a.isDir) || a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  }

  async readFile(p) {
    const file = await this.#resolveSafe(p);
    const stat = await fs.stat(file);
    if (stat.size > MAX_FILE_BYTES) throw new Error(`File is too large to open (${(stat.size / 1048576).toFixed(1)} MB)`);
    const buffer = await fs.readFile(file);
    if (buffer.subarray(0, 8000).includes(0)) throw new Error("Binary files cannot be opened in the editor");
    return buffer.toString("utf8");
  }

  async writeFile(p, content) {
    await fs.writeFile(await this.#resolveSafe(p), content, "utf8");
  }

    /** Creates a NEW file (and any missing folders). Refuses to overwrite and to leave the project. */
  async createFile(p, content) {
    if (!this.#root) throw new Error("No folder is open");
    const abs = path.resolve(p);
    if (!isInside(this.#root, abs)) throw new Error(`Path is outside the opened folder: ${p}`);
    // The nearest folder that already exists must really be inside the project (no symlink escapes).
    let anchor = abs;
    for (;;) {
      try {
        await fs.access(anchor);
        break;
      } catch {
        const parent = path.dirname(anchor);
        if (parent === anchor) throw new Error(`Invalid path: ${p}`);
        anchor = parent;
      }
    }
    if (!isInside(this.#root, await fs.realpath(anchor))) throw new Error(`Path escapes the opened folder: ${p}`);
    await fs.mkdir(path.dirname(abs), { recursive: true });
    try {
      await fs.writeFile(abs, content, { encoding: "utf8", flag: "wx" }); // "wx": fail if it already exists
    } catch (err) {
      if (err.code === "EEXIST") throw new Error(`File already exists: ${path.basename(abs)}`);
      throw err;
    }
  }
}