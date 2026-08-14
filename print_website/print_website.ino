#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <LittleFS.h>

HardwareSerial Printer(2);

// =======================
// ESP32 pins to RP203 TTL
// =======================
static const int PRINTER_RX = 16;
static const int PRINTER_TX = 17;

// =======================
// Wi-Fi credentials
// =======================
const char* WIFI_SSID = "Your WiFi SSID";
const char* WIFI_PASS = "Your Password";

// Optional fallback AP
const char* AP_SSID = "RP203-Printer";
const char* AP_PASS = "12345678";

// =======================
// Printer tuning
// =======================
static const uint8_t HEAT_MAX_DOTS = 4;
static const uint8_t HEAT_TIME     = 190;
static const uint8_t HEAT_INTERVAL = 180;

// =======================
// Web server
// =======================
WebServer server(80);
File uploadFile;
const char* BIN_PATH = "/last.bin";

uint16_t lastWidthBytes = 0;
uint16_t lastHeight = 0;
bool imageReady = false;

void printerInit() {
  uint8_t init_cmd[] = {0x1B, 0x40};
  Printer.write(init_cmd, sizeof(init_cmd));
  delay(200);

  uint8_t heat_cmd[] = {
    0x1B, 0x37,
    HEAT_MAX_DOTS,
    HEAT_TIME,
    HEAT_INTERVAL
  };
  Printer.write(heat_cmd, sizeof(heat_cmd));
  delay(200);

  uint8_t line_space[] = {0x1B, 0x32};
  Printer.write(line_space, sizeof(line_space));
  delay(50);
}

void feedLines(uint8_t n) {
  for (uint8_t i = 0; i < n; i++) {
    Printer.write('\n');
  }
}

bool readHeaderFromFile(File &f, uint16_t &widthBytes, uint16_t &height) {
  if (!f || f.size() < 4) return false;

  f.seek(0, SeekSet);
  uint8_t hdr[4];
  if (f.read(hdr, 4) != 4) return false;

  widthBytes = (uint16_t)hdr[0] | ((uint16_t)hdr[1] << 8);
  height     = (uint16_t)hdr[2] | ((uint16_t)hdr[3] << 8);

  if (widthBytes == 0 || height == 0) return false;
  return true;
}

bool printStoredBitmap() {
  File f = LittleFS.open(BIN_PATH, "r");
  if (!f) return false;

  uint16_t widthBytes = 0, height = 0;
  if (!readHeaderFromFile(f, widthBytes, height)) {
    f.close();
    return false;
  }

  const uint32_t dataBytes = (uint32_t)widthBytes * (uint32_t)height;
  if (f.size() < 4 + dataBytes) {
    f.close();
    return false;
  }

  uint8_t header[] = {
    0x1D, 0x76, 0x30, 0x00,
    (uint8_t)(widthBytes & 0xFF),
    (uint8_t)((widthBytes >> 8) & 0xFF),
    (uint8_t)(height & 0xFF),
    (uint8_t)((height >> 8) & 0xFF)
  };

  Printer.write(header, sizeof(header));
  delay(30);

  uint8_t buf[256];
  uint32_t remaining = dataBytes;

  while (remaining > 0) {
    size_t toRead = remaining > sizeof(buf) ? sizeof(buf) : remaining;
    size_t n = f.read(buf, toRead);
    if (n == 0) break;

    Printer.write(buf, n);
    remaining -= n;

    if ((remaining & 0x1FF) == 0) {
      delay(4);
    }
  }

  f.close();
  delay(800);
  feedLines(4);
  return true;
}

String htmlPage() {
  String s;
  s.reserve(3500);

  s += F("<!doctype html><html><head><meta charset='utf-8'>");
  s += F("<meta name='viewport' content='width=device-width, initial-scale=1'>");
  s += F("<title>RP203 Printer</title>");
  s += F("<style>");
  s += F("body{font-family:Arial,sans-serif;max-width:760px;margin:20px auto;padding:0 12px;background:#f5f5f5;color:#111}");
  s += F(".card{background:#fff;padding:16px;border-radius:16px;box-shadow:0 2px 10px rgba(0,0,0,.08);margin-bottom:14px}");
  s += F("button,input,a{font-size:16px} button,a{padding:10px 14px;border:0;border-radius:10px;text-decoration:none;display:inline-block}");
  s += F("button{background:#111;color:#fff} a{background:#444;color:#fff} .muted{color:#666;font-size:14px}");
  s += F("code{background:#eee;padding:2px 6px;border-radius:6px}");
  s += F("</style></head><body>");

  s += F("<div class='card'><h2>RP203 Thermal Printer</h2>");
  s += F("<div class='muted'>Upload a packed <code>.bin</code> file and print it.</div>");
  s += F("<div class='muted'>Format: 2 bytes widthBytes, 2 bytes height, then bitmap bytes.</div></div>");

  s += F("<div class='card'><h3>Status</h3>");
  s += F("<p>Ready: ");
  s += (imageReady ? F("Yes") : F("No"));
  s += F("</p><p>Stored file: ");
  s += (LittleFS.exists(BIN_PATH) ? F("Yes") : F("No"));
  s += F("</p><p>WidthBytes: ");
  s += String(lastWidthBytes);
  s += F(" | Height: ");
  s += String(lastHeight);
  s += F("</p></div>");

  s += F("<div class='card'><h3>Upload</h3>");
  s += F("<form method='POST' action='/upload' enctype='multipart/form-data'>");
  s += F("<input type='file' name='imgfile' accept='.bin'><br><br>");
  s += F("<button type='submit'>Upload</button>");
  s += F("</form></div>");

  s += F("<div class='card'><h3>Print</h3>");
  s += F("<p><a href='/print'>Print Last Image</a></p>");
  s += F("<p><a href='/clear'>Clear Stored Image</a></p>");
  s += F("</div>");

  s += F("<div class='card'><h3>Wi-Fi</h3><p>STA IP: ");
  s += WiFi.localIP().toString();
  s += F("</p><p>AP IP: ");
  s += WiFi.softAPIP().toString();
  s += F("</p></div>");

  s += F("</body></html>");
  return s;
}

