import { app, BrowserWindow, ipcMain } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

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

// Handler for the test call coming from the UI.
ipcMain.handle("app:ping", (_event, message) => {
  return { echo: message, at: Date.now() };
});

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  app.quit();
});