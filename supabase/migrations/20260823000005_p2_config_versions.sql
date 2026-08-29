-- DFIP V1 P2 — configuration versions, Filter Logic 1_2 membership, client KPI namespace.
-- Campaign/template rows are in following P2 migrations.
-- Does not alter the 16 qa KPI rows seeded in P1.

ALTER TABLE label_group_member
    ALTER COLUMN group_name DROP NOT NULL,
    ALTER COLUMN filter_logic_1_value DROP NOT NULL;

INSERT INTO campaign_label_version (id, client_id, version_label, effective_from, effective_to, row_count, distinct_key_count, duplicate_key_count, status, notes)
VALUES
('a0000000-0000-4000-8000-000000000021', 'a0000000-0000-4000-8000-000000000001', 'campaign-v1', '2025-04-01', '2025-08-01', 3905, 3174, 371, 'superseded', 'New Logic A:M from 11. WE Report Raw Data Apr toi May 25 VJ Prod.xlsx'),
('a0000000-0000-4000-8000-000000000022', 'a0000000-0000-4000-8000-000000000001', 'campaign-v2', '2025-08-01', NULL, 4093, 3339, 389, 'active', 'New Logic A:M from 14. WE Report Raw Data Aug-25 VJ Prod.xlsx');

INSERT INTO template_label_version (id, client_id, version_label, effective_from, effective_to, row_count, status, notes) VALUES
('a0000000-0000-4000-8000-000000000031', 'a0000000-0000-4000-8000-000000000001', 'template-v1', '2025-04-01', '2025-06-01', 41, 'superseded', 'New Logic Q:R from 11. WE Report Raw Data Apr toi May 25 VJ Prod.xlsx'),
('a0000000-0000-4000-8000-000000000032', 'a0000000-0000-4000-8000-000000000001', 'template-v2', '2025-06-01', '2025-07-01', 41, 'superseded', 'New Logic Q:R from 12. WE Report Raw Data June 25 VJ Prod.xlsx'),
('a0000000-0000-4000-8000-000000000033', 'a0000000-0000-4000-8000-000000000001', 'template-v3', '2025-07-01', '2025-08-01', 42, 'superseded', 'New Logic Q:R from 13. WE Report Raw Data July 25 VJ Prod.xlsx'),
('a0000000-0000-4000-8000-000000000034', 'a0000000-0000-4000-8000-000000000001', 'template-v4', '2025-08-01', NULL, 49, 'active', 'New Logic Q:R from 14. WE Report Raw Data Aug-25 VJ Prod.xlsx');

INSERT INTO label_group_version (id, client_id, version_label, status, notes) VALUES
('a0000000-0000-4000-8000-000000000041', 'a0000000-0000-4000-8000-000000000001', 'fl1-group-v1', 'active', 'pivotCacheDefinition1.xml fieldGroup Filter Logic 1_2');

INSERT INTO label_group_member (version_id, row_order, group_name, filter_logic_1_value) VALUES
('a0000000-0000-4000-8000-000000000041', 1, 'Group7', 'TAMC | D2C AMC|Manual Campaign'),
('a0000000-0000-4000-8000-000000000041', 2, 'Group2', 'Service | FMS & LMS | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 3, 'Group6', 'TEST'),
('a0000000-0000-4000-8000-000000000041', 4, 'Group2', 'Service | Campaigns | IT Related'),
('a0000000-0000-4000-8000-000000000041', 5, 'Group5', 'D2C Product| CLTV | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 6, 'Group1', 'TAMC | D2C AMC|API Data'),
('a0000000-0000-4000-8000-000000000041', 7, 'Group3', 'Direct Sales | Book A Demo |Water Softner'),
('a0000000-0000-4000-8000-000000000041', 8, 'Group1', 'Direct Sales CLTV | Book A Demo | Athena API'),
('a0000000-0000-4000-8000-000000000041', 9, 'Group2', 'Service | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 10, 'Group1', 'FSC| Service | Campaigns | API'),
('a0000000-0000-4000-8000-000000000041', 11, 'Group1', 'D2C Product| API Event | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 12, 'Group3', 'Direct Sales CLTV | Book A Demo| Robotic'),
('a0000000-0000-4000-8000-000000000041', 13, 'Group1', 'TAMC | D2C AMC|COCO | API Data'),
('a0000000-0000-4000-8000-000000000041', 14, 'Group6', 'AMC or D2C Find More'),
('a0000000-0000-4000-8000-000000000041', 15, 'Group2', 'D2C Product|Service | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 16, 'Group5', 'D2C Product| Pros | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 17, 'Group6', 'IOT | Event Based | App Push Campaigns'),
('a0000000-0000-4000-8000-000000000041', 18, 'Group2', 'Service | Campaigns | Termination BP '),
('a0000000-0000-4000-8000-000000000041', 19, 'Group7', 'AMC | FSC | Service | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 20, 'Group2', 'D2C |Service | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 21, 'Group6', 'IT | Transactional Invoice Download Campiagn'),
('a0000000-0000-4000-8000-000000000041', 22, 'Group2', 'Service Manual | Open Work Order Reminder Campaign'),
('a0000000-0000-4000-8000-000000000041', 23, 'Group3', 'Direct Sales | Book A Demo |Forbes Pro'),
('a0000000-0000-4000-8000-000000000041', 24, 'Group1', 'Ecom Rating | API Data | Squadstack'),
('a0000000-0000-4000-8000-000000000041', 25, 'Group4', 'Rental| Rec-Renewal | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 26, 'Group4', 'Rental| Standard Event Based | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 27, 'Group7', 'TAMC | D2C AMC|Automated Renewal Campaign'),
('a0000000-0000-4000-8000-000000000041', 28, 'Group5', 'D2C Product |Sales Assist | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 29, 'Group2', 'Service | Technician | Manual Camp'),
('a0000000-0000-4000-8000-000000000041', 30, 'Group2', 'FSC | Service | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 31, 'Group2', 'Service | Campaigns | Find more'),
('a0000000-0000-4000-8000-000000000041', 32, 'Group3', 'Brand Marketing| Consumer research | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 33, 'Group2', 'Service | Campaigns | Legal Team'),
('a0000000-0000-4000-8000-000000000041', 34, 'Group3', 'Retail Pricing Campaign'),
('a0000000-0000-4000-8000-000000000041', 35, 'Group2', 'Service | ST | Direct Sales'),
('a0000000-0000-4000-8000-000000000041', 36, 'Group3', 'Direct Sales CLTV | Book A Demo | VC'),
('a0000000-0000-4000-8000-000000000041', 37, NULL, NULL),
('a0000000-0000-4000-8000-000000000041', 38, 'TAMC | D2C AMC|Manual Campaign Bain Trail 4', 'TAMC | D2C AMC|Manual Campaign Bain Trail 4'),
('a0000000-0000-4000-8000-000000000041', 39, 'Group2', 'Service | ST | Manual'),
('a0000000-0000-4000-8000-000000000041', 40, 'Group2', 'LMS | Service | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 41, 'TAMC | D2C AMC|Manual Campaign Bain Trail 3', 'TAMC | D2C AMC|Manual Campaign Bain Trail 3'),
('a0000000-0000-4000-8000-000000000041', 42, 'OWOC Service Booking Drop off | Campaigns', 'OWOC Service Booking Drop off | Campaigns'),
('a0000000-0000-4000-8000-000000000041', 43, '0', '0');

