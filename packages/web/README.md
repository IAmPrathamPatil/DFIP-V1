# packages/web

P6 Python origin server (`dfip_web`) for the Admin/Client SPA in `apps/web/static`.

- Serves static HTML/CSS/JS and `/config.json` (`apiBaseUrl`, `apiPrefix`, `adminRoles`, `uploadMaxBytes`, `uploadMaxFiles`, `uploadMaxTotalBytes`)
- Does not proxy `/api/v1`
- Does not import `dfip_core`
- Includes `DfipApiClient` for tests (the browser uses `apps/web/static/js/api-client.js`)
- Builds the empty client workbook **structure** via `dfip_web.client_workbook`
  (Settings + Facts tables only). That builder does not create Office
  DataMashup and cannot Refresh All. The M script stays in `excel/PublishedFacts.m`.

V1 is complete at P10. A live database, RLS, and production IdP are V2.

```powershell
python -m dfip_web
```
