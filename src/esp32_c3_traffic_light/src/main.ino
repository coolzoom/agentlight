/*
  ESP32-C3 Codex 三色红绿灯控制程序

  硬件连接：
  - 红灯：GPIO20
  - 黄灯：GPIO2
  - 绿灯：GPIO21

  串口协议：
  以 115200 波特率发送一行字符串，支持：
  identify / id   -> 返回设备 ID（用于主机自动识别）
  idle
  thinking
  ai
  success
  busy
  wait_confirm
  confirm
  waiting
  wait
  error
  off

  兼容旧版命令：
  writing -> ai
  running -> busy
  done    -> success
*/

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <driver/ledc.h>

// 如果你的灯板是高电平点亮，把这里改成 1。
#define LED_ACTIVE_HIGH 0

// GPIO 修改集中放这里，后续换线只需要改这三个定义。
#define RED_LED_PIN 20
#define YELLOW_LED_PIN 2
#define GREEN_LED_PIN 21

const uint8_t LED_PWM_MAX = 255;
const uint8_t LED_PWM_LIMIT = 140;       // 约 55% 占空比
const uint8_t LED_PWM_SOFT = 84;         // 约 33% 占空比
const uint8_t LED_PWM_TRAIL = 28;        // 柔和拖尾亮度
const uint8_t LED_BREATH_MIN = 40;

const unsigned long IDLE_BREATH_STEP_MS = 14;
const unsigned long THINKING_CHASE_INTERVAL_MS = 110;
const unsigned long AI_CHASE_INTERVAL_MS = 240;
const unsigned long BUSY_BLINK_INTERVAL_MS = 550;
const unsigned long SUCCESS_HOLD_MS = 5000;
const unsigned long ERROR_BLINK_INTERVAL_MS = 130;
const unsigned long WAIT_BLINK_INTERVAL_MS = 550;

const char *BLE_DEVICE_NAME = "AgentCore-Light";
const char *BLE_SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0";
const char *BLE_CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef1";
const char *DEVICE_ID = "agent-signal-light-v1";

// 128 点呼吸表：sin 相位 -pi/2 起始于最低亮度，首尾同为 15，循环无跳变
const uint8_t BREATH_PERIOD = 128;
const uint8_t BREATH_SINE[BREATH_PERIOD] = {
   15,  15,  15,  16,  16,  17,  18,  19,
   20,  21,  22,  24,  26,  27,  29,  31,
   33,  36,  38,  40,  43,  45,  48,  51,
   54,  56,  59,  62,  65,  68,  71,  74,
   78,  81,  84,  87,  90,  93,  96,  99,
  101, 104, 107, 110, 112, 115, 117, 119,
  122, 124, 126, 128, 129, 131, 133, 134,
  135, 136, 137, 138, 139, 139, 140, 140,
  140, 140, 140, 139, 139, 138, 137, 136,
  135, 134, 133, 131, 129, 128, 126, 124,
  122, 119, 117, 115, 112, 110, 107, 104,
  101,  99,  96,  93,  90,  87,  84,  81,
   78,  74,  71,  68,  65,  62,  59,  56,
   54,  51,  48,  45,  43,  40,  38,  36,
   33,  31,  29,  27,  26,  24,  22,  21,
   20,  19,  18,  17,  16,  16,  15,  15,
};

enum LightState {
  STATE_IDLE,
  STATE_THINKING,
  STATE_AI,
  STATE_BUSY,
  STATE_SUCCESS,
  STATE_WAIT_CONFIRM,
  STATE_CONFIRM,
  STATE_WAITING,
  STATE_WAIT,
  STATE_ERROR,
  STATE_OFF
};

LightState currentState = STATE_IDLE;
String serialBuffer;
BLEServer *bleServer = nullptr;

unsigned long stateStartMs = 0;
unsigned long lastEffectFrameMs = 0;
uint8_t chaseIndex = 0;
bool blinkOn = false;
uint8_t breathIndex = 0;

