CREATE TABLE provider_credentials (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    encrypted_api_key TEXT NOT NULL,
    key_hint TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE pricing_plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    model TEXT NOT NULL,
    version TEXT NOT NULL,
    provider_input_usd TEXT NOT NULL,
    provider_cached_usd TEXT NOT NULL,
    provider_cache_write_usd TEXT NOT NULL,
    provider_output_usd TEXT NOT NULL,
    billed_input_usd TEXT NOT NULL,
    billed_cached_usd TEXT NOT NULL,
    billed_cache_write_usd TEXT NOT NULL,
    billed_output_usd TEXT NOT NULL,
    allow_below_cost INTEGER NOT NULL DEFAULT 0,
    below_cost_reason TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE installations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_hint TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    provider_credential_id TEXT NOT NULL DEFAULT '',
    pricing_plan_id TEXT NOT NULL DEFAULT '',
    reasoning_effort TEXT NOT NULL DEFAULT 'low',
    billing_mode TEXT NOT NULL DEFAULT 'prepaid',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE calls (
    id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    board_id TEXT NOT NULL DEFAULT '',
    chat_id TEXT NOT NULL DEFAULT '',
    app_ai_call_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    resolved_model TEXT NOT NULL DEFAULT '',
    reasoning_effort TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    provider_request_id TEXT NOT NULL DEFAULT '',
    estimated_provider_cost_usd TEXT,
    provider_cost_usd TEXT,
    charged_usd TEXT,
    margin_usd TEXT,
    pricing_plan_id TEXT NOT NULL DEFAULT '',
    pricing_version TEXT NOT NULL DEFAULT '',
    provider_rates_json TEXT NOT NULL DEFAULT '{}',
    billed_rates_json TEXT NOT NULL DEFAULT '{}',
    below_cost INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(installation_id, idempotency_key)
);
CREATE TABLE user_limits (
    installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    monthly_limit_usd TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(installation_id, user_id)
);
CREATE TABLE credit_ledger (
    id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
    amount_usd TEXT NOT NULL,
    kind TEXT NOT NULL,
    call_id TEXT REFERENCES calls(id),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE gateway_defaults (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    provider_credential_id TEXT NOT NULL DEFAULT '',
    pricing_plan_id TEXT NOT NULL DEFAULT '',
    reasoning_effort TEXT NOT NULL DEFAULT 'low',
    billing_mode TEXT NOT NULL DEFAULT 'prepaid',
    updated_at TEXT NOT NULL
);
CREATE TABLE gateway_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    max_output_tokens INTEGER NOT NULL DEFAULT 64000,
    max_request_bytes INTEGER NOT NULL DEFAULT 5242880,
    updated_at TEXT NOT NULL
);
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX credit_ledger_call_id
    ON credit_ledger(call_id) WHERE call_id IS NOT NULL;
CREATE INDEX calls_installation_created
    ON calls(installation_id, created_at DESC);
CREATE INDEX calls_installation_user_created
    ON calls(installation_id, user_id, created_at DESC);
CREATE INDEX credit_ledger_installation_created
    ON credit_ledger(installation_id, created_at DESC);
CREATE INDEX audit_log_created ON audit_log(created_at DESC);

INSERT INTO pricing_plans (
    id, name, model, version,
    provider_input_usd, provider_cached_usd, provider_cache_write_usd, provider_output_usd,
    billed_input_usd, billed_cached_usd, billed_cache_write_usd, billed_output_usd,
    created_at, updated_at
) VALUES (
    'plan-current', 'Current plan', 'gpt-5.6-terra', 'current-v1',
    '2.500000', '0.250000', '3.125000', '15.000000',
    '2.500000', '0.250000', '3.125000', '15.000000',
    '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
);
INSERT INTO installations (
    id, name, token_hash, token_hint, pricing_plan_id, created_at, updated_at
) VALUES (
    'inst-current', 'Current installation',
    '1e08095aaca2f33e7e2523620557c56e3b1c5b59929bc8fef3c265121fb6f590',
    'urrent', 'plan-current', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
);
INSERT INTO calls (
    id, installation_id, idempotency_key, status, requested_model, resolved_model,
    provider_cost_usd, charged_usd, margin_usd, pricing_plan_id, pricing_version,
    provider_rates_json, billed_rates_json, created_at, completed_at
) VALUES (
    'call-current', 'inst-current', 'current-request', 'ok', 'ignored-client-model',
    'gpt-5.6-terra', '0.010000', '0.010000', '0.000000', 'plan-current', 'current-v1',
    '{}', '{}', '2026-07-01T00:00:00Z', '2026-07-01T00:00:01Z'
);
INSERT INTO gateway_defaults (
    singleton, pricing_plan_id, reasoning_effort, billing_mode, updated_at
) VALUES (1, 'plan-current', 'low', 'prepaid', '2026-07-01T00:00:00Z');
INSERT INTO gateway_settings (
    singleton, max_output_tokens, max_request_bytes, updated_at
) VALUES (1, 64000, 5242880, '2026-07-01T00:00:00Z');
