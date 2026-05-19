# Phase 11 — Web GUI

Adds a React single-page application served at port 3000 with four main sections: Settings, Search, Logs, and Knowledge Graph.

## What was built

### Tech stack

- **React 19** + **TypeScript** + **Vite**
- **shadcn/ui v4** + **Tailwind CSS v4** — component library and styling
- **TanStack Query v5** — data fetching and caching
- **React Router v7** — client-side routing
- **D3 v7** — knowledge graph visualization (HTML5 Canvas, NOT SVG)
- **Nginx** — serves the built SPA and proxies `/api/*` → `orchestrator:8000`

The frontend is built as a Docker multi-stage image (`node:20-alpine` build → `nginx:alpine` serve).

---

### Settings page (`/settings`)

Collection management wizard with two flows:

**CSV/JSON upload:**
1. Select or name a collection
2. Upload a CSV or JSON file
3. Configure `text_fields` and `metadata_fields` via the `/setup/suggest-config` LLM suggestion or manual entry
4. Trigger a full sync

**REST API source:**
1. Enter the API URL and authentication details
2. Configure fields and pagination
3. API keys are entered as env var names — the backend stores `${VAR_NAME}` in config, not the actual secret

---

### Search page (`/search`)

Full-text + semantic search with live results:

- Collection selector (entity dropdown)
- Search bar with debounced query
- Score threshold slider (`min_score`)
- Filter inputs for `metadata_fields`

**Result display:**
- Compact result cards showing key fields at a glance (configurable per collection)
- Status indicator: green dot (active) / red dot (inactive) — no text label
- Click a card → full-detail modal with blurred backdrop and all fields
- `PRODOTTO_PRINCIPALE` and other long text fields don't overflow the modal

---

### Logs page (`/logs`)

Sync history table for the selected collection:
- Timestamp, type (`full` / `incremental` / `scheduled`), status, records synced, duration
- Newest first, paginated

---

### Knowledge Graph page (`/graph`)

Interactive force-directed graph of the collection's embedding space:

**Visualization:**
- D3 Canvas force simulation (not SVG — GPU-composited for large graphs)
- UMAP projection computed server-side; D3 forces settle the layout
- Cluster colors: 5-color shadcn chart palette + slate for noise (cluster −1)
- Zoom and pan (mouse wheel + drag)
- Click a node → detail panel slides in from the right (absolute-positioned, CSS `translateX` animation)

**Controls (in page header):**
- Collection selector + "Load Graph" button (manual trigger — avoids loading on every collection switch)
- Node count badge
- "Reset view" button (restores default zoom/pan)

**Left legend:**
- Full-height cluster legend with LLM-generated cluster names
- Scrollable if clusters exceed viewport height
- Filter clusters on/off

**Architecture notes (D3 + React):**
- D3 simulation state is kept in `useRef` — never in React state (would cause infinite re-render on every tick)
- Canvas uses a `useState` callback ref (not `useRef`) so the `useEffect` re-triggers when the canvas mounts
- The detail panel is `absolute`-positioned inside a `relative` canvas container — never a flex sibling. This prevents the canvas from resizing during the panel open/close animation (which would clear the canvas and cause a white flash).
- `pointer-events-none` is applied to the hidden panel so canvas click events pass through

---

### Nginx proxy

`frontend/nginx.conf` proxies all `/api/*` requests to the orchestrator:

```nginx
location /api/ {
    proxy_pass http://orchestrator:8000/;  # trailing slash is mandatory (strips /api prefix)
}
```

## Key files

| File | Purpose |
|---|---|
| `frontend/src/pages/SettingsPage.tsx` | Collection setup wizard |
| `frontend/src/pages/SearchPage.tsx` | Search interface |
| `frontend/src/pages/LogsPage.tsx` | Sync history |
| `frontend/src/pages/GraphPage.tsx` | Knowledge graph container + header controls |
| `frontend/src/pages/graph/GraphCanvas.tsx` | Canvas + absolute panel layout |
| `frontend/src/pages/graph/useGraphRender.ts` | D3 force simulation, zoom, click hit-testing |
| `frontend/src/pages/graph/ClusterLegend.tsx` | Full-height scrollable legend |
| `frontend/src/pages/graph/NodeSidebar.tsx` | Node detail panel (shadcn Sheet) |
| `frontend/src/pages/search/ResultCard.tsx` | Compact card + full-detail modal |
| `frontend/src/api/graph.ts` | TanStack Query hook (manual trigger, `gcTime:0`) |
| `frontend/nginx.conf` | SPA serve + `/api` proxy |
| `frontend/Dockerfile` | Multi-stage build |
| `sync-service/api/graph.py` | `GET /graph/{collection}` — UMAP + HDBSCAN + LLM cluster names |
