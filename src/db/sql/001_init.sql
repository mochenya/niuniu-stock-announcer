CREATE TABLE IF NOT EXISTS announcements (
    source text NOT NULL CHECK (source IN ('cninfo', 'sse', 'szse')),
    announcement_id text NOT NULL,
    sec_code text,
    sec_name text,
    org_id text,
    announcement_title text,
    announcement_time_ms bigint,
    adjunct_url text,
    page_column text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, announcement_id)
);

CREATE INDEX IF NOT EXISTS idx_announcements_announcement_time_ms
    ON announcements (announcement_time_ms DESC);

CREATE INDEX IF NOT EXISTS idx_announcements_sec_code
    ON announcements (sec_code);

CREATE TABLE IF NOT EXISTS announcement_hits (
    id bigserial PRIMARY KEY,
    source_key text NOT NULL,
    announcement_source text NOT NULL CHECK (announcement_source IN ('cninfo', 'sse', 'szse')),
    announcement_id text NOT NULL,
    market text NOT NULL CHECK (market IN ('sh', 'sz', 'bj', 'hk')),
    stock_code text NOT NULL,
    stock_key text NOT NULL,
    company_name text,
    search_mode text NOT NULL CHECK (search_mode IN ('stock', 'stock_keyword')),
    search_keyword text,
    filter_status text NOT NULL CHECK (filter_status IN ('selected', 'filtered')),
    filter_reason text,
    filter_keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
    filter_title text,
    config_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_hit_at timestamptz NOT NULL DEFAULT now(),
    last_hit_at timestamptz NOT NULL DEFAULT now(),
    hit_count integer NOT NULL DEFAULT 1 CHECK (hit_count > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_key, announcement_source, announcement_id),
    FOREIGN KEY (announcement_source, announcement_id)
        REFERENCES announcements (source, announcement_id)
        ON DELETE CASCADE,
    CHECK (
        (search_mode = 'stock' AND search_keyword IS NULL)
        OR (search_mode = 'stock_keyword' AND search_keyword IS NOT NULL)
    ),
    CHECK (jsonb_typeof(filter_keywords) = 'array'),
    CHECK (jsonb_typeof(config_snapshot) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_announcement_hits_announcement
    ON announcement_hits (announcement_source, announcement_id);

CREATE INDEX IF NOT EXISTS idx_announcement_hits_stock_key
    ON announcement_hits (stock_key);

CREATE INDEX IF NOT EXISTS idx_announcement_hits_filter_status
    ON announcement_hits (filter_status);

CREATE TABLE IF NOT EXISTS announcement_summaries (
    announcement_source text NOT NULL CHECK (announcement_source IN ('cninfo', 'sse', 'szse')),
    announcement_id text NOT NULL,
    primary_hit_id bigint REFERENCES announcement_hits (id) ON DELETE SET NULL,
    stock_key text NOT NULL,
    market text NOT NULL CHECK (market IN ('sh', 'sz', 'bj', 'hk')),
    stock_code text NOT NULL,
    company_name text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    pdf_local_path text,
    summary_model text,
    summary_started_at timestamptz,
    summarized_at timestamptz,
    failure_reason text,
    failure_log text,
    summary_failure_count integer NOT NULL DEFAULT 0 CHECK (summary_failure_count >= 0),
    summary_json jsonb,
    summary_text text,
    summary_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    llm_response_json jsonb,
    input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (announcement_source, announcement_id),
    FOREIGN KEY (announcement_source, announcement_id)
        REFERENCES announcements (source, announcement_id)
        ON DELETE CASCADE,
    CHECK (summary_json IS NULL OR jsonb_typeof(summary_json) = 'object'),
    CHECK (llm_response_json IS NULL OR jsonb_typeof(llm_response_json) = 'object'),
    CHECK (jsonb_typeof(summary_tags) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_announcement_summaries_status
    ON announcement_summaries (status);

CREATE TABLE IF NOT EXISTS telegram_deliveries (
    id bigserial PRIMARY KEY,
    announcement_source text NOT NULL CHECK (announcement_source IN ('cninfo', 'sse', 'szse')),
    announcement_id text NOT NULL,
    primary_hit_id bigint REFERENCES announcement_hits (id) ON DELETE SET NULL,
    stock_key text NOT NULL,
    market text NOT NULL CHECK (market IN ('sh', 'sz', 'bj', 'hk')),
    stock_code text NOT NULL,
    company_name text NOT NULL,
    target_key text NOT NULL CHECK (target_key IN ('a_share', 'hk')),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'unknown')),
    failure_reason text,
    failure_log text,
    target_chat_id bigint,
    target_message_thread_id bigint,
    text_message_id bigint,
    pdf_message_id bigint,
    started_at timestamptz,
    sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (announcement_source, announcement_id, target_key),
    FOREIGN KEY (announcement_source, announcement_id)
        REFERENCES announcements (source, announcement_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_telegram_deliveries_status
    ON telegram_deliveries (status);

CREATE INDEX IF NOT EXISTS idx_telegram_deliveries_announcement
    ON telegram_deliveries (announcement_source, announcement_id);