void handleRoot() {
  server.send(200, "text/html", htmlPage());
}

void handleUploadComplete() {
  server.send(200, "text/plain", "Upload OK. Open / and press Print Last Image.");
}

void handleFileUpload() {
  HTTPUpload& upload = server.upload();

  if (upload.status == UPLOAD_FILE_START) {
    if (LittleFS.exists(BIN_PATH)) {
      LittleFS.remove(BIN_PATH);
    }
    uploadFile = LittleFS.open(BIN_PATH, FILE_WRITE);
    imageReady = false;
    lastWidthBytes = 0;
    lastHeight = 0;
  } else if (upload.status == UPLOAD_FILE_WRITE) {
    if (uploadFile) {
      uploadFile.write(upload.buf, upload.currentSize);
    }
  } else if (upload.status == UPLOAD_FILE_END) {
    if (uploadFile) {
      uploadFile.close();
    }

    File f = LittleFS.open(BIN_PATH, "r");
    if (f) {
      uint16_t w = 0, h = 0;
      if (readHeaderFromFile(f, w, h)) {
        lastWidthBytes = w;
        lastHeight = h;
        imageReady = true;
      }
      f.close();
    }
  } else if (upload.status == UPLOAD_FILE_ABORTED) {
    if (uploadFile) {
      uploadFile.close();
    }
    if (LittleFS.exists(BIN_PATH)) {
      LittleFS.remove(BIN_PATH);
    }
  }
}

void handlePrint() {
  if (!LittleFS.exists(BIN_PATH)) {
    server.send(404, "text/plain", "No image uploaded yet.");
    return;
  }

  bool ok = printStoredBitmap();
  if (ok) {
    server.send(200, "text/plain", "Print done.");
  } else {
    server.send(500, "text/plain", "Print failed. Check file format.");
  }
}

void handleClear() {
  if (LittleFS.exists(BIN_PATH)) {
    LittleFS.remove(BIN_PATH);
  }
  imageReady = false;
  lastWidthBytes = 0;
  lastHeight = 0;
  server.send(200, "text/plain", "Cleared.");
}

void handleStatus() {
  String json = "{";
  json += "\"ready\":" + String(imageReady ? "true" : "false") + ",";
  json += "\"widthBytes\":" + String(lastWidthBytes) + ",";
  json += "\"height\":" + String(lastHeight) + ",";
  json += "\"staIP\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"apIP\":\"" + WiFi.softAPIP().toString() + "\"";
  json += "}";
  server.send(200, "application/json", json);
}

void setupWiFi() {
  WiFi.mode(WIFI_AP_STA);

  WiFi.softAP(AP_SSID, AP_PASS);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to Wi-Fi");

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("STA IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("STA connection failed, AP still active.");
  }

  Serial.print("AP IP: ");
  Serial.println(WiFi.softAPIP());
}

void setup() {
  Serial.begin(115200);

  Printer.begin(9600, SERIAL_8N1, PRINTER_RX, PRINTER_TX);

  if (!LittleFS.begin(true)) {
    Serial.println("LittleFS mount failed");
    while (true) delay(1000);
  }

  delay(2000);
  printerInit();

  setupWiFi();

  server.on("/", HTTP_GET, handleRoot);
  server.on("/print", HTTP_GET, handlePrint);
  server.on("/clear", HTTP_GET, handleClear);
  server.on("/status", HTTP_GET, handleStatus);

  server.on(
    "/upload",
    HTTP_POST,
    handleUploadComplete,
    handleFileUpload
  );

  server.begin();
  Serial.println("Web server started");
}

void loop() {
  server.handleClient();
}