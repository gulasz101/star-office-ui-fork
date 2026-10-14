# Star Desktop Pet (Electron Shell)

This directory is the Electron desktop shell. It coexists with the existing Tauri version to make gradual migration easy.

## Capabilities wired up

- Reuses the original frontend: `http://127.0.0.1:19000/?desktop=1`
- Reuses the mini page: `desktop-pet/src/minimized.html`
- Automatically starts the Python backend on launch (if it is not already running)
- Main window / mini window switching
- Persistent tray (menu bar) menu
- Injects a `window.__TAURI__` compatibility layer via preload, so existing frontend logic needs as few changes as possible

## Launch

```bash
cd "/Users/wangzhaohan/Documents/GitHub/Star-Office-UI/electron-shell"
npm install
npm run dev
```

## Optional environment variables

- `STAR_PROJECT_ROOT`: project root directory (auto-detected by default)
- `STAR_BACKEND_PYTHON`: path to the backend Python executable
- `STAR_BACKEND_HOST`: backend host (default `127.0.0.1`)
- `STAR_BACKEND_PORT`: backend port (default `19000`)

## Notes

- The current stage is a "runnable migration skeleton": the goal is to replace the desktop container layer first.
- The existing Tauri directory is untouched; you can roll back or compare in parallel at any time.