// 避免每帧重复 ledc_stop 干扰同 timer 上其他 LEDC 通道
static int8_t lastPinBrightness[3] = {-1, -1, -1};

int pinSlot(uint8_t pin) {
  if (pin == RED_LED_PIN) return 0;
  if (pin == YELLOW_LED_PIN) return 1;
  if (pin == GREEN_LED_PIN) return 2;
  return -1;
}

void clearLedPinCache() {
  lastPinBrightness[0] = -1;
  lastPinBrightness[1] = -1;
  lastPinBrightness[2] = -1;
}

uint8_t brightnessToDuty(uint8_t brightness) {
  uint8_t limited = brightness;
  if (limited > LED_PWM_LIMIT) {
    limited = LED_PWM_LIMIT;
  }

#if LED_ACTIVE_HIGH
  return limited;
#else
  return LED_PWM_MAX - limited;
#endif
}

uint32_t ledOffIdleLevel() {
#if LED_ACTIVE_HIGH
  return 0;
#else
  return 1;
#endif
}

void stopLedPwmOff(uint8_t pin) {
  int8_t channel = analogGetChannel(pin);
  if (channel < 0) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, ledOffIdleLevel() ? HIGH : LOW);
    return;
  }

  ledc_mode_t speedMode = (ledc_mode_t)(channel / 8);
  ledc_channel_t ledcChannel = (ledc_channel_t)(channel % 8);
  ledc_stop(speedMode, ledcChannel, ledOffIdleLevel());
}

void writeLedPin(uint8_t pin, uint8_t brightness) {
  int slot = pinSlot(pin);
  int8_t prev = slot >= 0 ? lastPinBrightness[slot] : -1;

  if (brightness == 0) {
    if (prev != 0) {
      stopLedPwmOff(pin);
      if (slot >= 0) {
        lastPinBrightness[slot] = 0;
      }
    }
    return;
  }

  analogWrite(pin, brightnessToDuty(brightness));
  if (slot >= 0) {
    lastPinBrightness[slot] = (int8_t)brightness;
  }
}

void setLightLevels(uint8_t red, uint8_t yellow, uint8_t green) {
  writeLedPin(RED_LED_PIN, red);
  writeLedPin(YELLOW_LED_PIN, yellow);
  writeLedPin(GREEN_LED_PIN, green);
}

void setLight(bool red, bool yellow, bool green) {
  setLightLevels(
    red ? LED_PWM_LIMIT : 0,
    yellow ? LED_PWM_LIMIT : 0,
    green ? LED_PWM_LIMIT : 0
  );
}

void resetEffectState() {
  lastEffectFrameMs = 0;
  chaseIndex = 0;
  blinkOn = false;
  breathIndex = 0;
}

void updateEffect();

void enterState(LightState newState) {
  currentState = newState;
  stateStartMs = millis();
  resetEffectState();
  clearLedPinCache();
  if (newState == STATE_IDLE) {
    writeLedPin(RED_LED_PIN, 0);
    writeLedPin(YELLOW_LED_PIN, 0);
    writeLedPin(GREEN_LED_PIN, BREATH_SINE[0]);
  } else {
    setLight(false, false, false);
  }
  updateEffect();
}

bool setStatus(String status) {
  status.trim();
  status.toLowerCase();

  if (status.length() == 0) {
    return false;
  }

  if (status == "idle") {
    enterState(STATE_IDLE);
  } else if (status == "thinking") {
    enterState(STATE_THINKING);
  } else if (status == "ai" || status == "writing") {
    enterState(STATE_AI);
  } else if (status == "busy" || status == "running") {
    enterState(STATE_BUSY);
  } else if (status == "success" || status == "done") {
    if (currentState != STATE_SUCCESS) {
      enterState(STATE_SUCCESS);
    }
  } else if (status == "wait_confirm") {
    enterState(STATE_WAIT_CONFIRM);
  } else if (status == "confirm") {
    enterState(STATE_CONFIRM);
  } else if (status == "waiting") {
    enterState(STATE_WAITING);
  } else if (status == "wait") {
    enterState(STATE_WAIT);
  } else if (status == "error") {
    enterState(STATE_ERROR);
  } else if (status == "off") {
    enterState(STATE_OFF);
  } else {
    return false;
  }

  return true;
}

