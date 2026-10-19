import { useWorkbench } from "../state/workbench";
import { monaco } from "./monacoSetup";
import { decideRollback } from "./rollbackLogic";
/**
 * Owns the Monaco models (one per open file) and each file's view state (cursor, scroll, folds).
 * They live outside React, so switching tabs never loses undo history.
 */
const models = new Map<string, monaco.editor.ITextModel>();
const savedVersion = new Map<string, number>(); // model version that matches what is on disk
const viewStates = new Map<string, monaco.editor.ICodeEditorViewState | null>();
const opening = new Set<string>(); // guards against opening the same file twice at once

const store = () => useWorkbench.getState();

const errorText = (e: unknown) =>
  e instanceof Error ? e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, "") : String(e);

const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path;

export const getModel = (path: string) => models.get(path);
export const getViewState = (path: string) => viewStates.get(path) ?? null;
export function saveViewState(path: string, state: monaco.editor.ICodeEditorViewState | null) {
  if (models.has(path)) viewStates.set(path, state);
}

export async function openFile(path: string): Promise<void> {
  if (opening.has(path)) return;
  opening.add(path);
  try {
    if (!models.has(path)) {
      const content = await window.lyxia.workspace.readFile(path);
      // No language given: Monaco detects it from the file extension in the URI.
      const model = monaco.editor.createModel(content, undefined, monaco.Uri.file(path));
      savedVersion.set(path, model.getAlternativeVersionId());
      model.onDidChangeContent(() => {
        // Comparing versions (not a flag) means undoing back to the saved text clears the marker.
        store().setDirty(path, model.getAlternativeVersionId() !== savedVersion.get(path));
      });
      models.set(path, model);
    }
    store().addTab(path);
    store().setError(null);
  } catch (e) {
    store().setError(errorText(e));
  } finally {
    opening.delete(path);
  }
}

export async function saveFile(path: string | null): Promise<void> {
  const model = path ? models.get(path) : undefined;
  if (!path || !model) return;
  const version = model.getAlternativeVersionId(); // what we are about to write
  try {
    await window.lyxia.workspace.writeFile(path, model.getValue());
    savedVersion.set(path, version);
    // If the user kept typing while the save ran, the file is still dirty.
    store().setDirty(path, model.getAlternativeVersionId() !== version);
    store().setError(null);
  } catch (e) {
    store().setError(`Save failed: ${errorText(e)}`);
  }
}

/** Closes a tab. Asks before discarding unsaved changes, unless `force` is set. */
export function closeFile(path: string, force = false): void {
  if (!force && store().dirty[path] && !window.confirm(`Discard unsaved changes to ${baseName(path)}?`)) return;
  store().removeTab(path);
  models.get(path)?.dispose();
  models.delete(path);
  savedVersion.delete(path);
  viewStates.delete(path);
}

/** True if nothing is unsaved, or the user agreed to discard it. */
export function confirmDiscardAll(): boolean {
  const anyDirty = Object.values(store().dirty).some(Boolean);
  return !anyDirty || window.confirm("You have unsaved changes. Discard them and switch folders?");
}

/** Closes every file (used when another folder is opened). */
export function resetEditors(): void {
  for (const model of models.values()) model.dispose();
  models.clear();
  savedVersion.clear();
  viewStates.clear();
  store().reset();
}

// ---- what the AI chat can see ----
let activeEditor: monaco.editor.IStandaloneCodeEditor | null = null;

export function registerEditor(editor: monaco.editor.IStandaloneCodeEditor | null) {
  activeEditor = editor;
}

export interface ChatContext {
  activeFile: { path: string; language: string; cursorLine: number; excerpt: string; excerptStartLine: number } | null;
  selection: { text: string; startLine: number; endLine: number } | null;
}

const EXCERPT_RADIUS = 60; // lines above and below the cursor

/** The active file's cursor, nearby code, and selection, captured at the moment a message is sent. */
export function getChatContext(): ChatContext {
  const path = store().activeTab;
  const model = path ? models.get(path) : undefined;
  if (!path || !model || !activeEditor) return { activeFile: null, selection: null };

  const cursorLine = activeEditor.getPosition()?.lineNumber ?? 1;
  const total = model.getLineCount();
  const small = total <= 2 * EXCERPT_RADIUS; // small files are sent whole
  const start = small ? 1 : Math.max(1, cursorLine - EXCERPT_RADIUS);
  const end = small ? total : Math.min(total, cursorLine + EXCERPT_RADIUS);
  const excerpt = model.getValueInRange({
    startLineNumber: start,
    startColumn: 1,
    endLineNumber: end,
    endColumn: model.getLineMaxColumn(end),
  });

  const sel = activeEditor.getSelection();
  const selection =
    sel && !sel.isEmpty()
      ? { text: model.getValueInRange(sel), startLine: sel.startLineNumber, endLine: sel.endLineNumber }
      : null;

  return {
    activeFile: { path, language: model.getLanguageId(), cursorLine, excerpt, excerptStartLine: start },
    selection,
  };
}

// ---- AI editing ----
export interface EditContext {
  path: string;
  language: string;
  content: string; // current text, including unsaved changes
  cursorLine: number;
  selection: { startLine: number; endLine: number } | null;
}

export function getEditContext(): EditContext | null {
  const path = store().activeTab;
  const model = path ? models.get(path) : undefined;
  if (!path || !model || !activeEditor) return null;
  const sel = activeEditor.getSelection();
  return {
    path,
    language: model.getLanguageId(),
    content: model.getValue(),
    cursorLine: activeEditor.getPosition()?.lineNumber ?? 1,
    selection: sel && !sel.isEmpty() ? { startLine: sel.startLineNumber, endLine: sel.endLineNumber } : null,
  };
}

