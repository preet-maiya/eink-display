#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy to Azure without stored Azure passwords.
#
#   1. Creates an Entra app + service principal ("fridgedash-gh-deploy")
#   2. Grants it Website Contributor on the prod + PR staging Function Apps, and
#      Storage Table Data Contributor (PR workflow snapshots/deletes pr<N>* tables)
#   3. Adds federated credentials trusting this repo's "production" and "staging" environments
#   4. Stores IDs + SWA deployment token as GitHub secrets/variables (needs `gh`),
#      plus a staging-only device token (generated once) on the "staging" environment
#
# Run `make infra-staging` first so the staging apps exist.
#
# Safe to re-run: existing app, role assignment and credential are reused.
# Usage: scripts/setup-gh-oidc.sh [resource-group]   (default: fridgedash-rg)
set -euo pipefail

RG="${1:-fridgedash-rg}"
REPO="${GITHUB_REPO:-preet-maiya/eink-display}"
APP_NAME="fridgedash-gh-deploy"
ENV_NAMES=(production staging)

echo "==> Reading Azure context"
SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
FUNC_APP=$(az deployment group show -g "$RG" -n main --query properties.outputs.functionAppName.value -o tsv)
SWA_NAME=$(az deployment group show -g "$RG" -n main --query properties.outputs.staticWebAppName.value -o tsv)
FUNC_ID=$(az functionapp show -g "$RG" -n "$FUNC_APP" --query id -o tsv)
STORAGE=$(az deployment group show -g "$RG" -n main --query properties.outputs.storageAccountName.value -o tsv)
STORAGE_ID=$(az storage account show -g "$RG" -n "$STORAGE" --query id -o tsv)
STAGING_APPS=$(az deployment group show -g "$RG" -n staging --query "properties.outputs.stagingAppNames.value" -o tsv | tr '\n' ' ' | xargs)
[[ -n "$STAGING_APPS" ]] || { echo "No staging apps found. Run: make infra-staging"; exit 1; }
echo "    subscription: $SUB_ID"
echo "    function app: $FUNC_APP"
echo "    static web app: $SWA_NAME"
echo "    staging apps: $STAGING_APPS"

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

assign_role() {  # <role> <scope>
  local existing
  existing=$(az role assignment list --assignee "$SP_ID" --scope "$2" --role "$1" --query "[0].id" -o tsv)
  [[ -n "$existing" ]] && return
  # New service principals can take a few seconds to replicate
  for i in 1 2 3 4 5; do
    az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
      --role "$1" --scope "$2" -o none && return
    echo "    retrying in 10s..."; sleep 10
  done
  exit 1
}

echo "==> Role assignments"
assign_role "Website Contributor" "$FUNC_ID"
for app in $STAGING_APPS; do
  assign_role "Website Contributor" "$(az functionapp show -g "$RG" -n "$app" --query id -o tsv)"
done
assign_role "Storage Table Data Contributor" "$STORAGE_ID"

# GitHub's OIDC subject embeds immutable IDs: repo:owner@<owner_id>/repo@<repo_id>:...
REPO_IDS=$(gh api "repos/$REPO" --jq '"\(.owner.login)@\(.owner.id)/\(.name)@\(.id)"')
for ENV_NAME in "${ENV_NAMES[@]}"; do
  SUBJECT="repo:$REPO_IDS:environment:$ENV_NAME"
  echo "==> Federated credential ($SUBJECT)"
  HAS_CRED=$(az ad app federated-credential list --id "$CLIENT_ID" \
    --query "[?subject=='$SUBJECT'].name | [0]" -o tsv)
  if [[ -z "$HAS_CRED" ]]; then
    az ad app federated-credential create --id "$CLIENT_ID" -o none --parameters "{
      \"name\": \"github-$ENV_NAME-ids\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"$SUBJECT\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }"
  fi
done

echo "==> GitHub secrets/variables"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  for ENV_NAME in "${ENV_NAMES[@]}"; do
    gh api -X PUT "repos/$REPO/environments/$ENV_NAME" >/dev/null
  done
  gh secret set AZURE_CLIENT_ID       -R "$REPO" -b "$CLIENT_ID"
  gh secret set AZURE_TENANT_ID       -R "$REPO" -b "$TENANT_ID"
  gh secret set AZURE_SUBSCRIPTION_ID -R "$REPO" -b "$SUB_ID"
  gh variable set AZURE_FUNCTIONAPP_NAME -R "$REPO" -b "$FUNC_APP"
  gh variable set AZURE_RESOURCE_GROUP   -R "$REPO" -b "$RG"
  gh variable set STORAGE_ACCOUNT_NAME   -R "$REPO" -b "$STORAGE"
  gh variable set STAGING_FUNCTIONAPP_NAMES -R "$REPO" -b "$STAGING_APPS"
  # Separate from the prod device token. Kept locally (gitignored) so you can paste
  # it into PR preview sites: `make staging-token`
  TOKEN_FILE="$(dirname "$0")/../.staging-device-token"
  if [[ ! -s "$TOKEN_FILE" ]]; then
    (umask 077; openssl rand -hex 32 > "$TOKEN_FILE")
    gh secret set STAGING_DEVICE_TOKEN -R "$REPO" -e staging < "$TOKEN_FILE"
  fi
  # Token piped straight to gh, never echoed
  az staticwebapp secrets list -g "$RG" -n "$SWA_NAME" --query properties.apiKey -o tsv \
    | gh secret set SWA_DEPLOYMENT_TOKEN -R "$REPO"
  echo "    done"
else
  cat <<MSG
    gh CLI not installed/authenticated. Add these in GitHub → Settings:
      Environments → New environments: ${ENV_NAMES[*]}
        staging → secret STAGING_DEVICE_TOKEN = (openssl rand -hex 32 > .staging-device-token)
      Secrets → Actions:
        AZURE_CLIENT_ID       = $CLIENT_ID
        AZURE_TENANT_ID       = $TENANT_ID
        AZURE_SUBSCRIPTION_ID = $SUB_ID
        SWA_DEPLOYMENT_TOKEN  = (output of: make swa-token)
      Variables → Actions:
        AZURE_FUNCTIONAPP_NAME    = $FUNC_APP
        AZURE_RESOURCE_GROUP      = $RG
        STORAGE_ACCOUNT_NAME      = $STORAGE
        STAGING_FUNCTIONAPP_NAMES = $STAGING_APPS
MSG
fi

echo "==> Setup complete. Trigger a deploy from the Actions tab or push to main."
