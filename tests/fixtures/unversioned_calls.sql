CREATE TABLE calls (
    id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    estimated_provider_cost_usd TEXT
);
