from __future__ import annotations

# 摘要和投递阶段共享同一套公告字段，保持 SELECT 列名与 row_mappers 对齐。
SUMMARY_CANDIDATE_SQL = """
SELECT
    a.source,
    a.announcement_id,
    a.sec_code,
    a.sec_name,
    a.org_id,
    a.announcement_title,
    a.announcement_time_ms,
    a.adjunct_url,
    a.page_column,
    s.primary_hit_id,
    s.market,
    s.stock_code,
    s.stock_key,
    s.company_name,
    s.status AS summary_status,
    s.summary_failure_count,
    s.pdf_local_path,
    s.summary_json,
    s.summary_text,
    COALESCE(s.summary_tags, '[]'::jsonb) AS summary_tags,
    h.search_keyword
FROM announcement_summaries AS s
JOIN announcements AS a
  ON a.source = s.announcement_source
 AND a.announcement_id = s.announcement_id
LEFT JOIN announcement_hits AS h
  ON h.id = s.primary_hit_id
"""

# 投递候选在摘要字段之外追加 Telegram 投递状态和消息 ID。
DELIVERY_CANDIDATE_SQL = """
SELECT
    a.source,
    a.announcement_id,
    a.sec_code,
    a.sec_name,
    a.org_id,
    a.announcement_title,
    a.announcement_time_ms,
    a.adjunct_url,
    a.page_column,
    s.primary_hit_id,
    s.market,
    s.stock_code,
    s.stock_key,
    s.company_name,
    s.status AS summary_status,
    s.summary_failure_count,
    s.pdf_local_path,
    s.summary_json,
    s.summary_text,
    COALESCE(s.summary_tags, '[]'::jsonb) AS summary_tags,
    h.search_keyword,
    d.id AS delivery_id,
    d.status AS delivery_status,
    d.target_key,
    d.target_chat_id,
    d.target_message_thread_id,
    d.text_message_id,
    d.pdf_message_id
FROM telegram_deliveries AS d
JOIN announcement_summaries AS s
  ON s.announcement_source = d.announcement_source
 AND s.announcement_id = d.announcement_id
JOIN announcements AS a
  ON a.source = d.announcement_source
 AND a.announcement_id = d.announcement_id
LEFT JOIN announcement_hits AS h
  ON h.id = d.primary_hit_id
"""
