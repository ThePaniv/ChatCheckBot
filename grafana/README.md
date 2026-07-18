# Grafana dashboard

[`dashboard.json`](dashboard.json) is an importable Grafana dashboard for the bot's
read-only stats endpoint (see the [Dashboard section](../README.md#dashboard) of the root
README for the endpoint itself). It has 12 panels over three rows:

- **Overview** — stat tiles (total users, active users, cancelled users, total checks,
  response rate) and a donut of the yes/no/ignored breakdown, all off `?view=summary`.
- **Activity** — a *Recent checks* table and an *answer-latency-over-time* scatter, off
  `?view=logs`.
- **Users** — a per-user table (frequency, next check, active), off `?view=users`.

## Import

1. **Add the Infinity datasource** (once):
   - Grafana → **Connections → Add new connection → Infinity** (install the plugin if
     prompted) → **Add new data source**.
   - Under **URL, Headers & Params → Headers**, add `Authorization` = `Bearer <stats_token>`
     (`cd infra && terraform output -raw stats_token`). Keeping the token on the datasource
     means it never lives in the dashboard JSON.
   - Save & test.
2. **Import the dashboard:**
   - Grafana → **Dashboards → New → Import** → **Upload JSON file** (`dashboard.json`) or
     paste its contents.
   - When prompted for the **Infinity** datasource input, pick the one from step 1.
3. **Set the base URL:** open the dashboard and set the **Stats base URL** textbox (top of
   the dashboard) to your `terraform output -raw stats_url` value (keep the trailing slash).
   The panels interpolate it into every query.

## Sharing

Share it with **signed-in members of your Grafana org** (Dashboard → **Share → Link**, or
add members to the org) — **not** as a public dashboard, because the *Recent checks* and
*Users* panels show first names and usernames. If you ever do want a public link, build a
separate dashboard containing only the `summary` (anonymized, counts-only) panels.

## Notes

- `checked_at` / `next_check_at` are parsed from epoch seconds by the Infinity **backend**
  parser (`timestamp_epoch_s`); `frequency_seconds` and `latency_seconds` render with the
  `s` unit, so 43200 shows as `12 hour`, etc.
- The endpoint scans both DynamoDB tables on each refresh (dashboard auto-refresh is 5m).
  Fine at hobby scale; if the logs table grows large, add server-side windowing to
  `stats_handler` (e.g. a `?since=` epoch filter) before lowering the refresh interval.
