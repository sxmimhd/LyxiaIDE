import { useWorkbench } from "../state/workbench";
import { monaco } from "./monacoSetup";

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

/** Closes a tab. Asks before discarding unsaved changes. */
export function closeFile(path: string): void {
  if (store().dirty[path] && !window.confirm(`Discard unsaved changes to ${baseName(path)}?`)) return;
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