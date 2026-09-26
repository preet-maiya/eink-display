RESOURCE_GROUP ?= fridgedash-rg
LOCATION       ?= eastus
PREFIX         ?= fridgedash
FUNCTIONS_DIR  ?= ./function-app
WEB_DIR        ?= ./settings-site
SWA_NAME       := fridgedash-swa-4jak7dmb55jui
SWA_URL        := https://purple-dune-050a09e0f.5.azurestaticapps.net

.PHONY: help login rg validate infra outputs \
        auth-setup auth-show \
        deploy-functions deploy-web deploy \
        destroy

# ─────────────────────────────────────────────────────────────────────────────
# HELP
# ─────────────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  FIRST-TIME SETUP (run in order)"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  make login             Sign in to Azure CLI"
	@echo "  make infra             Create resource group + all Azure resources"
	@echo "  make auth-setup        Register Entra app + wire AAD login to SWA"
	@echo "  make deploy            Deploy Function App code + settings site"
	@echo ""
	@echo "  DEPLOY / REDEPLOY"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  make deploy-functions  Publish Function App code only"
	@echo "  make deploy-web        Publish settings site only"
	@echo "  make deploy            Both of the above"
	@echo ""
	@echo "  DIAGNOSTICS"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  make outputs           Print resource names / hostnames from infra"
	@echo "  make auth-show         Print current SWA auth app settings"
	@echo "  make validate          Compile-check Bicep template locally"
	@echo ""
	@echo "  TEAR DOWN"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  make destroy           Delete ENTIRE resource group (irreversible)"
	@echo ""
	@echo "  ENV VARS REQUIRED BEFORE 'make infra'"
	@echo "    export GOOGLE_CALENDAR_ICS_URL=..."
	@echo "    export DEVICE_TOKEN=\$$(openssl rand -hex 32)"
	@echo ""

# ─────────────────────────────────────────────────────────────────────────────
# FIRST-TIME SETUP
# ─────────────────────────────────────────────────────────────────────────────

login:
	az login

rg:
	az group create --name $(RESOURCE_GROUP) --location $(LOCATION)

validate:
	bicep build infra/main.bicep --outfile /tmp/main.json

infra: rg
	az deployment group create \
		--name main \
		--resource-group $(RESOURCE_GROUP) \
		--template-file infra/main.bicep \
		--parameters infra/main.bicepparam \
		--parameters prefix=$(PREFIX) location=$(LOCATION) \
		--query properties.outputs

# Register an Entra (AAD) app and configure SWA to use it for login.
# Run once after 'make infra'. Requires no arguments — reads tenant + SWA name automatically.
auth-setup:
	$(eval TENANT_ID := $(shell az account show --query tenantId -o tsv))
	$(eval APP_ID    := $(shell az ad app create \
		--display-name "Fridge Dashboard" \
		--web-redirect-uris "$(SWA_URL)/.auth/login/aad/callback" \
		--sign-in-audience AzureADandPersonalMicrosoftAccount \
		--query appId -o tsv))
	$(eval SECRET    := $(shell az ad app credential reset \
		--id $(APP_ID) \
		--display-name "fridgedash-swa" \
		--years 2 \
		--query password -o tsv))
	az staticwebapp appsettings set \
		--name $(SWA_NAME) \
		--resource-group $(RESOURCE_GROUP) \
		--setting-names \
			AAD_CLIENT_ID=$(APP_ID) \
			AAD_CLIENT_SECRET=$(SECRET) \
			AAD_TENANT_ID=$(TENANT_ID)
	@echo ""
	@echo "Auth configured. APP_ID=$(APP_ID)  TENANT=$(TENANT_ID)"
	@echo "Now update staticwebapp.config.json with tenant ID and run: make deploy-web"
	@echo "TENANT_ID=$(TENANT_ID)"

# ─────────────────────────────────────────────────────────────────────────────
# DEPLOY
# ─────────────────────────────────────────────────────────────────────────────

deploy-functions:
	$(eval FUNC_APP := $(shell az deployment group show -g $(RESOURCE_GROUP) -n main --query properties.outputs.functionAppName.value -o tsv))
	cd $(FUNCTIONS_DIR) && func azure functionapp publish $(FUNC_APP) --python

deploy-web:
	$(eval SWA_TOKEN := $(shell az staticwebapp secrets list -g $(RESOURCE_GROUP) -n $(SWA_NAME) --query properties.apiKey -o tsv))
	npx --yes @azure/static-web-apps-cli deploy $(WEB_DIR) --deployment-token $(SWA_TOKEN) --env production

deploy: deploy-functions deploy-web

# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSTICS
# ─────────────────────────────────────────────────────────────────────────────

outputs:
	az deployment group show \
		--resource-group $(RESOURCE_GROUP) \
		--name main \
		--query properties.outputs

auth-show:
	az staticwebapp appsettings list \
		--name $(SWA_NAME) \
		--resource-group $(RESOURCE_GROUP) \
		--query "[?contains(name,'AAD')]"

# ─────────────────────────────────────────────────────────────────────────────
# TEAR DOWN
# ─────────────────────────────────────────────────────────────────────────────

destroy:
	@echo "WARNING: This deletes $(RESOURCE_GROUP) and everything in it."
	@read -p "Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ]
	az group delete --name $(RESOURCE_GROUP) --yes --no-wait
