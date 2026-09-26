/*
 * Fridge e-ink dashboard — reTerminal E1001 (ESP32-S3, 7.5" 800×480 e-paper)
 *
 * Required libraries (install via Arduino Library Manager):
 *   - GxEPD2 by ZinggJM  (tested ≥ 1.5.x)
 *   - Adafruit GFX Library
 *
 * Rename secrets.h.example → secrets.h and fill in your values.
 *
 * Board package: "esp32 by Espressif Systems" (NOT "Arduino ESP32 Boards",
 * which uploads via dfu-util and fails with "No DFU capable USB device").
 *
 * Arduino IDE → Tools:
 *   Board:  esp32 → "XIAO_ESP32S3" (Seeed's recommendation for the E1001)
 *   PSRAM:  "OPI PSRAM"
 *   USB CDC On Boot: "Disabled" (USB-C goes through a USB-UART bridge,
 *                    shows up as /dev/cu.usbserial-*)
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <WiFiClientSecure.h>
#include <GxEPD2_BW.h>
#include <Wire.h>
#include "driver/rtc_io.h"
#include "secrets.h"

// ── Display pin mapping (Seeed wiki: reTerminal E10xx with Arduino) ──────────
#define EPD_SCK   7
#define EPD_MOSI  9
#define EPD_CS    10
#define EPD_DC    11
#define EPD_RST   12
#define EPD_BUSY  13

// ── Button GPIOs (active low; all RTC-capable so they can wake deep sleep) ───
#define BTN_REFRESH  3   // Green: force immediate refresh
#define BTN_RIGHT    4   // Right white: step to next widget
#define BTN_LEFT     5   // Left white: step to previous widget

// ── Battery ADC (Seeed wiki: GPIO1 via 2:1 divider, gated by GPIO21) ────────
#define BATTERY_ADC_PIN  1
#define BATTERY_EN_PIN   21    // must be HIGH while sampling, LOW otherwise
#define VDIV_RATIO       2.0

// ── SHT40 temperature/humidity sensor (I2C) ─────────────────────────────────
#define SHT40_SDA        19
#define SHT40_SCL        20
#define SHT40_ADDR       0x44
#define SHT40_MEAS_HIGH  0xFD   // high-precision measurement, ~9 ms
#define TEMP_OFFSET_C    0.0f   // calibrate against a reference thermometer

// ── Refresh interval ─────────────────────────────────────────────────────────
#define REFRESH_US        (20ULL * 60 * 1000000)   // 20 minutes
#define RETRY_US          ( 5ULL * 60 * 1000000)   // after a failed fetch
#define WIFI_TIMEOUT_MS   15000

// Display: GDEY075T7 (UC8179), 800×480 — the E1001 panel.
// It sits on non-default SPI pins, so it gets its own HSPI bus.
SPIClass epdSpi(HSPI);
GxEPD2_BW<GxEPD2_750_GDEY075T7, GxEPD2_750_GDEY075T7::HEIGHT> display(
    GxEPD2_750_GDEY075T7(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));

// BMP from the server is 1 = white, which matches GxEPD2. Set to 1 if the
// screen comes out as a negative.
#define INVERT_BITS 0

static const int EPD_W = 800;
static const int EPD_H = 480;
static const int BMP_ROW_BYTES = EPD_W / 8;        // 100 bytes per row
static const int BMP_PIXEL_BYTES = BMP_ROW_BYTES * EPD_H;   // 48 000 bytes
static const int BMP_HEADER_SIZE = 54;              // standard 1-bit BMP header

static uint8_t imgBuf[BMP_PIXEL_BYTES];
static uint8_t rowBuf[BMP_ROW_BYTES];

static const uint64_t BTN_MASK =
    (1ULL << BTN_REFRESH) | (1ULL << BTN_RIGHT) | (1ULL << BTN_LEFT);

static const char* WIDGET_ORDER[] = {
    "weather", "calendar", "meal_plan", "commute", "portfolio"
};
static const int N_WIDGETS = 5;

int stepWidget(int idx, int delta);

// Survives deep sleep. -1 = follow server rotation.
RTC_DATA_ATTR static int currentWidgetIdx = -1;

// ── Setup: runs on every wake; the device sleeps between refreshes ─────────

void setup() {
    Serial.begin(115200);

    esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
    bool coldBoot = (cause != ESP_SLEEP_WAKEUP_EXT1 && cause != ESP_SLEEP_WAKEUP_TIMER);

    // Release buttons from RTC control so they read as normal GPIOs
    for (int pin : {BTN_REFRESH, BTN_RIGHT, BTN_LEFT}) {
        rtc_gpio_deinit((gpio_num_t)pin);
        pinMode(pin, INPUT_PULLUP);
    }

    String widgetRequest = "";
    if (cause == ESP_SLEEP_WAKEUP_EXT1) {
        uint64_t woke = esp_sleep_get_ext1_wakeup_status();
        if (woke & (1ULL << BTN_RIGHT)) {
            Serial.println("Woke: BTN_RIGHT");
            currentWidgetIdx = stepWidget(currentWidgetIdx, +1);
            widgetRequest = WIDGET_ORDER[currentWidgetIdx];
        } else if (woke & (1ULL << BTN_LEFT)) {
            Serial.println("Woke: BTN_LEFT");
            currentWidgetIdx = stepWidget(currentWidgetIdx, -1);
            widgetRequest = WIDGET_ORDER[currentWidgetIdx];
        } else {
            Serial.println("Woke: BTN_REFRESH");
        }
    } else {
        Serial.println(coldBoot ? "Cold boot" : "Woke: timer");
    }

    // Only clear the panel on cold boot; otherwise keep the last image while fetching
    epdSpi.begin(EPD_SCK, -1, EPD_MOSI, -1);
    display.epd2.selectSPI(epdSpi, SPISettings(2000000, MSBFIRST, SPI_MODE0));
    display.init(115200, coldBoot, 2, false);
    display.setRotation(0);

    // Read sensors before WiFi/display power up and warm the board
    int bat = readBatteryPct();
    float tempC = NAN, rh = NAN;
    readSHT40(tempC, rh);

    bool ok = connectWiFi() && fetchAndDisplay(widgetRequest, bat, tempC, rh);
    display.hibernate();

    goToSleep(ok ? REFRESH_US : RETRY_US);
}

void loop() {
    // Never reached: setup() always ends in deep sleep
}

// ── Power ──────────────────────────────────────────────────────────────────

bool connectWiFi() {
    Serial.println("Connecting to WiFi...");
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long t = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - t > WIFI_TIMEOUT_MS) {
            Serial.println("\nWiFi timeout");
            return false;
        }
        delay(200);
        Serial.print(".");
    }
    Serial.printf("\nConnected: %s\n", WiFi.localIP().toString().c_str());
    return true;
}

void goToSleep(uint64_t sleepUs) {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);

    // A held button would re-trigger ANY_LOW immediately; wait for release
    unsigned long t = millis();
    while ((digitalRead(BTN_REFRESH) == LOW || digitalRead(BTN_RIGHT) == LOW ||
            digitalRead(BTN_LEFT) == LOW) && millis() - t < 5000) {
        delay(10);
    }

    // Keep button pull-ups alive in deep sleep
    esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);
    for (int pin : {BTN_REFRESH, BTN_RIGHT, BTN_LEFT}) {
        rtc_gpio_pullup_en((gpio_num_t)pin);
        rtc_gpio_pulldown_dis((gpio_num_t)pin);
    }
    esp_sleep_enable_ext1_wakeup(BTN_MASK, ESP_EXT1_WAKEUP_ANY_LOW);
    esp_sleep_enable_timer_wakeup(sleepUs);

    Serial.printf("Sleeping %llu s\n", sleepUs / 1000000ULL);
    Serial.flush();
    esp_deep_sleep_start();
}

// ── HTTP + display ─────────────────────────────────────────────────────────

bool fetchAndDisplay(const String& widgetOverride, int batteryPct, float tempC, float rh) {
    String url = String("https://") + RENDER_HOST +
                 "/api/render?device=" + DEVICE_ID +
                 "&token=" + DEVICE_TOKEN +
                 "&battery=" + batteryPct;
    if (!isnan(tempC)) url += "&t_in=" + String(tempC, 1);
    if (!isnan(rh))    url += "&rh_in=" + String(rh, 0);
    if (widgetOverride.length() > 0) {
        url += "&widget=" + widgetOverride;
    }

    Serial.printf("GET %s\n", url.c_str());

    WiFiClientSecure client;
    client.setInsecure();  // replace with CA cert for production
    HTTPClient http;
    // HTTP/1.0 stops Azure from using chunked encoding; we read the raw stream
    http.useHTTP10(true);
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
        if (INVERT_BITS) {
            for (int j = 0; j < BMP_ROW_BYTES; j++) rowBuf[j] = ~rowBuf[j];
        }
        memcpy(imgBuf + row * BMP_ROW_BYTES, rowBuf, BMP_ROW_BYTES);
        bytesRead += BMP_ROW_BYTES;
    }

    http.end();
    Serial.printf("Read %d pixel bytes\n", bytesRead);

    // Push to display
    display.setFullWindow();
    display.writeImage(imgBuf, 0, 0, EPD_W, EPD_H);
    display.refresh(false);  // full refresh (set true for partial if supported)

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
    // Divider is only powered while BATTERY_EN_PIN is high
    pinMode(BATTERY_EN_PIN, OUTPUT);
    digitalWrite(BATTERY_EN_PIN, HIGH);
    delay(10);

    uint32_t mv = 0;
    const int samples = 8;
    for (int i = 0; i < samples; i++) mv += analogReadMilliVolts(BATTERY_ADC_PIN);
    digitalWrite(BATTERY_EN_PIN, LOW);

    float voltage = (mv / (float)samples) / 1000.0f * VDIV_RATIO;
    Serial.printf("Battery: %.2f V\n", voltage);

    // LiPo discharge curve approximation (voltage → percentage)
    // Values calibrated for 3.0 V (0%) to 4.2 V (100%)
    if (voltage >= 4.20f) return 100;
    if (voltage >= 4.10f) return 90 + (int)((voltage - 4.10f) / 0.10f * 10);
    if (voltage >= 3.95f) return 70 + (int)((voltage - 3.95f) / 0.15f * 20);
    if (voltage >= 3.80f) return 50 + (int)((voltage - 3.80f) / 0.15f * 20);
    if (voltage >= 3.65f) return 25 + (int)((voltage - 3.65f) / 0.15f * 25);
    if (voltage >= 3.40f) return 10 + (int)((voltage - 3.40f) / 0.25f * 15);
    if (voltage >= 3.00f) return  0 + (int)((voltage - 3.00f) / 0.40f * 10);
    return 0;
}

// Sensirion CRC-8: poly 0x31, init 0xFF
static uint8_t sht40Crc(const uint8_t* data) {
    uint8_t crc = 0xFF;
    for (int i = 0; i < 2; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (crc << 1) ^ 0x31 : crc << 1;
    }
    return crc;
}

bool readSHT40(float& tempC, float& rh) {
    Wire.begin(SHT40_SDA, SHT40_SCL);
    Wire.beginTransmission(SHT40_ADDR);
    Wire.write(SHT40_MEAS_HIGH);
    if (Wire.endTransmission() != 0) {
        Serial.println("SHT40: no ACK");
        return false;
    }
    delay(10);

    uint8_t buf[6];
    if (Wire.requestFrom((uint8_t)SHT40_ADDR, (size_t)6) != 6) {
        Serial.println("SHT40: short read");
        return false;
    }
    for (int i = 0; i < 6; i++) buf[i] = Wire.read();
    if (sht40Crc(buf) != buf[2] || sht40Crc(buf + 3) != buf[5]) {
        Serial.println("SHT40: CRC mismatch");
        return false;
    }

    uint16_t rawT  = (buf[0] << 8) | buf[1];
    uint16_t rawRH = (buf[3] << 8) | buf[4];
    tempC = -45.0f + 175.0f * rawT / 65535.0f + TEMP_OFFSET_C;
    rh = constrain(-6.0f + 125.0f * rawRH / 65535.0f, 0.0f, 100.0f);
    Serial.printf("SHT40: %.1f C, %.0f %%RH\n", tempC, rh);
    return true;
}

int stepWidget(int idx, int delta) {
    if (idx < 0) idx = 0;
    return (idx + delta + N_WIDGETS) % N_WIDGETS;
}
