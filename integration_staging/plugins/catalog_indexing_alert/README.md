# Catalog indexing alert plugin

This shared plugin checks scheduled catalog-indexing runs. The Argo `CronWorkflow` runs the `WorkflowTemplate` every 30 minutes. Update the `catalog-indexing-alert-config` ConfigMap with your `sites.csv` and region host map, and create the `catalog-indexing-alert` Secret with Slack and/or SMTP values. Apply the service account and role in `k8s/` once in the Argo namespace.

The plugin code is added to the existing `solution-jobs-v3` image by the shared image build. The workflow template sets `PYTHONPATH=/home/catalog_indexing_alert` and invokes `python -m checker.main`.

## Deployment

The repository plugin automation deploys workflow templates. CronWorkflow deployment and cleanup also need to be enabled in `.github/workflows/plugin_image_push.yml` for files under `argo/cron_workflow/`. Core Kubernetes resources (ConfigMap, Secret, ServiceAccount and Role) are applied through the cluster's existing configuration-management mechanism.

Confirm actual region hosts, status vocabulary, timestamp semantics, and notification routing before enabling production alerts. `TIMESTAMP_SEMANTICS=finish` is the current default assumption.
