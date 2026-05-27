# Auto Ballooning & Inspection Report Web Application

Web application for engineering drawing auto-ballooning (YOLO detection) and inspection report generation.

## Structure

- `frontend/` — Static UI (dashboard, login, admin, inspection report)
- `backend/` — FastAPI server (`serve_balloon.py`), Auto Ballooning module, auth, and assets

## Run locally

```powershell
cd backend
pip install -r requirements.txt
# Add DATABASE_URL + SUPER_ADMIN_* to backend/.env (see .env.example)
python serve_balloon.py
```

Open http://127.0.0.1:9080/login — you must log in before using `/app`.

For local dev **without** login, set `SMORX_DISABLE_BALLOON_AUTH=1` in `.env` (not recommended once PostgreSQL is configured).

## Configuration

Copy `backend/default_config.json` from your environment template (not committed). Place `AutoBallooningModel.pt` under `backend/Resources/models/` if not already present.

## Deploy on Render

Repo: [inspection-report-web-application](https://github.com/yadavKA01/inspection-report-web-application)

1. Sign in at [Render](https://render.com) → **New** → **Blueprint** → connect the GitHub repo above.
2. Render reads `render.yaml` (Python web service, `rootDir: backend`, `backend/start.sh`).
3. After the service is created, set **Environment** variables (minimum):
   - `DATABASE_URL` — PostgreSQL (e.g. Neon)
   - `JWT_SECRET_KEY` — long random string
   - `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` — first admin login
   - `ANTHROPIC_API_KEY` — Claude vision / OCR (optional but recommended)
4. **Manual Deploy** or wait for auto-deploy on push to `main`.
5. Open `https://<your-service>.onrender.com/login` then `/app`.

Build uses **Git LFS** for `AutoBallooningModel.pt`. Enable LFS on Render if the model is stored with LFS.

## License

Proprietary — SmorX.ai
