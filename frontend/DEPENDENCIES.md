# Dashboard dependencies (CLAUDE.md rule 4)

package.json can't hold comments, so each dependency is explained here. Exact
versions are locked in package-lock.json. All of them are free and open source.
Nothing here calls a paid service or needs a key.

| Package | What it costs | What breaks without it | What would justify changing it |
|---|---|---|---|
| `next` 16.3.6 | About 100 MB in node_modules; one build step (~15 s) | No web server, no pages, and no forwarding of /api to the backend. The browser would block direct calls because the backend sends no cross-site (CORS) headers | Drop it only if the backend serves the dashboard itself |
| `react`, `react-dom` 19.2 | Comes with Next | No screens | Never, while Next is used |
| `typescript` 5.9 (dev) | Build-time only | The dashboard can't be checked against the generated contract types (`src/contract/schema.d.ts`), so a backend shape change wouldn't be caught | Never. Rule 3 depends on it |
| `@types/node`, `@types/react`, `@types/react-dom` (dev) | Type definitions only | TypeScript can't check React or Node code | Never, while TypeScript is used |
| `vitest` 3.2 (dev) | ~30 MB, test time only | No tests for the event-stream folding, the stream reader, error messages or formatting (`npm test`) | A browser-level test runner (Playwright) could be added once the screens need click-through tests in CI |

Removed from the scaffold on purpose:

- **ESLint** and its Next config. `npm run verify` already typechecks and tests the code. ESLint would add about 60 MB and a second rule set to keep happy. Add it back if more people start editing the dashboard.
- **Tailwind.** Plain CSS (`src/app/globals.css`) is enough for three screens.
- **Google web fonts** (`next/font/google`). They download fonts during the build, which fails without internet or behind the China firewall (risk R5). The dashboard uses the computer's own fonts instead.

Infrastructure (also rule 4):

- `next.config.ts` forwards `/api/*` to `NEXT_PUBLIC_API_BASE` and turns off compression so the live stream isn't held back.
- The live stream itself goes through a small relay, `src/app/api/meetings/[id]/events/route.ts`. It costs one extra hop, and nothing new to install. Without it, the forwarding rule holds a quiet stream back for up to 15 s, so the live view says "Connecting..." when it is actually connected. It could go if the backend sent CORS headers and the browser read the stream directly.

The comments at the top of both files explain why.
