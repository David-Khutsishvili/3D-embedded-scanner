#include <Servo.h>

const int SERVO3_PIN = 10;

const int MIN_PULSE_US = 500;
const int MAX_PULSE_US = 2500;

const int ZERO_ANGLE = 0;
const int MAX_ANGLE = 180;
const unsigned long SETTLE_MS = 1000;

Servo servo3;

int angle = ZERO_ANGLE;

void reset() {
  // setting the elevtion to zero
  servo3.write(ZERO_ANGLE);
  delay(SETTLE_MS);

}

void step() {
  angle += 4;
  servo3.write(angle);
  delay(SETTLE_MS);
}

void rotation_scanning_routine() {
  while (angle < MAX_ANGLE)
  {
    step();
  }
  
}

void setup() {
  Serial.begin(115200);

  servo3.attach(SERVO3_PIN, MIN_PULSE_US, MAX_PULSE_US);

  reset();
  rotation_scanning_routine();
  reset();
}

void loop() {
  // nada
}
