#include <Servo.h>
#include "Adafruit_VL53L1X.h"
#include <Wire.h>

#define IRQ_PIN -1
#define d_vertical_angle 4
#define dh 1.44
#define detected_thresh 150 // mm - MUST BE CALIBRATED; below this counts as "object detected"

const int ROTATION_STEP_MAG = 4;
int d_rotational_angle = ROTATION_STEP_MAG;

const int SERVO1_PIN = 8;
const int SERVO2_PIN = 9;
const int SERVO3_PIN = 10;

const int MIN_PULSE_US = 500;
const int MAX_PULSE_US = 2500;

const int ZERO_ANGLE = 0;
const int MAX_ANGLE = 180;

// reset() swings a servo across the whole 0<->180 range - that's a real,
// slow move and needs a generous wait. A single scan step only moves a
// few degrees, and measure()'s own sampling loop already keeps the arm
// still for a while after that - so it only needs a small settle margin
// for the servo to physically get there before we start reading.
const unsigned long RESET_SETTLE_MS = 1000;
const unsigned long STEP_SETTLE_MS = 200;

const int MEASURE_COUNT = 10;
const int16_t OUT_OF_RANGE_MM = 9999; // stand-in for an invalid/out-of-range sensor read

// A reading normally shows up well within one timing budget (50ms). If it
// doesn't, retry a few times before finally giving up on that one sample -
// this is what used to hang the whole scan until a physical reset.
const unsigned long DATA_READY_TIMEOUT_MS = 300;
const int READ_RETRY_COUNT = 3;

const int ROTATION_STEPS = MAX_ANGLE / ROTATION_STEP_MAG + 1; // points per full 0<->180 sweep, both ends inclusive

struct measurement {
  int16_t dist_a;
  int16_t dist_b;
  float h;
  int thetha;
};

Servo servo1;
Servo servo2;
Servo servo3;

int vertical_angle = ZERO_ANGLE;
int rotational_angle = ZERO_ANGLE;

int scan_complete = 0;

void reset() {
  // setting the rotation to zero
  servo3.write(ZERO_ANGLE);
  rotational_angle = ZERO_ANGLE;
  // setting the elevtion to max
  servo1.write(MAX_ANGLE);
  servo2.write(MAX_ANGLE);
  vertical_angle = MAX_ANGLE;
  delay(RESET_SETTLE_MS * 10);
}

void vertical_step() {
  vertical_angle -= d_vertical_angle;
  servo1.write(vertical_angle);
  servo2.write(vertical_angle);
  delay(STEP_SETTLE_MS);
}

void rotational_step() {
  // Direction (d_rotational_angle's sign) is only ever flipped once, between
  // rings, by scanning_routine() - never mid-sweep - so this just walks one
  // step in whichever direction is currently set.
  rotational_angle += d_rotational_angle;
  servo3.write(rotational_angle);
  delay(STEP_SETTLE_MS);
}

//////////////////////////////////////////////////////////////////////////////////

const int XSHUT_A_PIN = 2;
const int XSHUT_B_PIN = 3;

const uint8_t SENSOR_A_ADDR = 0x30;
const uint8_t SENSOR_B_ADDR = 0x29;

Adafruit_VL53L1X vl53_a = Adafruit_VL53L1X(XSHUT_A_PIN, IRQ_PIN);
Adafruit_VL53L1X vl53_b = Adafruit_VL53L1X(XSHUT_B_PIN, IRQ_PIN);

