/** The part of a recorded change that rollback needs. */
export interface RollbackTarget {
  status: "modified" | "created";
  before: string | null; // null for a file the AI created
  after: string;
}

export type RollbackDecision =
  | { action: "restore" } // put `before` back
  | { action: "recreate" } // the file was deleted since: write `before` again
  | { action: "delete" } // move a file the AI created to the Recycle Bin
  | { action: "skip"; message: string } // nothing to do
  | { action: "conflict"; message: string }; // the user changed it since: do not touch without `force`

const normalizeEol = (text: string) => text.replace(/\r\n/g, "\n");

/**
 * Decides what rolling back one file means, given what the file contains right now
 * (the editor's text if it is open, else the disk; null if the file does not exist).
 * Rule: never overwrite work the AI did not produce, unless the user forces it.
 */
export function decideRollback(file: RollbackTarget, current: string | null, force: boolean): RollbackDecision {
  const matchesAi = current !== null && normalizeEol(current) === normalizeEol(file.after);

  if (file.status === "created") {
    if (current === null) return { action: "skip", message: "Already deleted" };
    if (!matchesAi && !force) return { action: "conflict", message: "You changed this new file after the AI created it" };
    return { action: "delete" };
  }

  if (current === null) {
    return force ? { action: "recreate" } : { action: "conflict", message: "The file no longer exists" };
  }
  // Back at the original already (e.g. the user pressed Ctrl+Z in the editor): restoring is harmless and
  // also fixes the disk, which may still hold the AI's version.
  const matchesOriginal = file.before !== null && normalizeEol(current) === normalizeEol(file.before);
  if (!matchesAi && !matchesOriginal && !force) {
    return { action: "conflict", message: "You edited this file after the AI change" };
  }
  return { action: "restore" };
}