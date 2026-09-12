#include <Servo.h>

const int SERVO1_PIN = 8;
const int SERVO2_PIN = 9;

const int MIN_PULSE_US = 500;
const int MAX_PULSE_US = 2500;

const int ZERO_ANGLE = 0;
const int MAX_ANGLE = 180;
const unsigned long SETTLE_MS = 1000;

Servo servo1;
Servo servo2;

int angle = MAX_ANGLE;

void reset() {
  // setting the elevtion to max
  servo1.write(MAX_ANGLE);
  servo2.write(MAX_ANGLE);
  delay(SETTLE_MS);

}

void step_down() {
  if (!angle) return;
  angle -= 4;
  servo1.write(angle);
  servo2.write(angle);
  delay(SETTLE_MS);
}

void elevation_scanning_routine() {
  while (0 < angle)
  {
    step_down();
  }
  
}

void setup() {
  Serial.begin(115200);

  servo1.attach(SERVO1_PIN, MIN_PULSE_US, MAX_PULSE_US);
  servo2.attach(SERVO2_PIN, MIN_PULSE_US, MAX_PULSE_US);

  reset();
  elevation_scanning_routine();
  reset();
}

void loop() {
  // nada
}
