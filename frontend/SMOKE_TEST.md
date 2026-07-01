# Frontend Manual Smoke Test

## Start

1. Ingest the demo database:

   ```bash
   python3 backend/scripts/ingest_data.py
   ```

2. Start the API:

   ```bash
   python3 -m uvicorn backend.api:app --reload
   ```

3. Open `http://127.0.0.1:8000/demo/`.

## Verify

1. Confirm four demo users load and browser requests contain
   `X-Demo-User`, never `company_id`.
2. Select `operator.company_1@example.com`.
3. Ask “List my plants”; confirm only Company 1 plants appear.
4. Select a financial report and generate it; confirm a permission error and
   no document entry.
5. Generate an energy report in each format and download the files.
6. Switch to `admin.company_1@example.com`, generate a financial report, and
   download it.
7. Switch back to the operator and confirm the admin report is not present in
   the page’s session list.
8. In browser developer tools, attempt the admin document URL with the
   operator header and confirm the API returns `404`.
9. Refresh the page. Confirm the selected user is restored, previous report
   downloads reappear, and saved chat answers are shown as recovered results.
