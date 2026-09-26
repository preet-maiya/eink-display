/*
 * Fridge e-ink dashboard — reTerminal E1001 (ESP32-S3, 7.5" 800×480 e-paper)
 *
 * Required libraries (install via Arduino Library Manager):
 *   - GxEPD2 by ZinggJM  (tested ≥ 1.5.x)
 *   - Adafruit GFX Library
 *
 * Verify / adjust pins at the top of this file before flashing.
 * Rename secrets.h.example → secrets.h and fill in your values.
 *
 * Board: "ESP32S3 Dev Module" (or Seeed reTerminal E1001 if listed)
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <GxEPD2_BW.h>
#include "secrets.h"

// ── Display pin mapping ─────────────────────────────────────────────────────
// Verify against Seeed reTerminal E1001 schematic before flashing.
#define EPD_CS    8
#define EPD_DC    9
#define EPD_RST   10
#define EPD_BUSY  11

// ── Button GPIOs (confirmed from build prompt) ────────────────────────────────
#define BTN_REFRESH  3   // Force immediate refresh
#define BTN_RIGHT    4   // Step to next widget
#define BTN_LEFT     5   // Step to previous widget

// ── Battery ADC ─────────────────────────────────────────────────────────────
// If the board exposes a fuel-gauge IC over I2C, replace this section.
// Otherwise this reads the LiPo via a voltage divider on an ADC pin.
// Check your board schematic for the actual ADC pin and divider ratio.
#define BATTERY_ADC_PIN  A0
#define VDIV_RATIO       2.0   // adjust for your voltage divider
#define ADC_VREF         3.3

// ── Refresh interval ─────────────────────────────────────────────────────────
#define REFRESH_MS  (20UL * 60 * 1000)   // 20 minutes

// Display: Waveshare 7.5" V2, 800×480
// Change to the matching GxEPD2 class for your exact panel revision.
GxEPD2_BW<GxEPD2_750_T7, GxEPD2_750_T7::HEIGHT> display(
    GxEPD2_750_T7(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));

static const int EPD_W = 800;
static const int EPD_H = 480;
static const int BMP_ROW_BYTES = EPD_W / 8;        // 100 bytes per row
static const int BMP_PIXEL_BYTES = BMP_ROW_BYTES * EPD_H;   // 48 000 bytes
static const int BMP_HEADER_SIZE = 54;              // standard 1-bit BMP header

static uint8_t imgBuf[BMP_PIXEL_BYTES];
static uint8_t rowBuf[BMP_ROW_BYTES];

static unsigned long lastRefreshMs = 0;
static String currentWidget = "";   // "" = use server rotation

// ── Setup ──────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    delay(500);

    pinMode(BTN_REFRESH, INPUT_PULLUP);
    pinMode(BTN_RIGHT,   INPUT_PULLUP);
    pinMode(BTN_LEFT,    INPUT_PULLUP);

    display.init(115200);
    display.setRotation(0);
    display.fillScreen(GxEPD_WHITE);
    display.display();

    Serial.println("Connecting to WiFi...");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.printf("\nConnected: %s\n", WiFi.localIP().toString().c_str());

    // Trigger first render immediately
    lastRefreshMs = millis() - REFRESH_MS;
}

// ── Main loop ──────────────────────────────────────────────────────────────

void loop() {
    bool doRefresh = false;
    String widgetRequest = "";

    // Button: immediate refresh (no widget override)
    if (digitalRead(BTN_REFRESH) == LOW) {
        delay(50);
        if (digitalRead(BTN_REFRESH) == LOW) {
            Serial.println("BTN_REFRESH pressed");
            doRefresh = true;
            widgetRequest = "";
            while (digitalRead(BTN_REFRESH) == LOW) delay(10);
        }
    }

    // Button: step right
    if (digitalRead(BTN_RIGHT) == LOW) {
        delay(50);
        if (digitalRead(BTN_RIGHT) == LOW) {
            Serial.println("BTN_RIGHT pressed");
            doRefresh = true;
            widgetRequest = nextWidget(currentWidget, +1);
            while (digitalRead(BTN_RIGHT) == LOW) delay(10);
        }
    }

    // Button: step left
    if (digitalRead(BTN_LEFT) == LOW) {
        delay(50);
        if (digitalRead(BTN_LEFT) == LOW) {
            Serial.println("BTN_LEFT pressed");
            doRefresh = true;
            widgetRequest = nextWidget(currentWidget, -1);
            while (digitalRead(BTN_LEFT) == LOW) delay(10);
        }
    }

    // Periodic refresh
    if (millis() - lastRefreshMs >= REFRESH_MS) {
        doRefresh = true;
        widgetRequest = "";
    }

    if (doRefresh) {
        int bat = readBatteryPct();
        bool ok = fetchAndDisplay(widgetRequest, bat);
        if (ok) {
            lastRefreshMs = millis();
            if (widgetRequest.length() > 0) currentWidget = widgetRequest;
        }
    }
}

// ── HTTP + display ─────────────────────────────────────────────────────────

bool fetchAndDisplay(const String& widgetOverride, int batteryPct) {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi disconnected, reconnecting...");
        WiFi.reconnect();
        unsigned long t = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - t < 10000) delay(200);
        if (WiFi.status() != WL_CONNECTED) return false;
    }

    String url = String("https://") + RENDER_HOST +
                 "/api/render?device=" + DEVICE_ID +
                 "&token=" + DEVICE_TOKEN +
                 "&battery=" + batteryPct;
    if (widgetOverride.length() > 0) {
        url += "&widget=" + widgetOverride;
    }

    Serial.printf("GET %s\n", url.c_str());

    WiFiClientSecure client;
    client.setInsecure();  // replace with CA cert for production
    HTTPClient http;
    http.begin(client, url);
    http.setTimeout(30000);
    int code = http.GET();

    if (code != 200) {
        Serial.printf("HTTP %d\n", code);
        http.end();
        return false;
    }

    int len = http.getSize();
    Serial.printf("Response size: %d bytes\n", len);

    WiFiClient* stream = http.getStreamPtr();

    // Read BMP header (54 bytes) to find pixel data offset
    uint8_t hdr[54];
    if (!readExact(stream, hdr, 54)) {
        Serial.println("Short header");
        http.end();
        return false;
    }

    if (hdr[0] != 'B' || hdr[1] != 'M') {
        Serial.println("Not a BMP");
        http.end();
        return false;
    }

    uint32_t dataOffset = hdr[10] | (hdr[11] << 8) | (hdr[12] << 16) | (hdr[13] << 24);
    // Skip any extra header bytes
    int extra = (int)dataOffset - 54;
    for (int i = 0; i < extra; i++) {
        uint8_t b;
        stream->read(&b, 1);
    }

    // Read pixel data: BMP stores rows bottom-up, each row 100 bytes
    // We store them top-down in imgBuf by filling from the end
    int bytesRead = 0;
    for (int row = EPD_H - 1; row >= 0; row--) {
        if (!readExact(stream, rowBuf, BMP_ROW_BYTES)) {
            Serial.printf("Short pixel data at row %d\n", row);
            http.end();
            return false;
        }
        // BMP 1-bit: 1=white, 0=black; Waveshare 7.5 V2 uses 0=black 1=white
        // Invert bits: comment out if display shows inverted image
        for (int j = 0; j < BMP_ROW_BYTES; j++) rowBuf[j] = ~rowBuf[j];
        memcpy(imgBuf + row * BMP_ROW_BYTES, rowBuf, BMP_ROW_BYTES);
        bytesRead += BMP_ROW_BYTES;
    }

    http.end();
    Serial.printf("Read %d pixel bytes\n", bytesRead);

    // Push to display
    display.setFullWindow();
    display.writeImage(imgBuf, 0, 0, EPD_W, EPD_H);
    display.refresh(false);  // full refresh (set true for partial if supported)
    display.hibernate();

    Serial.println("Display updated");
    return true;
}

// ── Helpers ────────────────────────────────────────────────────────────────

bool readExact(WiFiClient* stream, uint8_t* buf, int n) {
    int total = 0;
    unsigned long t = millis();
    while (total < n) {
        if (millis() - t > 10000) return false;
        int avail = stream->available();
        if (avail <= 0) { delay(5); continue; }
        int chunk = min(avail, n - total);
        total += stream->read(buf + total, chunk);
    }
    return total == n;
}

int readBatteryPct() {
    // LiPo discharge curve approximation (voltage → percentage)
    // Values calibrated for 3.0 V (0%) to 4.2 V (100%)
    int raw = analogRead(BATTERY_ADC_PIN);
    float voltage = raw / 4095.0f * ADC_VREF * VDIV_RATIO;

    if (voltage >= 4.20f) return 100;
    if (voltage >= 4.10f) return 90 + (int)((voltage - 4.10f) / 0.10f * 10);
    if (voltage >= 3.95f) return 70 + (int)((voltage - 3.95f) / 0.15f * 20);
    if (voltage >= 3.80f) return 50 + (int)((voltage - 3.80f) / 0.15f * 20);
    if (voltage >= 3.65f) return 25 + (int)((voltage - 3.65f) / 0.15f * 25);
    if (voltage >= 3.40f) return 10 + (int)((voltage - 3.40f) / 0.25f * 15);
    if (voltage >= 3.00f) return  0 + (int)((voltage - 3.00f) / 0.40f * 10);
    return 0;
}

static const char* WIDGET_ORDER[] = {
    "weather", "calendar", "meal_plan", "commute", "portfolio"
};
static const int N_WIDGETS = 5;

String nextWidget(const String& current, int delta) {
    int idx = 0;
    for (int i = 0; i < N_WIDGETS; i++) {
        if (current == WIDGET_ORDER[i]) { idx = i; break; }
    }
    idx = (idx + delta + N_WIDGETS) % N_WIDGETS;
    return String(WIDGET_ORDER[idx]);
}
