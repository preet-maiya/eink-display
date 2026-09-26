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

4. Flash the firmware:
   - Copy `firmware/secrets.h.example` to `firmware/secrets.h` and fill in your Wi-Fi details, the Function App host and `DEVICE_TOKEN`.
   - Open `firmware/firmware.ino` in the Arduino IDE. Select board **ESP32S3 Dev Module**, then upload.

## Everyday commands

| Command | What it does |
|---|---|
| `make deploy-functions` | Redeploy the Function App only |
| `make deploy-web` | Redeploy the settings site only |
| `make deploy` | Redeploy both |
| `make outputs` | Show resource names / hostnames |
| `make validate` | Compile-check the Bicep template |
| `make help` | List all commands |
| `make destroy` | Delete the whole resource group (irreversible) |
