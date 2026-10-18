import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WorkspaceService } from "./workspace.js";

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