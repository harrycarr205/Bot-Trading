# Dashboard UI/UX Redesign — Design Spec

Status: **Approved by user 2026-08-31. Ready for implementation planning.**

This spec covers the next planned dashboard job flagged at the end of the
2026-08-27 session (`dashboard_ui_ux_next` memory): improve the UI/UX of
the existing dashboard, not add new trading functionality. Scope grew
during brainstorming from a visual reskin to a full frontend rebuild — see
§1 for why, and §8 for the honest cost of that choice.

The existing dashboard (`docs/superpowers/specs/2026-08-25-dashboard-design.md`
and its two follow-up increments,
`2026-08-26-dashboard-process-control-design.md` and
`2026-08-26-dashboard-config-editing-design.md`) is server-rendered Jinja2
over FastAPI: 7 views, full page reloads, plain inline CSS, no JavaScript,
localhost-only, no authentication. This spec replaces that rendering layer
end to end while keeping every existing capability (read views, process
control, order cancel, config editing) and the existing trust model
(same-origin/CSRF guard, no auth, localhost-only).

---

## 1. Scope and decisions made

Decided during brainstorming (see conversation, not repeated in full here):

- **Full SPA rebuild** (React + TypeScript + Vite), not a template reskin
  and not a stay-server-rendered option. FastAPI becomes a JSON API plus
  static-file host for the built frontend.
- **Live auto-refresh** via polling (not WebSockets) — data changes on the
  order of minutes (a scheduled cycle, a heartbeat), so polling is simpler
  and sufficient; no new connection-lifecycle machinery for a problem that
  doesn't need it.
- **Open-ended information architecture** — free to add new views
  (Positions/Watchlist, Ticker Detail) where they make the app feel
  connected, not constrained to the existing 7 pages.
- **Extensibility**, all three of: consistent component/page patterns,
  a configurable/composable layout (Overview page only — see §4), and a
  lightweight plugin-style registry for adding panels/pages.
- **Auth stays out of scope.** The task framing mentioned "all their
  employees," but ARCHITECTURE.md §7 documents single-operator,
  localhost-only, no-auth as a deliberate, signed-off decision. Confirmed
  during brainstorming that this redesign does not change that — the
  Bloomberg-terminal framing is aesthetic, not a real multi-user
  requirement. If that ever changes, auth is a separate future spec.
- **Mouse/click navigation only** — no command palette, no keyboard-driven
  navigation layer. Rich linking and a persistent sidebar cover the "get
  anywhere from everywhere" requirement without a keyboard-shortcut
  system to design, document, and maintain.
- **Charts included** — equity curve and P&L history, using data that
  already exists in `portfolio_snapshots` and `realized_pnl`.

---

## 2. Visual identity

Bloomberg terminals are genuinely black-background, amber-on-black, dense
monospace grids — a real, recognizable identity, not a generic "dark mode
SaaS" default. Since the user explicitly asked for that feel, this leans
into it directly.

**Palette** (CSS custom properties, defined once in a `tokens.css`):

| Token | Hex | Use |
|---|---|---|
| `--void` | `#0A0A0B` | Base background |
| `--panel` | `#131316` | Cards/panels, one step up from void |
| `--hairline` | `#2A2A2E` | Borders/dividers — thin only, no shadows |
| `--amber` | `#FF9F1C` | Signature accent — headers, active nav, key labels, ticker strip |
| `--gain` | `#3ECF6E` | Positive P&L, buys, "fresh"/healthy status |
| `--loss` | `#FF5C5C` | Negative P&L, sells, alerts, stale/breaker states |
| `--ink` | `#E8E6E1` | Primary text (warm off-white, not pure white) |
| `--muted` | `#8A8A8F` | Secondary/label text |

**Typography** — three roles, each doing real work:
- **IBM Plex Sans Condensed** — headers, nav, section labels. Uppercase,
  letter-spaced. Condensed = more information per pixel.
- **IBM Plex Mono**, tabular figures — every number: prices, P&L,
  timestamps, tickers, table data. This is the functional core of the
  terminal feel (columns of numbers actually align), not decoration.
- **IBM Plex Sans** — prose: debate transcripts, reasoning text, longer
  paragraphs. The one place regular reading typography shows up.

Loaded via Google Fonts (`fonts.googleapis.com` / `fonts.gstatic.com`),
self-hosted fallback not required for a localhost dev tool.