String extractJsonStringValue(const String &json, const char *key) {
  String pattern = "\"";
  pattern += key;
  pattern += "\"";

  int keyIndex = json.indexOf(pattern);
  if (keyIndex < 0) {
    return "";
  }

  int colonIndex = json.indexOf(':', keyIndex + pattern.length());
  if (colonIndex < 0) {
    return "";
  }

  int valueStart = colonIndex + 1;
  while (valueStart < json.length() && isspace(static_cast<unsigned char>(json[valueStart]))) {
    valueStart++;
  }

  if (valueStart >= json.length() || json[valueStart] != '"') {
    return "";
  }

  valueStart++;
  String value = "";

  for (int i = valueStart; i < json.length(); ++i) {
    char c = json[i];
    if (c == '\\' && i + 1 < json.length()) {
      i++;
      value += json[i];
      continue;
    }
    if (c == '"') {
      return value;
    }
    value += c;
  }

  return "";
}

class LightBleServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *server) override {
    Serial.println("BLE client connected.");
  }

  void onDisconnect(BLEServer *server) override {
    Serial.println("BLE client disconnected.");
    server->startAdvertising();
    Serial.println("BLE advertising restarted.");
  }
};

class LightBleCharacteristicCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    std::string raw = characteristic->getValue();
    String payload = String(raw.c_str());
    if (payload.length() == 0) {
      return;
    }

    String status = extractJsonStringValue(payload, "status");
    if (status.length() == 0) {
      Serial.print("BLE JSON missing status: ");
      Serial.println(payload);
      return;
    }

    if (!setStatus(status)) {
      Serial.print("Unknown BLE status: ");
      Serial.println(status);
      return;
    }

    Serial.print("BLE status changed to: ");
    Serial.println(status);
  }
};

void setupBle() {
  BLEDevice::init(BLE_DEVICE_NAME);

  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new LightBleServerCallbacks());

  BLEService *service = bleServer->createService(BLE_SERVICE_UUID);
  BLECharacteristic *statusCharacteristic = service->createCharacteristic(
    BLE_CHARACTERISTIC_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
  );

  statusCharacteristic->setCallbacks(new LightBleCharacteristicCallbacks());
  service->start();

  BLEAdvertising *advertising = bleServer->getAdvertising();
  advertising->addServiceUUID(BLE_SERVICE_UUID);
  advertising->start();

  Serial.print("BLE advertising as: ");
  Serial.println(BLE_DEVICE_NAME);
}

bool effectFrameDue(unsigned long intervalMs) {
  unsigned long now = millis();

  if (lastEffectFrameMs == 0 || now - lastEffectFrameMs >= intervalMs) {
    lastEffectFrameMs = now;
    return true;
  }

  return false;
}

void showThinkingChaseFrame() {
  switch (chaseIndex % 3) {
    case 0:
      setLightLevels(LED_PWM_LIMIT, 0, 0);
      break;
    case 1:
      setLightLevels(0, LED_PWM_LIMIT, 0);
      break;
    default:
      setLightLevels(0, 0, LED_PWM_LIMIT);
      break;
  }

  chaseIndex = (chaseIndex + 1) % 3;
}

