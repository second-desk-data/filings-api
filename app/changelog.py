"""Public changelog for the Second Desk API, served at GET /changelog
on the API domain. Newest first. Dates are America/Chicago ship dates.
Keep entries factual: what shipped, in the release that shipped it."""

ENTRIES = [
    {
        "version": "3.0.0",
        "date": "2026-11-27",
        "title": "Agent commerce layer",
        "changes": [
            "Self-serve free keys: POST /v1/keys returns a free-tier key "
            "immediately (shown once, stored hashed, rate limited per "
            "email and per IP, one active free key per email).",
            "Catalog endpoints: GET /v1/catalog lists every sellable "
            "family with record counts, pack counts and price points; "
            "GET /v1/catalog/{family} lists that family's packs.",
            "Agent discovery: GET /.well-known/agent.json describes the "
            "business, plans, and the fully server-to-server buying "
            "path (plain HTTPS + X-API-Key, no browser).",
            "Work board: POST/GET /v1/jobs and /v1/resumes (candidates "
            "list themselves; consent required), DELETE for your own "
            "entries. Employer plan ($20/mo) covers job posting and "
            "resume search at scale; reading the board stays free.",
            "This changelog at GET /changelog; documented error bodies "
            "with next steps in the OpenAPI spec.",
            "Pricing policy served in the agent card: first-year "
            "subscribers lock their rate for life while the plan "
            "stays active (founding offer honored; better terms win; "
            "removal under the 30-day rule loses the lock).",
            "Paid plan lifecycle gate: keys carrying status suspended "
            "answer 402 plan_past_due and revoked keys answer 401 "
            "api_key_revoked (states and hooks documented in "
            "AGENT-COMMERCE.md; free keys are never suspended or "
            "revoked under that rule).",
        ],
    },
    {
        "version": "2.1.0",
        "date": "2026-11-26",
        "title": "Marketplace delivery across all data families",
        "changes": [
            "The signed delivery webhook now serves all 79 marketplace "
            "listings across five verticals (7,321,101 records), not "
            "just business filings.",
        ],
    },
    {
        "version": "2.0.0",
        "date": "2026-11-26",
        "title": "OpenAPI, MCP server, CORS",
        "changes": [
            "OpenAPI 3 spec served at /openapi.yaml.",
            "MCP server at /mcp (search, get business, regions, "
            "categories) with the same key auth and quotas.",
            "CORS enabled for browser-based clients; key scoping "
            "enforced on every route.",
        ],
    },
    {
        "version": "1.0.0",
        "date": "2026-11-26",
        "title": "US Business Filings API launch",
        "changes": [
            "5,724,302 business filing records across 8 states (PA, "
            "CT, NY, CO, IA, OR, LA, FL).",
            "GET /v1/businesses with state/city/county/category/date "
            "filters, GET /v1/businesses/{charter_number}, "
            "/v1/regions, /v1/parishes, /v1/categories.",
            "Plans: Free 50 requests/day; City Heavy $29/mo; Full "
            "State Heavy $59/mo; overage $0.005/request.",
        ],
    },
]


def document():
    return {
        "service": "Second Desk US Business Filings API",
        "changelog": ENTRIES,
        "note": "Newest first. The API version field in GET / stays "
                "stable for client compatibility; release history "
                "lives here.",
    }