const normalizeEol = (text: string) => text.replace(/\r\n/g, "\n");

/**
 * Applies AI-produced content to an open file as ONE undo step (Ctrl+Z reverts it).
 * Nothing is written to disk until the user saves. Returns an error message, or null on success.
 */
export function applyAiEdit(path: string, newContent: string, expectedOriginal: string): string | null {
  const model = models.get(path);
  if (!model) return "The file is no longer open.";
  // Line endings are ignored in the comparison: the editor normalises them, the backend reads them raw.
  if (normalizeEol(model.getValue()) !== normalizeEol(expectedOriginal)) {
    return "The file changed while the AI was working. Ask again.";
  }
  model.pushStackElement();
  model.pushEditOperations([], [{ range: model.getFullModelRange(), text: newContent }], () => null);
  model.pushStackElement();
  return null;
}

// ---- multi-file AI edits ----
/** Open files with their CURRENT text (unsaved edits included), so the AI plans from what the user sees. */
export function getOpenFiles(): { path: string; content: string }[] {
  const files: { path: string; content: string }[] = [];
  for (const [path, model] of models) {
    if (model.getValueLength() <= 200_000 && files.length < 20) files.push({ path, content: model.getValue() });
  }
  return files;
}

const samePath = (a: string, b: string) => a.replace(/\\/g, "/").toLowerCase() === b.replace(/\\/g, "/").toLowerCase();

export interface ReviewedChange {
  abs_path: string;
  status: string; // modified | created
  original: string;
  new_content: string;
}

/**
 * Applies one accepted change from a multi-file edit.
 * - modified: opens the file and applies the edit as one undo step (saved only when the user presses Ctrl+S)
 * - created: writes the new file to disk (it did not exist) and opens it
 * Returns an error message, or null on success.
 */
export async function applyFileChange(change: ReviewedChange): Promise<string | null> {
  try {
    const open = [...models.keys()].find((k) => samePath(k, change.abs_path));
    if (change.status === "created") {
      await window.lyxia.workspace.createFile(change.abs_path, change.new_content);
      await openFile(change.abs_path);
      return models.has(change.abs_path) ? null : `Created ${baseName(change.abs_path)} but could not open it.`;
    }
    const path = open ?? change.abs_path;
    if (!open) await openFile(path);
    if (!models.has(path)) return `Could not open ${baseName(change.abs_path)}.`;
    return applyAiEdit(path, change.new_content, change.original);
  } catch (e) {
    return `${baseName(change.abs_path)}: ${errorText(e)}`;
  }
}

// ---- history and rollback ----
export interface HistoryFileInput {
  path: string; // absolute
  status: "modified" | "created";
  before: string | null; // null for a file the AI created
  after: string;
}

/** Records an applied AI change so it can be rolled back later (even after saving or restarting). */
export async function recordChangeSet(instruction: string, source: "project" | "file", files: HistoryFileInput[]): Promise<void> {
  if (files.length === 0) return;
  try {
    await window.lyxia.history.save({ instruction, source, files });
  } catch (e) {
    store().setError(`The change was applied but could not be recorded in history: ${errorText(e)}`);
  }
}

export interface RollbackResult {
  path: string;
  ok: boolean;
  conflict: boolean; // true: the user changed the file since, so nothing was touched
  message: string;
}

/**
 * Rolls one file back to how it was before the AI change. Without `force`, a file the user changed since
 * is left alone and reported as a conflict.
 */
export async function rollbackFile(file: HistoryFileInput, force: boolean): Promise<RollbackResult> {
  const done = (message: string): RollbackResult => ({ path: file.path, ok: true, conflict: false, message });
  try {
    const open = [...models.keys()].find((k) => samePath(k, file.path));
    let current: string | null;
    if (open) {
      current = models.get(open)?.getValue() ?? null; // the editor's text, unsaved edits included
    } else {
      try {
        current = await window.lyxia.workspace.readFile(file.path);
      } catch {
        current = null; // missing (or unreadable) on disk
      }
    }

    const decision = decideRollback(file, current, force);
    switch (decision.action) {
      case "conflict":
        return { path: file.path, ok: false, conflict: true, message: decision.message };
      case "skip":
        return done(decision.message);
      case "delete":
        if (open) closeFile(open, true);
        await window.lyxia.workspace.trash(file.path);
        return done("Moved to the Recycle Bin");
      case "recreate":
        await window.lyxia.workspace.createFile(file.path, file.before ?? "");
        return done("Recreated from the snapshot");
      case "restore": {
        const before = file.before ?? "";
        const model = open ? models.get(open) : undefined;
        if (open && model) {
          // Inside the editor, so Ctrl+Z can still bring the AI version back; disk is restored too.
          model.pushStackElement();
          model.pushEditOperations([], [{ range: model.getFullModelRange(), text: before }], () => null);
          model.pushStackElement();
          await window.lyxia.workspace.writeFile(open, before);
          savedVersion.set(open, model.getAlternativeVersionId());
          store().setDirty(open, false);
        } else {
          await window.lyxia.workspace.writeFile(file.path, before);
        }
        return done("Restored");
      }
    }
  } catch (e) {
    return { path: file.path, ok: false, conflict: false, message: `${baseName(file.path)}: ${errorText(e)}` };
  }
}