void showAiChaseFrame() {
  switch (chaseIndex) {
    case 0:
      setLightLevels(LED_PWM_SOFT, LED_PWM_TRAIL, 0);
      break;
    case 1:
      setLightLevels(LED_PWM_TRAIL, LED_PWM_SOFT, 0);
      break;
    case 2:
      setLightLevels(0, LED_PWM_SOFT, LED_PWM_TRAIL);
      break;
    case 3:
      setLightLevels(0, LED_PWM_TRAIL, LED_PWM_SOFT);
      break;
    case 4:
      setLightLevels(LED_PWM_TRAIL, 0, LED_PWM_SOFT);
      break;
    default:
      setLightLevels(LED_PWM_SOFT, 0, LED_PWM_TRAIL);
      break;
  }

  chaseIndex = (chaseIndex + 1) % 6;
}

void updateIdleBreathing() {
  if (!effectFrameDue(IDLE_BREATH_STEP_MS)) {
    return;
  }

  breathIndex = (breathIndex + 1) % BREATH_PERIOD;
  setLightLevels(0, 0, BREATH_SINE[breathIndex]);
}

void updateEffect() {
  switch (currentState) {
    case STATE_IDLE:
      updateIdleBreathing();
      break;

    case STATE_THINKING:
      if (effectFrameDue(THINKING_CHASE_INTERVAL_MS)) {
        showThinkingChaseFrame();
      }
      break;

    case STATE_AI:
      if (effectFrameDue(AI_CHASE_INTERVAL_MS)) {
        showAiChaseFrame();
      }
      break;

    case STATE_BUSY:
      if (effectFrameDue(BUSY_BLINK_INTERVAL_MS)) {
        blinkOn = !blinkOn;
        setLightLevels(0, blinkOn ? LED_PWM_SOFT : 0, 0);
      }
      break;

    case STATE_SUCCESS:
      setLightLevels(0, 0, LED_PWM_LIMIT);
      if (millis() - stateStartMs >= SUCCESS_HOLD_MS) {
        enterState(STATE_IDLE);
        Serial.println("State changed to: idle");
      }
      break;

    case STATE_WAIT_CONFIRM:
    case STATE_CONFIRM:
    case STATE_WAITING:
    case STATE_WAIT:
      if (effectFrameDue(WAIT_BLINK_INTERVAL_MS)) {
        blinkOn = !blinkOn;
        setLightLevels(0, blinkOn ? LED_PWM_LIMIT : 0, 0);
      }
      break;

    case STATE_ERROR:
      if (effectFrameDue(ERROR_BLINK_INTERVAL_MS)) {
        blinkOn = !blinkOn;
        setLightLevels(blinkOn ? LED_PWM_LIMIT : 0, 0, 0);
      }
      break;

    case STATE_OFF:
      setLightLevels(0, 0, 0);
      break;
  }
}

void handleCommand(String command) {
  command.trim();
  command.toLowerCase();

  if (command.length() == 0) {
    return;
  }

  if (command == "identify" || command == "id") {
    Serial.print("ID: ");
    Serial.println(DEVICE_ID);
    return;
  }

  if (!setStatus(command)) {
    Serial.print("Unknown state: ");
    Serial.println(command);
    return;
  }

  Serial.print("State changed to: ");
  Serial.println(command);
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (serialBuffer.length() > 0) {
        handleCommand(serialBuffer);
        serialBuffer = "";
      }
    } else if (serialBuffer.length() < 31) {
      serialBuffer += c;
    }
  }
}

void setup() {
  Serial.begin(115200);
  unsigned long serialWaitStart = millis();
  while (!Serial && millis() - serialWaitStart < 3000) {
    delay(10);
  }

  serialBuffer.reserve(32);

  setLightLevels(0, 0, 0);
  setupBle();

  enterState(STATE_IDLE);

  Serial.print("READY ID=");
  Serial.println(DEVICE_ID);
  Serial.println("ESP32-C3 traffic light ready.");
  Serial.println("Commands: identify, idle, thinking, ai, success, busy, wait_confirm, confirm, waiting, wait, error, off");
}

void loop() {
  readSerialCommands();
  updateEffect();
}
