# web/

The React front end lands here (Vite, Tailwind, shadcn, TanStack
Query). `web/dist` is its build output, git-ignored, and served by FastAPI from
`/` with an SPA fallback whenever the directory exists — see
`gaggiclanker/static.py`.

Until then, this directory holds only this file. The Dockerfile's front-end stage
already handles both cases: if `web/package.json` exists it runs `npm ci && npm
run build`, otherwise it ships an empty `dist/` and the app mounts nothing.

In development the Vite dev server serves the app on its own port and proxies
`/api` to the backend, so `web/dist` is absent and the fallback is inactive. Set
`CORS_ORIGINS=http://localhost:5173` for that setup.
