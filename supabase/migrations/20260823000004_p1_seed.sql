-- DFIP V1 P1 — seed: tenant placeholder, 67-column catalog, rate cards, 16 QA KPIs.
-- Does not seed New Logic campaign/template rows (that is P2 configuration load).
-- Does not compute costs or KPIs.

INSERT INTO client (id, code, name)
VALUES (
    'a0000000-0000-4000-8000-000000000001',
    'default',
    'Default client (V1 single-tenant placeholder)'
);

-- 67 workbook columns A:BO. excel_header is exact, including double spaces on E/F.
INSERT INTO we_source_column (
    excel_position, excel_letter, excel_header, db_column, source_role, value_kind, notes
) VALUES
(1,  'A',  'Filter Logic 1', 'filter_logic_1', 'derived_excel', 'text', 'VLOOKUP Campaign Name -> New Logic col G. Miss -> NULL.'),
(2,  'B',  'Filter Logic 2', 'filter_logic_2', 'derived_excel', 'text', 'VLOOKUP Campaign Name -> New Logic col H. Miss -> NULL.'),
(3,  'C',  'Template Status', 'template_status', 'derived_excel', 'text', 'IFERROR VLOOKUP Template Name (WhatsApp) -> New Logic R. Miss -> blank.'),
(4,  'D',  'AMC Status - Filter Logic 3', 'amc_status_filter_logic_3', 'derived_excel', 'text', 'VLOOKUP Campaign Name -> New Logic col J.'),
(5,  'E',  'AMC Device Category -  Filter Logic 4', 'amc_device_category_filter_logic_4', 'derived_excel', 'text', 'Double space before Filter. VLOOKUP New Logic col K.'),
(6,  'F',  'AMC Product Cat -  Filter Logic 5', 'amc_product_cat_filter_logic_5', 'derived_excel', 'text', 'Double space before Filter. VLOOKUP New Logic col L.'),
(7,  'G',  'Manual Or Automated', 'manual_or_automated', 'derived_excel', 'text', 'VLOOKUP Campaign Name -> New Logic col M.'),
(8,  'H',  'Total Cost', 'total_cost', 'derived_excel', 'numeric', 'Versioned rate card applied to Delivered. Not a Web Engage source field.'),
(9,  'I',  'HHH', 'hhh', 'derived_excel', 'text', 'TEXT(Start Date, "HH"). Blank Start Date currently yields 00.'),
(10, 'J',  'Month', 'month_label', 'derived_excel', 'text', 'TEXT(Day, "MMM-YY") e.g. Apr-25. month_start stored separately.'),
(11, 'K',  'Day', 'day', 'web_engage_source', 'date', 'Part of business key.'),
(12, 'L',  'Campaign Name', 'campaign_name', 'web_engage_source', 'text', 'Label lookup key. Not unique. Not the business key.'),
(13, 'M',  'Campaign ID', 'campaign_id', 'web_engage_source', 'text', 'Must remain text. Part of business key.'),
(14, 'N',  'Variation Name', 'variation_name', 'web_engage_source', 'text', NULL),
(15, 'O',  'Variation ID', 'variation_id', 'web_engage_source', 'text', 'Must remain text. Mixed-type corruption observed in Excel. Part of business key.'),
(16, 'P',  'Channel', 'channel', 'web_engage_source', 'text', 'Cost-card match field after Utility template status.'),
(17, 'Q',  'Type of Campaign', 'type_of_campaign', 'web_engage_source', 'text', NULL),
(18, 'R',  'Status', 'status', 'web_engage_source', 'text', 'Retained in staging JSONB; not a report dimension.'),
(19, 'S',  'Segment Name', 'segment_name', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(20, 'T',  'Segment ID', 'segment_id', 'web_engage_source', 'text', '99.87% blank in production. Not a key.'),
(21, 'U',  'Journey Name', 'journey_name', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(22, 'V',  'Journey ID', 'journey_id', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(23, 'W',  'Campaign Tags', 'campaign_tags', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(24, 'X',  'Start Date', 'start_date', 'web_engage_source', 'timestamp', 'Input to HHH.'),
(25, 'Y',  'Created By', 'created_by', 'web_engage_source', 'text', 'PII. Store in staging; do not expose via API.'),
(26, 'Z',  'Conversion Event', 'conversion_event', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(27, 'AA', 'Conversion Deadline', 'conversion_deadline', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(28, 'AB', 'Control Group', 'control_group', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(29, 'AC', 'Total in Control Group', 'total_in_control_group', 'web_engage_source', 'integer', 'Retained in staging JSONB.'),
(30, 'AD', 'Unique Control Group Conversions', 'unique_control_group_conversions', 'web_engage_source', 'integer', 'Retained in staging JSONB.'),
(31, 'AE', 'Unique Control Group Conversion Rate', 'unique_control_group_conversion_rate', 'web_engage_source', 'numeric', 'Native rate. Do not sum. Not a report field.'),
(32, 'AF', 'Sent', 'sent', 'web_engage_source', 'integer', 'Additive report measure.'),
(33, 'AG', 'Failed', 'failed', 'web_engage_source', 'integer', 'Additive report measure.'),
(34, 'AH', 'Queued', 'queued', 'web_engage_source', 'integer', 'Entirely null in recovered cache.'),
(35, 'AI', 'Delivered', 'delivered', 'web_engage_source', 'integer', 'Additive report measure. Cost input.'),
(36, 'AJ', 'Unique Impressions', 'unique_impressions', 'web_engage_source', 'integer', 'Additive report measure.'),
(37, 'AK', 'Unique Clicks', 'unique_clicks', 'web_engage_source', 'integer', 'Additive report measure.'),
(38, 'AL', 'Unique Conversions', 'unique_conversions', 'web_engage_source', 'integer', 'Additive report measure.'),
(39, 'AM', 'Unique Impression-Through Conversions', 'unique_impression_through_conversions', 'web_engage_source', 'integer', 'Used by Q&A KPIs 8-13.'),
(40, 'AN', 'Unique Click-Through Conversions', 'unique_click_through_conversions', 'web_engage_source', 'integer', 'Additive report measure.'),
(41, 'AO', 'Failed Rate', 'failed_rate', 'web_engage_source', 'numeric', 'Native rate. Do not sum. Not a report field.'),
(42, 'AP', 'Queued Rate', 'queued_rate', 'web_engage_source', 'numeric', 'Native rate. Entirely null.'),
(43, 'AQ', 'Delivered Rate', 'delivered_rate_native', 'web_engage_source', 'numeric', 'Native rate. Do not sum. Distinct from KPI Delivered Rate*.'),
(44, 'AR', 'Unique Impression Rate', 'unique_impression_rate', 'web_engage_source', 'numeric', 'Native rate. Do not sum.'),
(45, 'AS', 'Unique Click Rate', 'unique_click_rate', 'web_engage_source', 'numeric', 'Native rate. Do not sum.'),
(46, 'AT', 'Unique Conversion Rate', 'unique_conversion_rate', 'web_engage_source', 'numeric', 'Native rate. Do not sum.'),
(47, 'AU', 'Unique Impression-Through Conversion Rate', 'unique_impression_through_conversion_rate', 'web_engage_source', 'numeric', 'Native rate. Do not sum.'),
(48, 'AV', 'Unique Click-Through Conversion Rate', 'unique_click_through_conversion_rate', 'web_engage_source', 'numeric', 'Native rate. Do not sum.'),
(49, 'AW', 'Revenue (INR)', 'revenue_inr', 'web_engage_source', 'numeric', 'Additive report measure.'),
(50, 'AX', 'Impression-Through Revenue (INR)', 'impression_through_revenue_inr', 'web_engage_source', 'numeric', 'Used by Q&A KPIs 11.'),
(51, 'AY', 'Click-Through Revenue (INR)', 'click_through_revenue_inr', 'web_engage_source', 'numeric', 'Additive report measure.'),
(52, 'AZ', 'Failed (DND Queue Drop)', 'failed_dnd_queue_drop', 'web_engage_source', 'integer', 'Retained in staging JSONB.'),
(53, 'BA', 'Failed (Frequency Capping Queue Drop)', 'failed_frequency_capping_queue_drop', 'web_engage_source', 'integer', 'Retained in staging JSONB.'),
(54, 'BB', 'Failed (Personalization Error)', 'failed_personalization_error', 'web_engage_source', 'integer', 'Retained in staging JSONB.'),
(55, 'BC', 'Failed (Channel Not Available)', 'failed_channel_not_available', 'web_engage_source', 'integer', 'Retained in staging JSONB.'),
(56, 'BD', 'Layout Name', 'layout_name', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(57, 'BE', 'ESP/SSP/WSP name', 'esp_ssp_wsp_name', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(58, 'BF', 'Title/Subject Line', 'title_subject_line', 'web_engage_source', 'text', 'Creative content. Do not expose via API.'),
(59, 'BG', 'Message', 'message', 'web_engage_source', 'text', 'Creative content. Do not expose via API.'),
(60, 'BH', 'Default CTA Label', 'default_cta_label', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(61, 'BI', 'Default CTA Link', 'default_cta_link', 'web_engage_source', 'text', 'Creative content. Do not expose via API.'),
(62, 'BJ', 'Image', 'image', 'web_engage_source', 'text', 'Creative content. Do not expose via API.'),
(63, 'BK', 'Key-value Pairs', 'key_value_pairs', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(64, 'BL', 'Sender ID (SMS)', 'sender_id_sms', 'web_engage_source', 'text', 'Mixed type in Excel. Retain as text.'),
(65, 'BM', 'From Name (Email)', 'from_name_email', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(66, 'BN', 'From Email (Email)', 'from_email_email', 'web_engage_source', 'text', 'Retained in staging JSONB.'),
(67, 'BO', 'Template Name (WhatsApp)', 'template_name_whatsapp', 'web_engage_source', 'text', 'Template label lookup key.');

-- Rate cards: independently versioned. M1.4 locked two vintages.
-- P1 prompt lists the Aug-Oct card; Apr-Jul is also seeded because the lock requires it.
INSERT INTO rate_card_version (
    id, client_id, version_label, effective_from, effective_to, status, notes
) VALUES
(
    'a0000000-0000-4000-8000-000000000011',
    'a0000000-0000-4000-8000-000000000001',
    'rate-v1',
    '2025-04-01',
    '2025-08-01',
    'superseded',
    'Apr-Jul 2025 production workbooks. Utility 0.12 / WhatsApp 0.83.'
),
(
    'a0000000-0000-4000-8000-000000000012',
    'a0000000-0000-4000-8000-000000000001',
    'rate-v2',
    '2025-08-01',
    NULL,
    'active',
    'Aug-Oct 2025 production workbooks. Utility 0.115 / WhatsApp 0.785. Listed in the P1 prompt.'
);

INSERT INTO rate_card_rule (
    id, version_id, priority, match_field, match_value, match_mode, rate, applies_to_measure
) VALUES
('a0000000-0000-4000-8000-000000000111', 'a0000000-0000-4000-8000-000000000011', 1, 'template_status', 'Utility', 'equals_ci', 0.120000, 'delivered'),
('a0000000-0000-4000-8000-000000000112', 'a0000000-0000-4000-8000-000000000011', 2, 'channel', 'SMS', 'equals_ci', 0.150000, 'delivered'),
('a0000000-0000-4000-8000-000000000113', 'a0000000-0000-4000-8000-000000000011', 3, 'channel', 'Email', 'equals_ci', 0.010000, 'delivered'),
('a0000000-0000-4000-8000-000000000114', 'a0000000-0000-4000-8000-000000000011', 4, 'channel', 'RCS', 'equals_ci', 0.250000, 'delivered'),
('a0000000-0000-4000-8000-000000000115', 'a0000000-0000-4000-8000-000000000011', 5, 'channel', 'WhatsApp', 'equals_ci', 0.830000, 'delivered'),
('a0000000-0000-4000-8000-000000000121', 'a0000000-0000-4000-8000-000000000012', 1, 'template_status', 'Utility', 'equals_ci', 0.115000, 'delivered'),
('a0000000-0000-4000-8000-000000000122', 'a0000000-0000-4000-8000-000000000012', 2, 'channel', 'SMS', 'equals_ci', 0.150000, 'delivered'),
('a0000000-0000-4000-8000-000000000123', 'a0000000-0000-4000-8000-000000000012', 3, 'channel', 'Email', 'equals_ci', 0.010000, 'delivered'),
('a0000000-0000-4000-8000-000000000124', 'a0000000-0000-4000-8000-000000000012', 4, 'channel', 'RCS', 'equals_ci', 0.250000, 'delivered'),
('a0000000-0000-4000-8000-000000000125', 'a0000000-0000-4000-8000-000000000012', 5, 'channel', 'WhatsApp', 'equals_ci', 0.785000, 'delivered');

-- 16 Q&A reporting KPIs. Exact recovered display names (spaces/asterisk preserved).
-- solve_order 12 and 13 depend on KPI 10.
INSERT INTO kpi_definition (
    id, namespace, display_name, slug, formula, numerator_measure, denominator_measure,
    operation, is_linear, solve_order, depends_on_kpi_id, is_used_by_reports, notes
) VALUES
('a0000000-0000-4000-8000-000000000201', 'qa', 'CTR', 'ctr',
    '''Unique Clicks''/Delivered', 'unique_clicks', 'delivered',
    'divide', false, 1, NULL, true, NULL),
('a0000000-0000-4000-8000-000000000202', 'qa', 'Conversion Rate', 'conversion_rate',
    '''Unique Conversions''/Delivered', 'unique_conversions', 'delivered',
    'divide', false, 2, NULL, true, NULL),
('a0000000-0000-4000-8000-000000000203', 'qa', 'ROAS', 'roas',
    '''Revenue (INR)''/''Total Cost''', 'revenue_inr', 'total_cost',
    'divide', false, 3, NULL, true, NULL),
('a0000000-0000-4000-8000-000000000204', 'qa', 'Delivered Rate*', 'delivered_rate_star',
    'Delivered/Sent', 'delivered', 'sent',
    'divide', false, 4, NULL, true, 'Exact recovered name includes trailing asterisk.'),
('a0000000-0000-4000-8000-000000000205', 'qa', 'Cost/Conv', 'cost_conv',
    '''Total Cost''/''Unique Click-Through Conversions''', 'total_cost', 'unique_click_through_conversions',
    'divide', false, 5, NULL, true, NULL),
('a0000000-0000-4000-8000-000000000206', 'qa', 'Click thou Conv Rate', 'click_thou_conv_rate',
    '''Unique Click-Through Conversions''/Delivered', 'unique_click_through_conversions', 'delivered',
    'divide', false, 6, NULL, true, NULL),
('a0000000-0000-4000-8000-000000000207', 'qa', 'Click Through Cost/Conv', 'click_through_cost_conv',
    '''Total Cost''/''Unique Click-Through Conversions''', 'total_cost', 'unique_click_through_conversions',
    'divide', false, 7, NULL, true, 'Duplicate formula of Cost/Conv. Both retained.'),
('a0000000-0000-4000-8000-000000000208', 'qa', 'Cost/ View through Conv ', 'cost_view_through_conv',
    '''Total Cost''/''Unique Impression-Through Conversions''', 'total_cost', 'unique_impression_through_conversions',
    'divide', false, 8, NULL, true, 'Exact recovered name includes trailing space.'),
('a0000000-0000-4000-8000-000000000209', 'qa', 'View through Conv Rate', 'view_through_conv_rate',
    '''Unique Impression-Through Conversions''/Delivered', 'unique_impression_through_conversions', 'delivered',
    'divide', false, 9, NULL, true, NULL),
('a0000000-0000-4000-8000-000000000210', 'qa', 'Click + View Conv ', 'click_plus_view_conv',
    '''Unique Impression-Through Conversions''+''Unique Click-Through Conversions''',
    'unique_impression_through_conversions', 'unique_click_through_conversions',
    'add', true, 10, NULL, true, 'Exact recovered name includes trailing space. Linear.'),
('a0000000-0000-4000-8000-000000000211', 'qa', 'Click + View Revenue', 'click_plus_view_revenue',
    '''Impression-Through Revenue (INR)''+''Click-Through Revenue (INR)''',
    'impression_through_revenue_inr', 'click_through_revenue_inr',
    'add', true, 11, NULL, true, 'Linear.'),
('a0000000-0000-4000-8000-000000000212', 'qa', 'Cost/ Click + View Conv ', 'cost_click_plus_view_conv',
    '''Total Cost''/''Click + View Conv ''', 'total_cost', NULL,
    'divide', false, 12, 'a0000000-0000-4000-8000-000000000210', true,
    'Depends on KPI 10. Exact recovered name includes trailing space.'),
('a0000000-0000-4000-8000-000000000213', 'qa', 'All Click + View Conv Rate', 'all_click_plus_view_conv_rate',
    '''Click + View Conv ''/Delivered', NULL, 'delivered',
    'divide', false, 13, 'a0000000-0000-4000-8000-000000000210', true,
    'Depends on KPI 10.'),
('a0000000-0000-4000-8000-000000000214', 'qa', 'Delivered to Imp. Rate', 'delivered_to_imp_rate',
    '''Unique Impressions''/Delivered', 'unique_impressions', 'delivered',
    'divide', false, 14, NULL, true, NULL),
('a0000000-0000-4000-8000-000000000215', 'qa', 'CTR ( Del to Click )', 'ctr_del_to_click',
    '''Unique Clicks''/Delivered', 'unique_clicks', 'delivered',
    'divide', false, 15, NULL, true, 'Duplicate formula of CTR. Both retained.'),
('a0000000-0000-4000-8000-000000000216', 'qa', 'CTR ( Impr. to Click )', 'ctr_impr_to_click',
    '''Unique Clicks''/''Unique Impressions''', 'unique_clicks', 'unique_impressions',
    'divide', false, 16, NULL, true, NULL);
