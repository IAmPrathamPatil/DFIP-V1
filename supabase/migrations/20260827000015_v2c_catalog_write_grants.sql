-- V2-C: publisher Logic/Labels uploads write existing catalog tables.
-- Does not alter locked P1/P2 table shapes or RLS policy text.
-- Application-level authorization remains primary; RLS stays defense in depth.

GRANT INSERT, UPDATE ON TABLE campaign_label_version TO dfip_api;
GRANT INSERT, UPDATE ON TABLE campaign_label_version TO dfip_worker;
GRANT INSERT, UPDATE ON TABLE campaign_label_row TO dfip_api;
GRANT INSERT, UPDATE ON TABLE campaign_label_row TO dfip_worker;
GRANT INSERT, UPDATE ON TABLE label_group_version TO dfip_api;
GRANT INSERT, UPDATE ON TABLE label_group_version TO dfip_worker;
GRANT INSERT, UPDATE ON TABLE label_group_member TO dfip_api;
GRANT INSERT, UPDATE ON TABLE label_group_member TO dfip_worker;
