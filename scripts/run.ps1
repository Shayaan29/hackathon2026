# Starts the backend, which also serves the built cockpit on http://localhost:8000
$root = Split-Path -Parent $PSScriptRoot
Push-Location "$root\backend"
try {
    & .\.venv\Scripts\python.exe -m app
} finally {
    Pop-Location
}
