const { contextBridge, ipcRenderer } = require("electron");

// The only things the web page is allowed to call.
contextBridge.exposeInMainWorld("lyxia", {
  ping: (message) => ipcRenderer.invoke("app:ping", message),
});