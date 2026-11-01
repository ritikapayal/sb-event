# Azure Service Bus queue consumer

A small Python worker that receives messages from an Azure Service Bus **queue** and runs in Docker. It parses Event Grid JSON bodies and logs event ID, topic, subject, event type, event time, blob URL, content type, content length, and blob type. It also checks Service Bus application properties as fallbacks. It does not log the full message body. If processing raises a Service Bus error, it attempts to abandon the message so it can be delivered again.

## Configure

1. Copy `.env.example` to `.env`.
2. Set `SERVICE_BUS_CONNECTION_STRING` to a connection string with **Listen** permission and `SERVICE_BUS_QUEUE_NAME` to the queue to consume.
3. Keep `.env` private. It is excluded from the Docker build context; do not commit real credentials or bake them into the image.

## Run with Docker Compose

```sh
docker compose up --build
```

Stop the worker with `Ctrl+C`. Compose restarts the container after an unexpected exit.

## Automated deployment to Azure Kubernetes Service (AKS)

The GitHub Actions workflow at `.github/workflows/deploy-aks.yml` builds and pushes a Linux image to ACR and deploys it to AKS when code is pushed to `main`. You can also start it manually from the repository's **Actions** tab using **Run workflow**. Each build uses the commit SHA as its image tag.

One-time setup:

1. Create a GitHub Actions OIDC federated credential for an Azure app registration. Set the issuer to `https://token.actions.githubusercontent.com`, audience to `api://AzureADTokenExchange`, and subject to `repo:OWNER/REPOSITORY:ref:refs/heads/main` (replace the owner and repository). This lets the workflow sign in without a stored Azure client secret.
2. Grant that Azure identity `AcrPush` on the ACR, `Azure Kubernetes Service Cluster User Role` on the AKS resource, and Kubernetes permissions to create/update the `sb-event` namespace's Secret and Deployment. Create the namespace once with an administrator account:

   ```sh
   kubectl create namespace sb-event
   ```

   The AKS cluster identity must also be allowed to pull from ACR; attach the registry once if needed:

   ```sh
   az aks update --resource-group YOUR_RESOURCE_GROUP --name YOUR_AKS_CLUSTER --attach-acr YOUR_ACR_NAME
   ```

3. In the GitHub repository, open **Settings → Secrets and variables → Actions**. Add these **repository variables**:
   - `AZURE_CLIENT_ID` — application (client) ID of the federated Azure identity
   - `AZURE_TENANT_ID` — Microsoft Entra tenant ID
   - `AZURE_SUBSCRIPTION_ID` — subscription ID
   - `ACR_NAME` — registry name without `.azurecr.io`
   - `AZURE_RESOURCE_GROUP` — AKS resource group
   - `AKS_CLUSTER_NAME` — AKS cluster name
   - `SERVICE_BUS_QUEUE_NAME` — queue to consume
   - `LOG_LEVEL` — optional; defaults to `INFO`
4. Add `SERVICE_BUS_CONNECTION_STRING` as a **repository secret**. Use a Service Bus connection string with Listen permission. Do not put credentials in the workflow, Kubernetes manifest, or source code.
5. Push the workflow and app to the repository's `main` branch, then follow the run under **Actions**. The workflow creates/updates the Kubernetes Secret and waits for the deployment rollout.

The Service Bus connection string is passed from GitHub Actions Secrets to a Kubernetes Secret at deploy time; it is not included in the container image or committed files. Restrict access to repository and cluster secrets, and enable AKS secret encryption at rest or use Azure Key Vault for production.

## Manual deployment to Azure Kubernetes Service (AKS)

Prerequisites: Azure CLI (`az`), Podman, `kubectl`, an Azure Container Registry (ACR), and an AKS cluster. The signed-in Azure identity needs permission to push to ACR and deploy to the cluster. Set the resource names below to your existing resources:

```sh
az login
ACR_NAME=your-acr-name
RESOURCE_GROUP=your-resource-group
AKS_NAME=your-aks-cluster
```

Replace the example values with your resource names. `ACR_NAME` is the registry name without `.azurecr.io`.

Build and push the image with Podman:

```sh
IMAGE="$ACR_NAME.azurecr.io/sb-event:1.0.0"
az acr login --name "$ACR_NAME" --expose-token --output tsv --query accessToken \
  | podman login "$ACR_NAME.azurecr.io" \
      --username 00000000-0000-0000-0000-000000000000 \
      --password-stdin
podman build --tag "$IMAGE" .
podman push "$IMAGE"
```

Allow the AKS cluster to pull images from the registry and configure `kubectl`:

```sh
az aks update --resource-group "$RESOURCE_GROUP" --name "$AKS_NAME" --attach-acr "$ACR_NAME"
az aks get-credentials --resource-group "$RESOURCE_GROUP" --name "$AKS_NAME"
kubectl config current-context
```

Create a namespace and load settings into a Kubernetes Secret. First make a separate Kubernetes env file from `k8s/secret.env.example` at `k8s/secret.env`, and enter the same values as `.env` without quotes. This keeps the local shell `.env` format separate from Kubernetes' env-file format. The commands below do not print secret values:

```sh
cp k8s/secret.env.example k8s/secret.env
# Edit k8s/secret.env with the values from .env, without wrapping the connection string in quotes.
kubectl create namespace sb-event --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic service-bus-consumer \
  --namespace sb-event \
  --from-env-file=k8s/secret.env \
  --dry-run=client -o yaml \
  | kubectl apply -f -
```

Kubernetes Secrets are not encrypted just because their values are base64 encoded. Restrict access to the namespace and cluster, enable AKS secret encryption at rest, and consider Azure Key Vault integration for production. Do not commit `.env`, `k8s/secret.env`, or a generated Secret manifest.

Edit `k8s/deployment.yaml` and replace `REPLACE_WITH_ACR_NAME` in the image name with your ACR name. Then deploy and check the worker:

```sh
kubectl apply --namespace sb-event -f k8s/deployment.yaml
kubectl rollout status --namespace sb-event deployment/service-bus-consumer
kubectl logs --namespace sb-event deployment/service-bus-consumer --follow
```

To deploy a new image, build and push it with a new tag, update the image in `k8s/deployment.yaml`, then apply the manifest again. The worker is a queue consumer and does not need a Kubernetes Service or Ingress.

## Run locally

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a && . ./.env && set +a
python -m app.consumer
```

This starter consumes a queue. For a topic, use a subscription receiver instead. Replace the sample logging in `app/consumer.py` with application-specific message processing before completing messages; avoid logging message bodies if they may contain sensitive data.

## Debug in VS Code

1. Install Microsoft's **Python** and **Python Debugger** extensions.
2. Open this project folder in VS Code and select `.venv/bin/python` with **Python: Select Interpreter**.
3. Open `app/consumer.py` and click beside a line number to set a breakpoint.
4. Open **Run and Debug**, choose **Debug Service Bus consumer**, and press **F5**.

The launch configuration loads environment variables from the local `.env` file. Make sure it contains valid Service Bus settings before starting the debugger.
