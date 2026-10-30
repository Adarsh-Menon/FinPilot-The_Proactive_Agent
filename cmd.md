# FinPilot: commands to run it

Open two PowerShell terminals. Keep both open while you use the app.

## 1. Backend API (port 8010)

```powershell
cd C:\Users\adars\OneDrive\Desktop\FinPilot
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010 --reload
```

## 2. Frontend (port 5173)

```powershell
cd C:\Users\adars\OneDrive\Desktop\FinPilot\frontend
npm run dev
```

## Links

| What | Link |
|---|---|
| Customer app | http://localhost:5173 |
| Admin portal | http://localhost:5173/admin |
| API docs | http://127.0.0.1:8010/docs |
| Grafana (optional) | http://localhost:3000 |

## Sign-ins

- Customer demo: `outlook_demo` / `synthetic-demo-password-1`
- Admin demo: `local_ops` / `synthetic-admin-password-1`
- Or create another admin operator (asks for a 12+ character password):

```powershell
cd C:\Users\adars\OneDrive\Desktop\FinPilot
.venv\Scripts\python.exe -m backend.manage create-operator --username youradmin
```

## Optional

Grafana and Prometheus (needs Docker running):

```powershell
cd C:\Users\adars\OneDrive\Desktop\FinPilot
docker compose -f compose.observability.yaml up -d
```

Run the backend tests:

```powershell
cd C:\Users\adars\OneDrive\Desktop\FinPilot
.venv\Scripts\python.exe -m pytest tests/backend -q
```

Build the frontend:

```powershell
cd C:\Users\adars\OneDrive\Desktop\FinPilot\frontend
npm run build
```

## Notes

- "Port already in use": the app is already running; close the old terminal or just open the links.
- Restart the backend after editing `.env` (for example after adding `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`).
- Back up `C:\Users\adars\.finpilot\master.key`: without it the encrypted database cannot be read.
