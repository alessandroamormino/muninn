# smart-search — Frontend

React SPA served via Nginx on port 3000. Proxies API requests to the orchestrator.

## Development

```bash
npm install
npm run dev   # starts Vite dev server on http://localhost:5173
```

The dev server proxies `/api/*` to `http://localhost:8000` (configured in `vite.config.ts`).

## Build

```bash
npm run build   # outputs to dist/
```

In production, the Docker multi-stage build runs `npm run build` during the image build and copies `dist/` into the Nginx container. `npm` is not available in the final image.

## Tech stack

- React 19 + TypeScript
- Vite
- shadcn/ui v4 + Tailwind CSS v4
- TanStack Query v5
- React Router v7
- D3 v7 (knowledge graph)
