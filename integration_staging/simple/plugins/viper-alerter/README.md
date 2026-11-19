# Viper alerter

This is a self-contained Argo CronWorkflow service. Build and publish its image to your container registry; Argo pulls the image using the configured workflow image reference. No changes to the repository's shared Dockerfile or GitHub Actions workflows are needed.

The default region map is `us: feed.unbxd.io`, `us-gcp: feed-g-nam.unbxd.io`, `au: feed-anz.unbxd.io`, `uk: feed-uk.unbxd.io`, `sg: feed-apac.unbxd.io`, `anz-gcp` and `au-gcp: feed-g-anz.unbxd.io`, `uk-gcp: feed-g-eu.unbxd.io`, and `sg-gcp: feed-g-apac.unbxd.io`.

## Build and publish

From this folder:

```sh
docker build -t registry.example.com/unbxd/viper-alerter:1.0.0 .
docker push registry.example.com/unbxd/viper-alerter:1.0.0
```

Set the same image reference in `argo/cron_workflow/viper-alerter.yaml` (and the default in the WorkflowTemplate). If the registry is private, configure image pull credentials in the Argo namespace according to the cluster's existing convention.

## Deploy

Create or update `viper-alerter-config` from your sites CSV and region map, create the notification Secret, then apply state access and Argo resources:

```sh
kubectl apply -f k8s/state-rbac.yaml
kubectl apply -f k8s/configmap.example.yaml
kubectl apply -f k8s/secret.example.yaml
kubectl apply -f argo/workflow_template/viper-alerter.yaml
kubectl apply -f argo/cron_workflow/viper-alerter.yaml
```

Replace the example ConfigMap and Secret values before applying. The current evaluator assumes the API timestamp is a run finish time; confirm the platform's timestamp meaning, status vocabulary, and all region host mappings before production use.
