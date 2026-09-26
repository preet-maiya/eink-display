#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy to Azure without stored Azure passwords.
#
#   1. Creates an Entra app + service principal ("fridgedash-gh-deploy")
#   2. Grants it Website Contributor on the Function App only
#   3. Adds a federated credential trusting this repo's "production" environment
#   4. Stores IDs + SWA deployment token as GitHub secrets/variables (needs `gh`)
#
# Safe to re-run: existing app, role assignment and credential are reused.
# Usage: scripts/setup-gh-oidc.sh [resource-group]   (default: fridgedash-rg)
set -euo pipefail

RG="${1:-fridgedash-rg}"
REPO="${GITHUB_REPO:-preet-maiya/eink-display}"
APP_NAME="fridgedash-gh-deploy"
ENV_NAME="production"

echo "==> Reading Azure context"
SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
FUNC_APP=$(az deployment group show -g "$RG" -n main --query properties.outputs.functionAppName.value -o tsv)
SWA_NAME=$(az deployment group show -g "$RG" -n main --query properties.outputs.staticWebAppName.value -o tsv)
FUNC_ID=$(az functionapp show -g "$RG" -n "$FUNC_APP" --query id -o tsv)
echo "    subscription: $SUB_ID"
echo "    function app: $FUNC_APP"
echo "    static web app: $SWA_NAME"

echo "==> Entra app registration"
CLIENT_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [[ -z "$CLIENT_ID" ]]; then
  CLIENT_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
  echo "    created $CLIENT_ID"
else
  echo "    reusing $CLIENT_ID"
fi

echo "==> Service principal"
SP_ID=$(az ad sp show --id "$CLIENT_ID" --query id -o tsv 2>/dev/null || true)
if [[ -z "$SP_ID" ]]; then
  SP_ID=$(az ad sp create --id "$CLIENT_ID" --query id -o tsv)
fi

echo "==> Role assignment (Website Contributor on $FUNC_APP)"
EXISTING=$(az role assignment list --assignee "$SP_ID" --scope "$FUNC_ID" \
  --role "Website Contributor" --query "[0].id" -o tsv)
if [[ -z "$EXISTING" ]]; then
  # New service principals can take a few seconds to replicate
  for i in 1 2 3 4 5; do
    az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
      --role "Website Contributor" --scope "$FUNC_ID" -o none && break
    echo "    retrying in 10s..."; sleep 10
  done
fi

echo "==> Federated credential (repo:$REPO:environment:$ENV_NAME)"
SUBJECT="repo:$REPO:environment:$ENV_NAME"
HAS_CRED=$(az ad app federated-credential list --id "$CLIENT_ID" \
  --query "[?subject=='$SUBJECT'].name | [0]" -o tsv)
if [[ -z "$HAS_CRED" ]]; then
  az ad app federated-credential create --id "$CLIENT_ID" -o none --parameters "{
    \"name\": \"github-$ENV_NAME\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }"
fi

echo "==> GitHub secrets/variables"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh api -X PUT "repos/$REPO/environments/$ENV_NAME" >/dev/null
  gh secret set AZURE_CLIENT_ID       -R "$REPO" -b "$CLIENT_ID"
  gh secret set AZURE_TENANT_ID       -R "$REPO" -b "$TENANT_ID"
  gh secret set AZURE_SUBSCRIPTION_ID -R "$REPO" -b "$SUB_ID"
  gh variable set AZURE_FUNCTIONAPP_NAME -R "$REPO" -b "$FUNC_APP"
  # Token piped straight to gh, never echoed
  az staticwebapp secrets list -g "$RG" -n "$SWA_NAME" --query properties.apiKey -o tsv \
    | gh secret set SWA_DEPLOYMENT_TOKEN -R "$REPO"
  echo "    done"
else
  cat <<MSG
    gh CLI not installed/authenticated. Add these in GitHub → Settings:
      Environments → New environment: $ENV_NAME
      Secrets → Actions:
        AZURE_CLIENT_ID       = $CLIENT_ID
        AZURE_TENANT_ID       = $TENANT_ID
        AZURE_SUBSCRIPTION_ID = $SUB_ID
        SWA_DEPLOYMENT_TOKEN  = (output of: make swa-token)
      Variables → Actions:
        AZURE_FUNCTIONAPP_NAME = $FUNC_APP
MSG
fi

echo "==> Setup complete. Trigger a deploy from the Actions tab or push to main."
