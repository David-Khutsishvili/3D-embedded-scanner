#include <Servo.h>

const int SERVO_PIN = 9;

const int MIN_PULSE_US = 500;
const int MAX_PULSE_US = 2500;

const int ZERO_ANGLE = 0;
const int MAX_ANGLE = 180;
const unsigned long SETTLE_MS = 1000;

Servo servo;

void setup() {
  Serial.begin(115200);

  servo.attach(SERVO_PIN, MIN_PULSE_US, MAX_PULSE_US);

  // setting the elevting to max
  servo.write(MAX_ANGLE);
  delay(SETTLE_MS);


  Serial.print("Servo on D");
  Serial.print(SERVO_PIN);
  Serial.println(" swept to 180 deg and zeroed at 0 deg.");
}

void loop() {
  // nada
}
