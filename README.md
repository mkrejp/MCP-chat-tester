# MCP chat tester

Google-signed-in chat page for interrogating the [fastmcp-rag-timer](https://github.com/mkrejp/fastmcp-rag-timer) MCP server. The browser talks only to this app. This process holds the bearer token, calls Streamable HTTP MCP, and returns text plus page images.

Credential names match [fastmcp-rag-timer-frontend](https://github.com/mkrejp/fastmcp-rag-timer-frontend). Copy the existing Railway values; do not create a second Google client or a second MCP token.

| Variable | Role |
| --- | --- |
| `GOOGLE_CLIENT_ID` | Same OAuth client id |
| `GOOGLE_CLIENT_SECRET` | Same OAuth client secret |
| `GOOGLE_ALLOWED_EMAILS` | Same comma-separated allowlist |
| `MCP_API_TOKEN` | Same bearer token the RAG server accepts |
| `MCP_SERVER_URL` | Default `https://rag-timer-00001-production.up.railway.app/mcp/` |
| `PUBLIC_BASE_URL` | Origin used for the Google callback |
| `SESSION_SECRET` | Cookie signing key for this app |
| `DEEPL_API_KEY` | DeepL key for translating dynamic page content (reply, facts, MCP descriptions) |
| `DEEPL_API_URL` | Optional DeepL host override (free keys ending in `:fx` default to `api-free.deepl.com`) |

`/health`, `/auth/*`, and `/static/*` are public. `/` and `/api/*` require a Google session. The token is not included in HTML, JSON, or image responses.

The chat page has a language selector (English, Czech, German, French, Italian, Spanish). Fixed chrome uses locale files under `/static/locales/`. Dynamic strings are translated through `POST /api/translate` with DeepL. The choice is stored in `localStorage` as `mcpChatLang`. Without `DEEPL_API_KEY`, the chrome still switches language and dynamic text stays in the original language.

The Google client must allow `{PUBLIC_BASE_URL}/auth/callback`. For this machine that is `http://127.0.0.1:8000/auth/callback`.

Each question plans read-only tool calls (`search_domain_knowledge`, and `search_images` or `list_documents` when the wording asks for them). Write tools such as `ingest_text` are never called. The sidebar lists tools, resources, and prompts from the live server.

## Run

```text
uv sync --all-groups
cp .env.example .env
uv run python -m mcp_chat
```

Open `http://127.0.0.1:8000`. Railway health checks should use `GET /health`.

The production service is the existing Railway app `fastmcp-rag-timer-frontend` in project `perfect-eagerness`. It keeps `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ALLOWED_EMAILS`, `MCP_API_TOKEN`, `PUBLIC_BASE_URL`, and `SESSION_SECRET`. Add `DEEPL_API_KEY` (and optionally `DEEPL_API_URL`) for page translation. The public origin stays `https://fastmcp-rag-timer-frontend-production.up.railway.app`, so the Google callback does not change. The process starts with `python -m mcp_chat` and listens on `PORT`.

## Map

Each answer is a set of fact cards: title, excerpt, and the page or figure image. Cards that include `lat` and `lon` (or a `Souřadnice` line) are drawn on the map as “This answer”.

Context layers come from the public GeoServer WMS at `https://geoserver-production-4d0d.up.railway.app/geoserver/ows`:

- `trails:obce` — municipality polygons
- `trails:cast_obce` — municipal-part points
- `trails:locality_links` — stored locality GPS, on by default
- `trails:layer_objects` — catalog geometries

OpenStreetMap is the base map. GeoServer is not a substitute for the answer points: those coordinates live on the RAG hits.

```text
uv run pytest
uv run ruff check src tests
```

`mcp.config.example.json` shows the same remote server with `${MCP_API_TOKEN}` left for the server process to resolve. The running app reads the environment variables above.
