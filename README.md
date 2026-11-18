# Catalog indexing alert

Python 3.12 service that evaluates the latest and previous scheduled indexing runs for each CSV site. Each CronWorkflow execution checks the status endpoint concurrently, treats absence after the grace deadline as `missing`, and delivers alerts through Slack and/or SMTP.

## Local use

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
CSV_SOURCE=sites.example.csv CSV_SOURCE_TYPE=local STATE_BACKEND=file DRY_RUN=true python -m checker.main
```

Configure at least one notification destination and set `DRY_RUN=false` for delivery. Local JSON state only deduplicates when the same state file persists between executions.

## Tests

```sh
pytest -q
```

## Build and deploy

```sh
docker build -t your-registry/catalog-indexing-alert:latest .
docker push your-registry/catalog-indexing-alert:latest
kubectl apply -f k8s/serviceaccount.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.example.yaml
kubectl apply -f k8s/cronworkflow.yaml
```

Before applying, replace the image in `k8s/cronworkflow.yaml`, and populate the notification Secret through your cluster's secret management process. `k8s/configmap.yaml` includes sample `sites.csv` and `config.yaml` entries; update the CSV data for your sites. Set `CSV_SOURCE_TYPE=local` and `CSV_SOURCE=/config/sites.csv`.

## Configuration

`CONFIG_YAML` may point to YAML containing `region_hosts` and `region_webhooks`. The default mapping is `apac: feed-apac.unbxd.io`. Add other region mappings as required. Status timestamp behavior defaults to `finish`, as the supplied API description does not establish whether timestamps are start or finish moments.

| Environment variable | Default | Purpose |
|---|---|---|
| `CSV_SOURCE` | `sites.example.csv` | Local path, object storage URL, or Google Sheet ID/URL |
| `CSV_SOURCE_TYPE` | `local` | `local`, `s3`, `gcs`, `object_storage`, or `sheets` |
| `CONFIG_YAML` | unset | Optional YAML config for host and webhook maps |
| `BUFFER_MINUTES` | `15` | Grace period after max duration |
| `MAX_WORKERS` | `10` | Concurrent site checks |
| `REQUEST_TIMEOUT_SECONDS` | `15` | Timeout per HTTP attempt |
| `TIMESTAMP_SEMANTICS` | `finish` | `finish` or `start` interpretation of `timeStamp` |
| `DRY_RUN` | `false` | Log alerts without sending or storing |
| `STATE_BACKEND` | `kubernetes` | `kubernetes` or `file` |
| `STATE_FILE` | `/tmp/indexing-alert-state.json` | Local state JSON file |
| `STATE_CONFIGMAP` | `indexing-alert-state` | Kubernetes state ConfigMap |
| `POD_NAMESPACE` / `NAMESPACE` | `default` | Namespace containing state ConfigMap |
| `REMINDER_HOURS` | unset | Reminder interval; optional |
| `SLACK_WEBHOOK_URL` | unset | Default Slack incoming webhook |
| `SMTP_HOST` | unset | SMTP host; enables email when recipients are configured |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | unset | SMTP login user |
| `SMTP_PASSWORD` | unset | SMTP login password |
| `EMAIL_FROM` | unset | Email sender address |
| `EMAIL_RECIPIENTS` | unset | Comma-separated recipient list |

Cloud loaders are optional: install `boto3` for S3, `google-cloud-storage` for GCS, or `gspread` and configure its service account for Google Sheets. These packages are intentionally not in the base image.

## Assumptions and items to confirm

- The default terminal failure statuses are `FAILED` and `ERROR`; `REJECTED` is distinct; `INDEXING`, `PROCESSING`, and `IN_PROGRESS` mean active. A non-200 code for any non-success/non-rejected status is treated as failed. Adjust the sets in `checker/evaluator.py` to match the platform's actual vocabulary.
- `timeStamp` defaults to run finish time; set `TIMESTAMP_SEMANTICS=start` if the platform confirms it means start time. With finish semantics, the scheduled time to next scheduled time boundary defines attribution as requested.
- S3/GCS and Sheets loaders are implemented behind optional SDK imports; their dependencies and credentials must be provisioned separately.
- Kubernetes uses `kubectl` in the pod for ConfigMap compare-and-swap updates, which requires the RBAC permissions shown. State writes occur after successful notification.
- `REMINDER_HOURS` is listed as a setting; reminder scheduling requires persistent per-alert reminder timestamps and should be enabled only after that policy is finalized.
- Please confirm the other region-to-host mappings, precise status vocabulary and `timeStamp` meaning before production rollout.
