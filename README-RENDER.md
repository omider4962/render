# Render deployment

## Deploy

1. Push this folder to a GitHub repository.
2. In Render choose **New → Blueprint** and select the repository.
3. Render will read `render.yaml` and create the web service.
4. In the service Environment settings, set `DOMAIN` to your public Render URL, for example:
   `https://omid3r-vless-panel.onrender.com`
5. Redeploy after setting `DOMAIN`.

The application listens on Render's `PORT` and exposes `/health` for the Render health check.

## Stable UUIDs

Render Free has an ephemeral filesystem. SQLite data can be lost after a restart or spin-down. To keep the three generated VLESS UUIDs stable across restarts, set these environment variables once:

- `VLESS_UUID_1`
- `VLESS_UUID_2`
- `VLESS_UUID_3`

Use valid UUID values. If they are not set, the application generates UUIDs on first initialization, but they are not guaranteed to survive a Render Free restart.

## Keeping a Free service awake

Render Free services spin down after 15 minutes without inbound HTTP traffic or inbound WebSocket messages. The application cannot prevent this by pinging itself. If you need it awake, use an external monitor/cron that requests `GET /health` periodically, or use a paid Render instance for continuous uptime.

See Render's current free-tier documentation for the exact limits.
