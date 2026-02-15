# GroupMe Calendar Sync

Syncs GroupMe events into a committed `calendar.ics` file so calendar clients can subscribe via a stable GitHub Pages URL.

## How it works

- `scripts/groupme_to_ics.py` fetches events from GroupMe and renders deterministic ICS output.
- GitHub Actions runs hourly and commits `calendar.ics` only when content changes.
- GitHub Pages serves the ICS file for public subscription.

## Required configuration

Add the following repository secrets:

- `GROUPME_TOKEN`: GroupMe access token.
- `GROUP_ID`: Group ID to sync.

Optional environment values:

- `ICS_OUTPUT_PATH` (default: `calendar.ics`)
- `DEFAULT_TZ` (default: `UTC`)
- `GROUPME_BASE_URL` (default: `https://api.groupme.com`)
- `EVENTS_LIMIT` (default: `200`)
- `EVENTS_END_AT` (default: `1970-01-01T00:00:00Z`)

## Local integration testing

Local integration testing is expected to run inside a project virtual environment (`.venv`).

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```
2. Export environment variables:
   ```bash
   export GROUPME_TOKEN="..."
   export GROUP_ID="..."
   ```
3. Run a real API sync locally:
   ```bash
   python scripts/groupme_to_ics.py --output /tmp/calendar.ics --verbose
   ```
4. Run smoke test without writing files:
   ```bash
   python scripts/groupme_to_ics.py --dry-run --verbose
   ```
5. Run against a local/mock API server:
   ```bash
   export GROUPME_BASE_URL="http://localhost:8080"
   python scripts/groupme_to_ics.py --output /tmp/calendar.ics --verbose
   ```
6. Or run the venv-based integration helper:
   ```bash
   ./scripts/local_integration_test.sh
   ```

## GitHub Pages setup

1. Go to repository `Settings` -> `Pages`.
2. Set source to `Deploy from a branch`.
3. Select your default branch and `/ (root)` folder.
4. After first publish, subscribe using:

```text
https://<owner>.github.io/<repo>/calendar.ics
```

## GitHub Action schedule

Workflow file: `.github/workflows/sync-calendar.yml`

- Hourly cron: `0 * * * *`
- Manual runs: `workflow_dispatch`
