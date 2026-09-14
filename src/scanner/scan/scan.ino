#include <Servo.h>
#include "Adafruit_VL53L1X.h"
#include <Wire.h>
#include "WDT.h"

#define IRQ_PIN -1
#define SERVO1_PIN 8
#define SERVO2_PIN 9
#define SERVO3_PIN 10
#define XSHUT_A_PIN 2
#define XSHUT_B_PIN 3

#define SENSOR_A_ADDR 0x30
#define SENSOR_B_ADDR 0x29

#define MIN_PULSE_US 500
#define MAX_PULSE_US 2500

#define ZERO_ANGLE 0
#define MAX_ANGLE 176
#define D_VERTICAL 1            // change in vertical angle
#define dh 0.36                 // actual vertical change per 1 deg
#define DETECTED_THRESH_MM 150  // is obj detected boundry 
#define ROTATION_STEP_MAG 2     // abs change in rotation angle

#define RESET_SETTLE_MS 1000
#define STEP_SETTLE_MS 125

#define MEASURE_COUNT 10
#define OUT_OF_RANGE_MM 9999
#define DATA_READY_TIMEOUT_MS 300
#define READ_RETRY_COUNT 3

#define WDT_TIMEOUT_MS 2000  // watchdog resets the board if not fed within this

struct measurement {
  int16_t dist_a;
  int16_t dist_b;
  float h;
  int thetha;
};

//// Elevation & Rotation Control

Servo servo1;
Servo servo2;
Servo servo3;

int vertical_angle = ZERO_ANGLE;
int rotational_angle = ZERO_ANGLE;
int d_rotational_angle = ROTATION_STEP_MAG;

void wdtDelay(unsigned long ms) {
  while (ms > 0) {
    unsigned long chunk = ms < 250 ? ms : 250;
    delay(chunk);
    WDT.refresh();
    ms -= chunk;
  }
}

void reset() {
  // setting the elevtion to max
  servo1.write(MAX_ANGLE);
  servo2.write(MAX_ANGLE);
  vertical_angle = MAX_ANGLE;

  // setting rotation plate to zero state
  servo3.write(ZERO_ANGLE);
  wdtDelay(RESET_SETTLE_MS * 10);
}

void vertical_step() {
  vertical_angle -= D_VERTICAL;
  servo1.write(vertical_angle);
  servo2.write(vertical_angle);
  delay(STEP_SETTLE_MS);
}

void rotational_step() {
  rotational_angle += d_rotational_angle;
  servo3.write(rotational_angle);
  delay(STEP_SETTLE_MS);
}

//// ToF Measurment

Adafruit_VL53L1X vl53_a = Adafruit_VL53L1X(XSHUT_A_PIN, IRQ_PIN);
Adafruit_VL53L1X vl53_b = Adafruit_VL53L1X(XSHUT_B_PIN, IRQ_PIN);

void init_sensors() {
  // setting tje pins xshut as output
  pinMode(XSHUT_A_PIN, OUTPUT);
  pinMode(XSHUT_B_PIN, OUTPUT);

  // shutting down both sensors
  digitalWrite(XSHUT_A_PIN, LOW);
  digitalWrite(XSHUT_B_PIN, LOW);

  delay(10);
  Wire.begin();

  // bring up sensor a
  if (!vl53_a.begin(SENSOR_A_ADDR, &Wire)) {
    Serial.println(F("Error: Could not find Sensor A. Check wiring!"));
    while (1);
  }

  // bring up sensor b
  if (!vl53_b.begin(SENSOR_B_ADDR, &Wire)) {
    Serial.println(F("Error: Could not find Sensor B. Check wiring!"));
    while (1);
  }

  if (!vl53_a.startRanging() || !vl53_b.startRanging()) {
    Serial.println(F("Failed to start ranging!"));
    while (1);
  }

  vl53_a.setTimingBudget(50);
  vl53_b.setTimingBudget(50);
}

int16_t read_distance(Adafruit_VL53L1X& sensor) {
  WDT.refresh();
  for (int attempt = 0; attempt < READ_RETRY_COUNT; attempt++) { // will attempt several readings in case of a misreading
    unsigned long wait_start = millis();
    bool ready = false;
    while (millis() - wait_start <= DATA_READY_TIMEOUT_MS) { // gives the sensor time to read
      if (sensor.dataReady()) {
        ready = true;
        break;
      }
      delay(1);
    }

    if (ready) {
      int16_t distance_mm = sensor.distance();
      sensor.clearInterrupt();
      if (distance_mm < 0) distance_mm = OUT_OF_RANGE_MM;
      return distance_mm;
    }

    Serial.print(F("WARN: sensor data-ready timeout, retry "));
    Serial.println(attempt + 1);
  }

  Serial.println(F("WARN: sensor still not responding after retries, skipping sample"));
  return OUT_OF_RANGE_MM;
}

bool measure(measurement& m, int measure_count) {
  m.h = (MAX_ANGLE - vertical_angle) * dh;
  m.thetha = rotational_angle;

  long sum_a = 0;
  long sum_b = 0;
  for (int i = 0; i < measure_count; i++) {
    sum_a += read_distance(vl53_a);
    sum_b += read_distance(vl53_b);
  }
  // returning the avg measured
  m.dist_a = sum_a / measure_count;
  m.dist_b = sum_b / measure_count;
  return m.dist_a < DETECTED_THRESH_MM && m.dist_b < DETECTED_THRESH_MM;
}

//// Serial Communication

void send_data(measurement& m) {
  Serial.print("{\"theta\":");
  Serial.print(m.thetha);
  Serial.print(",\"h\":");
  Serial.print(m.h, 3);
  Serial.print(",\"dist_a\":");
  Serial.print(m.dist_a);
  Serial.print(",\"dist_b\":");
  Serial.print(m.dist_b);
  Serial.println("}");
}

//// Scaning

const int ROTATION_STEPS = MAX_ANGLE / ROTATION_STEP_MAG + 1; 

void scanning_routine() {
  Serial.println("SCAN_START");

  while (ZERO_ANGLE < vertical_angle) {
    bool detected_any = false;

    for (int i = 0; i < ROTATION_STEPS; i++) {
      measurement m;
      bool detected = measure(m, MEASURE_COUNT);
      if (detected) {
        send_data(m);
        detected_any = true;
      }
      if (i < ROTATION_STEPS - 1)
        rotational_step();
    }

    if (!detected_any) break;

    d_rotational_angle *= -1; // reverse for the next ring
    vertical_step();
  }

  Serial.println("SCAN_END");
}

void setup() {
  Serial.begin(115200);

  // wait 3 seconds to let Serial Monitor connection to establish
  unsigned long start = millis();
  while (!Serial && (millis() - start < 3000)) delay(10);

  servo1.attach(SERVO1_PIN, MIN_PULSE_US, MAX_PULSE_US);
  servo2.attach(SERVO2_PIN, MIN_PULSE_US, MAX_PULSE_US);
  servo3.attach(SERVO3_PIN, MIN_PULSE_US, MAX_PULSE_US);

  init_sensors();

  if (!WDT.begin(WDT_TIMEOUT_MS)) {
    Serial.println(F("Warning: failed to start watchdog"));
  }

  reset();
  scanning_routine();
  reset();
}

void loop() {
  // nada
}
