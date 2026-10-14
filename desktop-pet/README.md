# Star Office Tauri Desktop Shell

This directory wraps `Star-Office-UI` into a desktop app (transparent window) and automatically starts the backend process on launch.

## Dev run

First prepare the Python environment at the repo root:

```bash
cd /Users/wangzhaohan/Documents/GitHub/Star-Office-UI
uv venv .venv
uv pip install -r backend/requirements.txt --python .venv/bin/python
```

Then start Tauri:

```bash
cd /Users/wangzhaohan/Documents/GitHub/Star-Office-UI/desktop-pet
npm install
npm run dev
```

## Backend auto-start logic

- Preferred: `../.venv/bin/python backend/app.py`
- First fallback: `python3 backend/app.py`
- Second fallback: `python backend/app.py`

The window navigates to this URL by default:

- `http://127.0.0.1:19000/?desktop=1`

## Optional environment variables

- `STAR_PROJECT_ROOT`: project root directory (auto-detected by default)
- `STAR_BACKEND_PYTHON`: custom Python executable path
- `STAR_BACKEND_URL`: custom URL the desktop window opens
