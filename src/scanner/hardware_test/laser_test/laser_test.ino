// Reads two VL53L1X distance sensors and prints both to the Serial Monitor.
//
// Both sensors share the single hardware I2C bus (A4/A5). Since they both
// boot at the same default address (0x29), XSHUT is used to bring them up
// one at a time so sensor A can be moved to a different address before
// sensor B is released onto the bus:
//   Sensor A XSHUT -> D2   (reassigned to 0x30)
//   Sensor B XSHUT -> D3   (stays at default 0x29)

#include "Adafruit_VL53L1X.h"
#include <Wire.h>

#define IRQ_PIN -1

const int XSHUT_A_PIN = 2;
const int XSHUT_B_PIN = 3;

const uint8_t SENSOR_A_ADDR = 0x30;
const uint8_t SENSOR_B_ADDR = 0x29;

Adafruit_VL53L1X vl53_a = Adafruit_VL53L1X(XSHUT_A_PIN, IRQ_PIN);
Adafruit_VL53L1X vl53_b = Adafruit_VL53L1X(XSHUT_B_PIN, IRQ_PIN);

void print_distance(const char *label, Adafruit_VL53L1X &sensor) {
  if (!sensor.dataReady()) return;

  int16_t distance_mm = sensor.distance();

  if (distance_mm != -1) {
    float distance_cm = distance_mm / 10.0;
    Serial.print(label);
    Serial.print(" distance: ");
    Serial.print(distance_mm);
    Serial.print(" mm  (");
    Serial.print(distance_cm, 1);
    Serial.println(" cm)");
  } else {
    Serial.print(label);
    Serial.println(": Out of range / Read error");
  }

  // Clear interrupt flag to trigger next measurement
  sensor.clearInterrupt();
}

void setup() {
  Serial.begin(115200);

  // Wait up to 3 seconds for Serial Monitor connection, then proceed anyway
  unsigned long start = millis();
  while (!Serial && (millis() - start < 3000)) {
    delay(10);
  }

  Serial.println("VL53L1X Dual Sensor Test (via XSHUT)");

  // Hold both sensors in shutdown until we bring them up one at a time.
  pinMode(XSHUT_A_PIN, OUTPUT);
  pinMode(XSHUT_B_PIN, OUTPUT);
  digitalWrite(XSHUT_A_PIN, LOW);
  digitalWrite(XSHUT_B_PIN, LOW);
  delay(10);

  Wire.begin();

  // Bring up sensor A first (still at the shared default address) and move
  // it off 0x29 while sensor B is still held in shutdown, so only A sees
  // the address-change command.
  if (!vl53_a.begin(SENSOR_A_ADDR, &Wire)) {
    Serial.println(F("Error: Could not find Sensor A. Check wiring!"));
    while (1);
  }
  Serial.println(F("Sensor A found!"));

  // Now release sensor B - the default address is free since A moved away.
  if (!vl53_b.begin(SENSOR_B_ADDR, &Wire)) {
    Serial.println(F("Error: Could not find Sensor B. Check wiring!"));
    while (1);
  }
  Serial.println(F("Sensor B found!"));

  if (!vl53_a.startRanging() || !vl53_b.startRanging()) {
    Serial.println(F("Failed to start ranging!"));
    while (1);
  }

  vl53_a.setTimingBudget(50);
  vl53_b.setTimingBudget(50);
}

void loop() {
  print_distance("Sensor A", vl53_a);
  print_distance("Sensor B", vl53_b);

  delay(20);
}
