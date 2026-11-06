# Aftermath Frontend

The Aftermath frontend is a Next.js App Router client for incident response and historical memory investigation. It uses the FastAPI service for incident data, SSE analysis, resolution recording, and statistics. The constellation renders backend incidents and recalled memories; it does not provide production incident fixtures.

## Prerequisites

- Node.js 20.9 or newer
- npm (the repository includes `package-lock.json`)
- The Aftermath FastAPI backend for live incident workflows

## Install

From this directory:

```bash
npm ci
```

## Environment configuration

Create `frontend/.env.local` for local development:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

`NEXT_PUBLIC_API_URL` is the base URL used by browser-side API calls. It is embedded into the frontend at build time, so set it in the environment used for `npm run build`. Do not put API secrets in a `NEXT_PUBLIC_*` variable.

The FastAPI backend must allow the frontend origin through its CORS setting. Configure `FRONTEND_ORIGIN` in the backend environment, for example:

```dotenv
FRONTEND_ORIGIN=http://localhost:3000
```

For multiple frontend origins, provide a comma-separated list, such as `http://localhost:3000,https://aftermath.example.com`. Set the value to the exact scheme and host used by the browser, without a trailing slash.

## Local development

Start the backend first, then run the frontend from `frontend/`:

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The browser calls the configured API directly; an unavailable or disallowed backend is shown as an API error in the app.

The frontend uses these backend routes:

- `GET /incidents`, `GET /incidents/{id}`, and `GET /incidents/{id}/memories`
- `GET /incidents/{id}/analyze` as a fetch-based Server-Sent Events stream
- `POST /incidents` and `POST /incidents/{id}/resolve`
- `GET /stats`

## Quality checks and production build

Run from `frontend/`:

```bash
npx tsc --noEmit
npm run lint
npm run build
npm run start
```

`npm run start` serves the most recent production build. The development server is not a substitute for verifying the production build.

## Deployment

1. Set `NEXT_PUBLIC_API_URL` to the public HTTPS base URL of the deployed FastAPI service in the frontend build environment.
2. Build and deploy the Next.js app with `npm run build` and the platform's Next.js deployment integration (or run `npm run start` on a Node.js host).
3. Set backend `FRONTEND_ORIGIN` to the deployed frontend origin. Include any separate preview origins that need browser access, comma-separated.
4. Keep database and agent credentials in the backend environment only. Rebuild the frontend whenever its public API URL changes.

Resolution feedback reports memory persistence only when the resolution API confirms it through `memory_saved`.
