# KPI dictionary — Power BI mapping

Authoritative formulas: `packages/analytics/dfip_analytics/kpis.py`.
DAX: `powerbi/dax/measures.dax` (generated from `powerbi_contract.py`).
Ratios are `DIVIDE(SUM(num), SUM(den))`. Additive measures are `SUM`.
Add/subtract KPIs are BLANK-safe after SUM.

## Additive facts

| Report measure | Column | DAX pattern | Additive | Card format |
|---|---|---|---|---|
| Sent | `sent` | SUM | yes | #,0 |
| Failed | `failed` | SUM | yes | #,0 |
| Delivered | `delivered` | SUM | yes | #,0 |
| Impressions | `unique_impressions` | SUM | yes | #,0 |
| Clicks | `unique_clicks` | SUM | yes | #,0 |
| Conversions | `unique_conversions` | SUM | yes | #,0 |
| Impression-Through Conversions | `unique_impression_through_conversions` | SUM | yes | #,0 |
| Click-Through Conversions | `unique_click_through_conversions` | SUM | yes | #,0 |
| Revenue | `revenue_inr` | SUM | yes | ₹ #,0.00 |
| Impression-Through Revenue | `impression_through_revenue_inr` | SUM | yes | ₹ #,0.00 |
| Click-Through Revenue | `click_through_revenue_inr` | SUM | yes | ₹ #,0.00 |
| Total Cost | `total_cost` | SUM | yes | ₹ #,0.00 |

## Q&A recovered KPIs

| Report measure | Namespace.slug | After SUM | Additive | Card format |
|---|---|---|---|---|
| CTR | qa.ctr | unique_clicks / delivered | no | 0.00% |
| Conversion Rate | qa.conversion_rate | unique_conversions / delivered | no | 0.00% |
| ROAS | qa.roas | revenue_inr / total_cost | no | 0.00 |
| Delivered Rate* | qa.delivered_rate_star | delivered / sent | no | 0.00% |
| Cost/Conv | qa.cost_conv | total_cost / unique_click_through_conversions | no | ₹ #,0.00 |
| Click thou Conv Rate | qa.click_thou_conv_rate | unique_click_through_conversions / delivered | no | 0.00% |
| QA Click Through Cost/Conv | qa.click_through_cost_conv | same as Cost/Conv | no | ₹ #,0.00 |
| Cost/ View through Conv | qa.cost_view_through_conv | total_cost / unique_impression_through_conversions | no | ₹ #,0.00 |
| View through Conv Rate | qa.view_through_conv_rate | unique_impression_through_conversions / delivered | no | 0.00% |
| Click + View Conv | qa.click_plus_view_conv | UIT conv + UCT conv | yes | #,0 |
| Click + View Revenue | qa.click_plus_view_revenue | IT revenue + CT revenue | yes | ₹ #,0.00 |
| Cost/ Click + View Conv | qa.cost_click_plus_view_conv | total_cost / [Click + View Conv] | no | ₹ #,0.00 |
| All Click + View Conv Rate | qa.all_click_plus_view_conv_rate | [Click + View Conv] / delivered | no | 0.00% |
| Delivered to Imp. Rate | qa.delivered_to_imp_rate | unique_impressions / delivered | no | 0.00% |
| CTR ( Del to Click ) | qa.ctr_del_to_click | same as CTR | no | 0.00% |
| QA CTR ( Impr. to Click ) | qa.ctr_impr_to_click | unique_clicks / unique_impressions | no | 0.00% |

## Client recovered KPIs

| Report measure | Namespace.slug | After SUM | Additive | Card format |
|---|---|---|---|---|
| Delivery Rate | client.delivery_rate | delivered / sent | no | 0.00% |
| Delivered to Imp. rate | client.delivered_to_imp_rate | unique_impressions / delivered | no | 0.00% |
| CTR (Del to Clicks) | client.ctr_del_to_clicks | unique_clicks / delivered | no | 0.00% |
| Client CTR ( Impr. to Click ) | client.ctr_impr_to_click | unique_clicks / unique_impressions | no | 0.00% |
| Client Click Through Cost/Conv | client.click_through_cost_conv | total_cost / unique_click_through_conversions | no | ₹ #,0.00 |
| Cost/Unique Conversion | client.cost_unique_conversion | total_cost / unique_conversions | no | ₹ #,0.00 |
| Delivered Thru Conv. rate | client.delivered_thru_conv_rate | unique_conversions / delivered | no | 0.00% |
| Cost/ UCT conversion | client.cost_uct_conversion | total_cost / unique_click_through_conversions | no | ₹ #,0.00 |
| UCT conversion rate | client.uct_conversion_rate | unique_click_through_conversions / unique_clicks | no | 0.00% |
| UC to UCTC Conversions | client.uc_to_uctc_conversions | unique_conversions − unique_click_through_conversions | yes | #,0 |
| UC to UCTC Revenue | client.uc_to_uctc_revenue | revenue_inr − click_through_revenue_inr | yes | ₹ #,0.00 |
| Actual Sent | client.actual_sent | sent − failed | yes | #,0 |
| Failed Rate SM | client.failed_rate_sm | failed / sent | no | 0.00% |
| Unique Click Through Conv ROAS | client.unique_click_through_conv_roas | click_through_revenue_inr / total_cost | no | 0.00 |
| Overall ROAS | client.overall_roas | revenue_inr / total_cost | no | 0.00 |

Executive cards use Q&A names plus `Delivery Rate` (same formula as
`Delivered Rate*` / delivered ÷ sent). Duplicate recovered names are prefixed
`QA ` or `Client `. Native Web Engage rate columns are not measures.

Known aliases (documentation only, not extra grains): Impressions →
`unique_impressions`, Clicks → `unique_clicks`, Spend → `total_cost`,
Sales → `revenue_inr`.

Not in the model: Orders, Amazon CPC, ACOS, Product/ASIN.