**Layout** — persistent dark left rail for navigation, sharp corners
(≤2px radius, no soft rounding, no drop shadows — hairline borders only),
dense card/table grid for content.

**Signature element** — a live ticker strip pinned across the top of
every page: equity + day delta, heartbeat freshness dot, active-breaker
count, scrolling recent activity. This is deliberately the single element
that is always visible on every page (answers "see anything from
everywhere" directly), is the primary surface for the polling/live-update
requirement, and is a functional homage to an actual Bloomberg ticker
tape rather than a decorative flourish.

**Motion** — restrained: when a polled value changes, it flashes
green/red briefly (~400ms), matching how a real ticker updates. No other
animation. Reduced-motion media query disables the flash.

---

## 3. Information architecture

Cross-linking is the organizing principle — anywhere a ticker, decision,
order, or run appears, it links to that thing's detail view.

| Page | Route | Status |
|---|---|---|
| Overview | `/` | Redesigned — composable widget grid (§4) |
| Positions & Watchlist | `/positions` | **New** — held positions + candidate universe, live P&L, links to Ticker Detail |
| Ticker Detail | `/ticker/:symbol` | **New** — drill-down hub: latest decision + reasoning, decision history, orders/fills, all for one symbol |
| Decisions / Journal | `/decisions` | Existing, restyled — ticker links to Ticker Detail, rows link to Decision Detail |
| Decision Detail | `/decisions/:id` | Existing, restyled — debate transcript, now links to ticker and to the resulting order if a trade happened |
| Orders & Fills | `/orders` | Existing, restyled — ticker and originating-decision columns become links |
| P&L | `/pnl` | Existing + real equity-curve/realized-P&L chart; closed trades link to their decision |
| Control | `/control` | Existing scheduler/watchdog controls; log tail becomes live-polling instead of a static dump |
| Config | `/config` | Existing candidate universe / risk config / env editing, functionally unchanged, restyled |

No separate "activity feed" page — that content lives inside the Overview
grid as one panel, avoiding a page that duplicates Overview's purpose.

**Global nav:** left rail lists all 9 entries, always visible. The ticker
strip (§2) is also always visible, so core state (equity, breakers,
heartbeat) is visible even three levels deep in a decision transcript.

---

## 4. Extensibility architecture

Sized to what a solo operator adding things occasionally actually needs —
not a general-purpose plugin platform.

**Overview page (composable grid).** Panels live in
`frontend/src/panels/`, each a self-contained module exporting:

```ts
{ id: string; title: string; defaultSize: {w,h}; refreshIntervalMs: number; Component: React.FC }
```

Vite's `import.meta.glob` auto-discovers every module under
`panels/`. A generic `<DashboardGrid>` component (`react-grid-layout`)
renders whatever panels exist, supports drag/resize, and persists the
layout in `localStorage` (no new DB table or server-side persistence —
this is a single machine, single operator, nothing to sync across
devices or clients). **Adding a new Overview widget later is: drop one
file in `panels/`. Nothing else changes.**

**Everything else (new pages).** Shared primitives in
`frontend/src/components/`: `Card`, `DataTable`, `StatTile`, `Chart`,
`StatusDot`. A new page is one file in `frontend/src/pages/` plus one
entry in a `routes.ts` array (`{ path, navLabel, icon, Component }`).
This is the "consistent pattern, minimal touch" extensibility — not a
runtime-loaded plugin system for full pages, since that's more machinery
than an occasional new page needs. `routes.ts` is also what drives the
left-rail nav, so a route and its nav entry can never drift apart.

This combination satisfies all three extensibility answers from
brainstorming (consistent patterns, composable layout, panel registry)
using two mechanisms rather than inventing a third, heavier one.

---

## 5. Backend: JSON API

`src/tradingsystem/dashboard/app.py` stops rendering Jinja2 templates and
becomes a JSON API. Existing SQLAlchemy queries are reused; the change is
in the response shape (Pydantic models) and route prefix (`/api/*`), not
in what data is fetched. Every existing capability keeps its route
one-for-one, no functional regressions:

- `GET /api/overview` — portfolio snapshot, heartbeat + staleness,
  active circuit breakers (unchanged data from today's `/`).
- `GET /api/positions` — **new** — currently-held positions (from latest
  snapshot / open orders) plus candidate-universe tickers, for the new
  Positions page.
- `GET /api/ticker/{symbol}` — **new** — decisions, orders/fills, and
  latest reasoning filtered to one ticker, for Ticker Detail.
- `GET /api/decisions`, `GET /api/decisions/{id}` — same data as today's
  `/decisions` and `/decisions/{id}`.
- `GET /api/orders` — same data as today's `/orders`.
- `GET /api/pnl` — same data as today's `/pnl`.
- `GET /api/pnl/series` — **new** — time series shaped for the equity
  curve / realized-P&L chart (portfolio_snapshots ordered by date,
  realized_pnl ordered by close date).
- `GET/POST /api/control/*` — same operations as today's `/control/*`
  form posts (scheduler/watchdog start/stop/force-stop, off-cycle run,
  log tail), JSON in/out instead of form-encoded/redirect.
- `GET/POST /api/config/*` — same operations as today's `/config/*`
  (candidate universe, risk config + justification note, `.env` field
  edits), JSON in/out.

`_require_same_origin` (the existing CSRF guard) stays on every mutating
endpoint, unchanged in behavior. `config_editing.py`'s validation and
audit-log logic (`run/config_changes.log`) is unchanged, just invoked
from a JSON route instead of a form handler.

FastAPI also serves the built frontend: `StaticFiles` mount for
`frontend/dist/assets`, plus a catch-all route returning `index.html` for
any non-`/api` path (SPA client-side routing). `python -m
tradingsystem.dashboard` remains the single entry point in normal use —
it serves the pre-built `dist/`. During development, Vite's dev server
runs separately with a proxy to FastAPI for `/api/*`, same pattern as any
FastAPI+Vite project.

---

## 6. Live data and error handling

**Polling.** A shared `usePolling(endpoint, intervalMs)` hook:
- Ticker strip, heartbeat, active breakers: ~10s.
- Positions/equity: ~30s (less noisy, doesn't need to feel instant).
- Static content (a past decision's debate transcript, a closed order) —
  fetched once, no polling. It doesn't change after the fact; polling it
  would just be wasted requests.

Updated numbers flash green/red briefly on change (§2).

**Error handling.** A broken or slow panel shows "data unavailable," not
a blank page or a crashed app — each Overview panel fetches
independently with its own loading/error state, and each route is
wrapped in a React error boundary so one bad panel can't take down the
page. This redesign is UI-layer only; it does not touch trading logic,
risk validation, or the orchestration loop, so none of those fail-closed
behaviors are affected.

---

## 7. Testing

- **Backend:** the existing dashboard test suite (`tests/test_dashboard*.py`)
  is adapted from HTML-content assertions to JSON-shape assertions
  against the new `/api/*` routes, using the same `TestClient` +
  `get_db` dependency-override pattern already established. One test per
  route for the happy path, plus existing edge cases carried forward
  (empty DB, nonexistent id → 404, ticker filter narrows results,
  same-origin guard rejects cross-origin mutations).
- **Frontend:** Vitest + React Testing Library for shared components
  (`Card`, `DataTable`, `StatTile`, panels) and the `usePolling` hook.
- **E2E smoke:** a handful of Playwright checks — nav reaches every page,
  Overview loads with panels rendered, a ticker link resolves to Ticker
  Detail with matching data, Control start/stop still functions against
  a real (test) scheduler process. Run against the actual built app using
  the `webapp-testing` skill during implementation, not asserted from
  reading the code.

---

## 8. Cost and what this does NOT cover

**Honest cost of the SPA choice:** this replaces the entire dashboard
delivery mechanism. All 7 Jinja2 templates are deleted. Node/npm becomes
a new project dependency alongside Python (a `frontend/` directory with
its own `package.json`, separate from the `pyproject.toml`-managed
Python side). The existing dashboard test suite needs real rework, not a
patch, since assertions move from HTML strings to JSON shapes. This is a
materially bigger lift than a visual reskin — a deliberate trade-off made
during brainstorming in exchange for live polling, a composable Overview
grid, and a proper component system for future pages.

**Out of scope for this spec:**
- Authentication, roles, or any multi-user concept (§1).
- Keyboard-driven navigation / command palette (§1).
- WebSocket push (§1) — polling only.
- Circuit-breaker clearing and the kill switch — stay CLI-only, per
  ARCHITECTURE.md §7, unchanged by this redesign.
- Mobile-specific layout beyond basic responsive behavior — this is a
  desktop tool used at a desk, like the system it's monitoring.
- Cross-device layout sync for the Overview grid (`localStorage` only,
  per §4) — a natural future increment if it's ever needed, not built
  now.
