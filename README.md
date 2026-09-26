# Fridge E-Ink Dashboard

A kitchen dashboard on a 7.5" e-paper display (reTerminal E1001, ESP32-S3). It shows calendar, weather, commute, meal plan and portfolio widgets.

- **`function-app/`**: Azure Functions (Python) app that renders the dashboard as an image and serves it to the device.
- **`settings-site/`**: Azure Static Web App for changing dashboard settings. Sign-in uses Microsoft Entra ID.
- **`firmware/`**: ESP32-S3 sketch. It wakes up, downloads the rendered image and draws it on the e-paper.
- **`infra/`**: Bicep templates for all Azure resources.

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) with Bicep (`az bicep install`)
- [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
- Python 3, Node.js (for `npx`)
- Arduino IDE with the **GxEPD2** and **Adafruit GFX** libraries

## Setup

1. Set environment variables. Put them in `.env`, which is gitignored:

   ```bash
   export GOOGLE_CALENDAR_ICS_URL="https://calendar.google.com/.../basic.ics"
   export DEVICE_TOKEN=$(openssl rand -hex 32)
   ```

   ```bash
   source .env
   ```

2. Download the fonts:

   ```bash
   python3 scripts/download_fonts.py
   ```

3. Create the Azure resources and deploy:

   ```bash
   make login        # sign in to Azure
   make infra        # create resource group + resources
   make auth-setup   # register Entra app for settings-site login
   make deploy       # publish Function App + settings site
   ```

4. (Optional) Let GitHub Actions deploy on push to `main` — see [CI deploys](#ci-deploys).

5. Flash the firmware:
   - Copy `firmware/secrets.h.example` to `firmware/secrets.h` and fill in your Wi-Fi details, the Function App host and `DEVICE_TOKEN`.
   - Open `firmware/firmware.ino` in the Arduino IDE. Install the **esp32 by Espressif Systems** board package, then select board **XIAO_ESP32S3**, PSRAM **OPI PSRAM**, USB CDC On Boot **Disabled**, port `/dev/cu.usbserial-*`, and upload.

## CI deploys

`.github/workflows/deploy-functions.yml` publishes `function-app/` when it changes on `main`;
`.github/workflows/deploy-web.yml` does the same for `settings-site/`. Both can also be run
manually from the Actions tab. Azure auth uses OIDC (no stored Azure password).

One-time setup, after `make infra`:

```bash
brew install gh && gh auth login   # optional, lets the script set secrets for you
make gh-setup
```

This creates the `fridgedash-gh-deploy` service principal (Website Contributor on the
Function App only), trusts the repo's `production` environment, and sets the
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `SWA_DEPLOYMENT_TOKEN`
secrets and `AZURE_FUNCTIONAPP_NAME` variable. Without `gh` it prints what to add by hand.

## Everyday commands

| Command | What it does |
|---|---|
| `make deploy-functions` | Redeploy the Function App only |
| `make deploy-web` | Redeploy the settings site only |
| `make deploy` | Redeploy both |
| `make gh-setup` | One-time GitHub Actions deploy setup |
| `make fw-setup` | Install the esp32 core + firmware libraries (once) |
| `make fw-flash` | Compile + upload the firmware to the device |
| `make fw-monitor` | Open the serial monitor (115200) |
| `make outputs` | Show resource names / hostnames |
| `make validate` | Compile-check the Bicep template |
| `make help` | List all commands |
| `make destroy` | Delete the whole resource group (irreversible) |
