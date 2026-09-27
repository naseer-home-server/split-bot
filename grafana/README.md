# Grafana Dashboard for Split Bot

This directory contains Grafana dashboard configurations for monitoring the
Split Bot application.

## Dashboard Files

- `split-bot-dashboard.json` - Main dashboard with comprehensive metrics
  visualization
- `push.py` - Script to automatically push the dashboard to Grafana via API

## How to Import

### Option 1: Using the Push Script (Recommended)

The `push.py` script automatically pushes the dashboard to your Grafana instance
via the API.

**Prerequisites:**

- Grafana API key/token with admin permissions
- Python 3.x
- `requests` library (already in requirements.txt)

**Setup Environment Variables:**

```bash
export GRAFANA_URL="http://localhost:3000"  # Your Grafana URL
export GRAFANA_API_KEY="your-api-key-here"  # Grafana API key/token
```

**Optional Environment Variables:**

```bash
export GRAFANA_FOLDER_ID="1"              # Folder ID to place dashboard in
export GRAFANA_FOLDER_NAME="Split Bot"     # Or use folder name (will create if doesn't exist)
export GRAFANA_OVERWRITE="true"           # Overwrite if dashboard exists (default: true)
export GRAFANA_DASHBOARD_PATH="./split-bot-dashboard.json"  # Custom dashboard path
```

**Run the Script:**

```bash
python grafana/push.py
```

**Getting a Grafana API Key:**

1. Log into Grafana
2. Go to Configuration → API Keys (or `/org/apikeys`)
3. Click "New API Key"
4. Set name, role (Admin recommended), and expiration
5. Copy the generated key

### Option 2: Manual Import via UI

1. **Open Grafana** and navigate to the Dashboards section
2. Click the **"+"** icon → **"Import"**
3. Click **"Upload JSON file"** and select `split-bot-dashboard.json`, or paste
   the JSON content directly
4. Click **"Load"**
5. Select your Prometheus data source from the dropdown (or create one if
   needed)
6. Click **"Import"**

## Prerequisites

- Grafana instance running
- Prometheus data source configured in Grafana
- Prometheus scraping metrics from the Split Bot server at `/metrics` endpoint
- The Prometheus job label should be `split-bot` (or update the queries in the
  dashboard)

## Dashboard Panels

The dashboard includes the following panels:

1. **HTTP Request Rate** - Request rate by method and endpoint
2. **Database Connection Status** - Real-time connection status gauge
3. **AI Processing Duration** - p95, p99, and mean latency by platform
4. **AI Processing Rate** - Success/failure rate by platform
5. **OCR Processing Duration** - p95, p99, and mean latency by source type
6. **OCR Processing Rate** - Success/failure rate by source type
7. **Messages Processed Rate** - Rate by group_id and platform
   status
8. **Users Created Rate** - User creation rate over time
9. **AI Processing Errors** - Error rate by platform and error type
10. **OCR Processing Errors** - Error rate by source type and error type
11. **Database Query Duration** - p95, p99, and mean query latency
12. **Database Errors** - Error rate by operation and error type

## Configuration

### Prometheus Job Label

The dashboard queries use `job="split-bot"` as the job label. If your Prometheus
configuration uses a different job label, you'll need to update the queries in
the dashboard:

1. Open the dashboard in Grafana
2. Edit any panel
3. Update the `job` label in the PromQL queries from `split-bot` to your actual
   job label

### Data Source Variable

The dashboard uses `${DS_PROMETHEUS}` as a variable for the Prometheus data
source. When importing, Grafana will prompt you to select your Prometheus data
source, which will automatically configure this variable.

## Customization

After importing, you can customize:

- Time range (default: last 6 hours)
- Refresh interval (default: 30 seconds)
- Panel sizes and positions
- Colors and visualizations
- Add or remove panels
- Modify queries and thresholds

## Metrics Endpoint

Ensure your Split Bot server is exposing metrics at `/metrics` endpoint and
Prometheus is configured to scrape it. The endpoint is automatically provided by
`prometheus-fastapi-instrumentator`.

Example Prometheus scrape config:

```yaml
scrape_configs:
    - job_name: "split-bot"
      scrape_interval: 15s
      static_configs:
          - targets: ["localhost:8000"]
```

## Troubleshooting

### Push Script Issues

- **401 Unauthorized**: Check that your API key is valid and has admin
  permissions
- **404 Not Found**: Verify the Grafana URL is correct and accessible
- **Dashboard file not found**: Ensure you're running the script from the
  correct directory or set `GRAFANA_DASHBOARD_PATH`
- **Folder not found**: If using `GRAFANA_FOLDER_NAME`, ensure you have
  permissions to create folders, or use `GRAFANA_FOLDER_ID` instead