void init_sensors() {
  // Hold both sensors in shutdown until we bring them up one at a time.
  pinMode(XSHUT_A_PIN, OUTPUT);
  pinMode(XSHUT_B_PIN, OUTPUT);
  digitalWrite(XSHUT_A_PIN, LOW);
  digitalWrite(XSHUT_B_PIN, LOW);
  delay(10);

  Wire.begin();

  // Bring up sensor A first and move it off the shared default address
  // while sensor B is still held in shutdown.
  if (!vl53_a.begin(SENSOR_A_ADDR, &Wire)) {
    Serial.println(F("Error: Could not find Sensor A. Check wiring!"));
    while (1);
  }

  // Now release sensor B - the default address is free since A moved away.
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

int16_t read_distance(Adafruit_VL53L1X &sensor) {
  // Occasionally a reading just doesn't show up (the sensor gets stuck and
  // stops flagging ready - normally only fixed by a physical reset). Try
  // the same position a few times before finally giving up on it, rather
  // than hanging the whole scan forever on the first missed reading.
  for (int attempt = 0; attempt < READ_RETRY_COUNT; attempt++) {
    unsigned long wait_start = millis();
    bool ready = false;
    while (millis() - wait_start <= DATA_READY_TIMEOUT_MS) {
      if (sensor.dataReady()) {
        ready = true;
        break;
      }
      delay(1);
    }

    if (ready) {
      int16_t distance_mm = sensor.distance();
      sensor.clearInterrupt();
      // An invalid/out-of-range read comes back as -1: treat it as "far
      // away" instead, so it can never masquerade as a close-object
      // detection below.
      if (distance_mm < 0) {
        distance_mm = OUT_OF_RANGE_MM;
      }
      return distance_mm;
    }

    Serial.print(F("WARN: sensor data-ready timeout, retry "));
    Serial.println(attempt + 1);
  }

  Serial.println(F("WARN: sensor still not responding after retries, skipping sample"));
  return OUT_OF_RANGE_MM;
}

bool measure(measurement& m, int measure_count) {
  m.h = ((MAX_ANGLE - vertical_angle) / d_vertical_angle) * dh;
  m.thetha = rotational_angle;

  long sum_a = 0;
  long sum_b = 0;
  for (int i = 0; i < measure_count; i++) {
    sum_a += read_distance(vl53_a);
    sum_b += read_distance(vl53_b);
  }
  // returning the avg measured
  // maybe ignore outliars later???
  m.dist_a = sum_a / measure_count;
  m.dist_b = sum_b / measure_count;
  return m.dist_a < detected_thresh && m.dist_b < detected_thresh;
}

void send_data(measurement& m) {
  // Send one measurement to the laptop over the USB-C serial connection,
  // as a single JSON object per line (JSON Lines / NDJSON) - self
  // describing, so no separate header/column-order to keep in sync.
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

void scanning_routine() {
  Serial.println("SCAN_START");

  // Serpentine: ring 1 sweeps 0->180, ring 2 sweeps 180->0, etc. - no
  // snapping rotation back to 0 between rings.
  while (vertical_angle > ZERO_ANGLE) {
    bool detected_any = false;

    for (int i = 0; i < ROTATION_STEPS; i++) {
      measurement m;
      bool detected = measure(m, MEASURE_COUNT);
      if (detected) {
        send_data(m);
        detected_any = true;
      }
      if (i < ROTATION_STEPS - 1) {
        rotational_step();
      }
    }

    if (!detected_any) break;

    d_rotational_angle = -d_rotational_angle; // reverse for the next ring
    vertical_step();
  }

  Serial.println("SCAN_END");
  scan_complete = 1;
}

void setup() {
  Serial.begin(115200);

  // Wait up to 3 seconds for Serial Monitor connection, then proceed anyway
  unsigned long start = millis();
  while (!Serial && (millis() - start < 3000)) {
    delay(10);
  }

  servo1.attach(SERVO1_PIN, MIN_PULSE_US, MAX_PULSE_US);
  servo2.attach(SERVO2_PIN, MIN_PULSE_US, MAX_PULSE_US);
  servo3.attach(SERVO3_PIN, MIN_PULSE_US, MAX_PULSE_US);

  init_sensors();

  reset();
  scanning_routine();
  reset();
}

void loop() {
  // nada
}
