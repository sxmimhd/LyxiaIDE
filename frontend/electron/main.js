import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WorkspaceService } from "./workspace.js";
import { HistoryStore } from "./history.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const workspace = new WorkspaceService();

// The last opened folder is remembered between launches.
const stateFile = () => path.join(app.getPath("userData"), "workspace.json");

async function restoreWorkspace() {
  try {
    const { root } = JSON.parse(await fs.readFile(stateFile(), "utf8"));
    await workspace.open(root);
  } catch {
    // first launch, or the folder no longer exists: start without a workspace
  }
}

function registerIpc() {
  ipcMain.handle("app:ping", (_event, message) => ({ echo: message, at: Date.now() }));

  ipcMain.handle("workspace:get", () => workspace.info);
  ipcMain.handle("workspace:open", async (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    const options = { properties: ["openDirectory"], title: "Open Folder" };
    const result = win ? await dialog.showOpenDialog(win, options) : await dialog.showOpenDialog(options);
    if (result.canceled || !result.filePaths[0]) return null;
    const info = await workspace.open(result.filePaths[0]);
    await fs.writeFile(stateFile(), JSON.stringify({ root: info.root }), "utf8");
    ipcMain.handle("fs:createFile", (_event, p, content) => workspace.createFile(p, content));
      // Deleting always means "move to the Recycle Bin", so even a rollback can be undone from there.
    ipcMain.handle("fs:trash", async (_event, p) => {
      const target = await workspace.resolveEntry(p);
      if (workspace.isRoot(target)) throw new Error("Cannot delete the project folder");
      await shell.trashItem(target);
  });

  // AI change history (snapshots for rollback), kept per project in the app's data folder.
  const history = new HistoryStore(path.join(app.getPath("userData"), "history"));
  const currentRoot = () => {
    const info = workspace.info;
    if (!info) throw new Error("No folder is open");
    return info.root;
  };
  ipcMain.handle("history:save", (_event, input) => history.save(currentRoot(), input));
  ipcMain.handle("history:list", () => history.list(currentRoot()));
  ipcMain.handle("history:get", (_event, id) => history.get(currentRoot(), id));
  ipcMain.handle("history:markRolledBack", (_event, id, paths) => history.markRolledBack(currentRoot(), id, paths));
    return info;
  });

  ipcMain.handle("fs:readDir", (_event, p) => workspace.readDir(p));
  ipcMain.handle("fs:readFile", (_event, p) => workspace.readFile(p));
  ipcMain.handle("fs:writeFile", (_event, p, content) => workspace.writeFile(p, content));
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    backgroundColor: "#1e1e1e",
    title: "LyxiaIDE",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadURL("http://localhost:5173");
}

app.whenReady().then(async () => {
  await restoreWorkspace();
  registerIpc();
  createWindow();
});

app.on("window-all-closed", () => {
  app.quit();
});