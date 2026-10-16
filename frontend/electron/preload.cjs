const { contextBridge, ipcRenderer } = require("electron");

// The only things the web page is allowed to call.
contextBridge.exposeInMainWorld("lyxia", {
  ping: (message) => ipcRenderer.invoke("app:ping", message),
  workspace: {
    get: () => ipcRenderer.invoke("workspace:get"),
    open: () => ipcRenderer.invoke("workspace:open"),
    readDir: (path) => ipcRenderer.invoke("fs:readDir", path),
    readFile: (path) => ipcRenderer.invoke("fs:readFile", path),
    writeFile: (path, content) => ipcRenderer.invoke("fs:writeFile", path, content),
  },
});