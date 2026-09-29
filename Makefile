RESOURCE_GROUP ?= fridgedash-rg
LOCATION       ?= eastus
PREFIX         ?= fridgedash
FUNCTIONS_DIR  ?= ./function-app
WEB_DIR        ?= ./settings-site
SWA_NAME       := fridgedash-swa-4jak7dmb55jui
SWA_URL        := https://purple-dune-050a09e0f.5.azurestaticapps.net

# Firmware (arduino-cli). Falls back to the copy bundled with Arduino IDE.
FW_DIR         ?= ./firmware
FW_BUILD       ?= $(FW_DIR)/build
ARDUINO_CLI    ?= $(shell command -v arduino-cli 2>/dev/null || echo "/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli")
ESP32_INDEX    := https://espressif.github.io/arduino-esp32/package_esp32_index.json
# 921600 is too fast for the E1001's USB-UART bridge; 230400 also works if 115200 feels slow
UPLOAD_BAUD    ?= 115200
FQBN           := esp32:esp32:XIAO_ESP32S3:PSRAM=opi,CDCOnBoot=cdc,UploadSpeed=$(UPLOAD_BAUD)
PORT           ?= $(firstword $(wildcard /dev/cu.usbserial-*))
CLI            := "$(ARDUINO_CLI)" --additional-urls $(ESP32_INDEX)

.PHONY: help login rg validate infra infra-staging staging-token outputs \
        auth-setup auth-show \
        deploy-functions deploy-web deploy gh-setup swa-token \
        fw-setup fw-build fw-flash fw-monitor fw-ports \
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
	@echo "  make gh-setup          One-time: let GitHub Actions deploy (OIDC + secrets)"
	@echo ""
	@echo "  PR STAGING (each open PR gets its own Function App + table snapshot)"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  make infra-staging     Create the staging app pool, then re-run gh-setup"
	@echo "  make staging-token     Print the device token for PR preview sites"
	@echo "  make swa-token         Print SWA deployment token (for manual secret setup)"
	@echo ""
	@echo "  FIRMWARE (reTerminal E1001 via arduino-cli)"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  make fw-setup          Install esp32 core + GxEPD2 / Adafruit GFX (once)"
	@echo "  make fw-build          Compile firmware/firmware.ino"
	@echo "  make fw-flash          Compile + upload (PORT=/dev/cu.usbserial-* auto)"
	@echo "  make fw-monitor        Serial monitor at 115200"
	@echo "  make fw-ports          List connected boards / serial ports"
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

# Pool of PR staging Function Apps on the same B1 plan. Takes no secrets, so it
# never touches prod. Re-running resets claimed apps, so do it with no PRs open.
infra-staging:
	az deployment group create \
		--name staging \
		--resource-group $(RESOURCE_GROUP) \
		--template-file infra/staging.bicep \
		--parameters prefix=$(PREFIX) \
			location="$$(az appservice plan list -g $(RESOURCE_GROUP) --query '[0].location' -o tsv)" \
		--query properties.outputs

staging-token:
	@cat .staging-device-token

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

# One-time: service principal + federated credential + GitHub secrets for .github/workflows
gh-setup:
	./scripts/setup-gh-oidc.sh $(RESOURCE_GROUP)

swa-token:
	@az staticwebapp secrets list -g $(RESOURCE_GROUP) -n $(SWA_NAME) --query properties.apiKey -o tsv

# ─────────────────────────────────────────────────────────────────────────────
# FIRMWARE
# ─────────────────────────────────────────────────────────────────────────────

fw-setup:
	$(CLI) core update-index
	$(CLI) core install esp32:esp32
	$(CLI) lib install GxEPD2 "Adafruit GFX Library"

fw-build:
	@test -f $(FW_DIR)/secrets.h || { echo "Missing $(FW_DIR)/secrets.h — copy secrets.h.example and fill it in"; exit 1; }
	$(CLI) compile --fqbn $(FQBN) --build-path $(FW_BUILD) $(FW_DIR)

fw-flash: fw-build
	@test -n "$(PORT)" || { echo "No /dev/cu.usbserial-* port found. Plug in the device (press a button to wake it) or pass PORT=..."; exit 1; }
	@! lsof $(PORT) >/dev/null 2>&1 || { echo "$(PORT) is in use (close the serial monitor first):"; lsof $(PORT); exit 1; }
	$(CLI) upload --fqbn $(FQBN) --input-dir $(FW_BUILD) -p $(PORT) $(FW_DIR)

fw-monitor:
	@test -n "$(PORT)" || { echo "No /dev/cu.usbserial-* port found. Pass PORT=..."; exit 1; }
	$(CLI) monitor -p $(PORT) -c baudrate=115200

fw-ports:
	$(CLI) board list

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
