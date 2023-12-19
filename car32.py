import RPi.GPIO as GPIO
import time
from flask import Flask, render_template, Response
import picamera
import io
from threading import Thread
import threading
import sys



camera = picamera.PiCamera(resolution='640x480', framerate=24)

# GPIO Pin Configuration
AIN1, AIN2, BIN1, BIN2 = 17, 27, 22, 23
PWMA, PWMB = 12, 13
FRONT_US_TRIG, FRONT_US_ECHO = 5, 6
BACK_US_TRIG, BACK_US_ECHO = 16, 20
PAN_SERVO, TILT_SERVO = 18, 19
FLED1, FLED2, BLED1, BLED2 = 4, 24, 25, 26
BUZZER = 15
MODE = "MANUAL"  # possible values are "AUTONOMOUS" and "MANUAL"
ROVER_RUNNING = True
t = None
# Global variable and setup RGB LED pins
siren_running = False
siren_thread = None
RED_LED = 21
GREEN_LED = 8
BLUE_LED = 7

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Setting up GPIO pins
GPIO.setup([AIN1, AIN2, BIN1, BIN2, PWMA, PWMB, FRONT_US_TRIG, BACK_US_TRIG, FLED1, FLED2, BLED1, BLED2, BUZZER], GPIO.OUT)
GPIO.setup([FRONT_US_ECHO, BACK_US_ECHO], GPIO.IN)
GPIO.setup([PAN_SERVO, TILT_SERVO], GPIO.OUT)
GPIO.setup([RED_LED, GREEN_LED, BLUE_LED], GPIO.OUT)


pwmA = GPIO.PWM(PWMA, 100)
pwmB = GPIO.PWM(PWMB, 100)
pwmA.start(0)
pwmB.start(0)

pan_servo = GPIO.PWM(PAN_SERVO, 50)  # 50 Hz frequency
tilt_servo = GPIO.PWM(TILT_SERVO, 50)

pan_servo.start(7.5)  # Center the servo
tilt_servo.start(7.5)

def pan_left():
    pan_servo.ChangeDutyCycle(10)  # Move ~90 degrees

def pan_right():
    pan_servo.ChangeDutyCycle(5)  # Move ~0 degrees

def tilt_up():
    tilt_servo.ChangeDutyCycle(10) 

def tilt_down():
    tilt_servo.ChangeDutyCycle(5) 


# Define Siren Function
def siren():
    while siren_running:
        GPIO.output([RED_LED, BLUE_LED], (1, 0))  # RED ON, BLUE OFF
        time.sleep(0.5)  # Adjust the delay to control the flashing speed
        GPIO.output([RED_LED, BLUE_LED], (0, 1))  # RED OFF, BLUE ON
        time.sleep(0.5)

# Define Start and Stop Siren Functions
def start_siren():
    global siren_running, siren_thread
    siren_running = True
    siren_thread = threading.Thread(target=siren)
    siren_thread.start()

def stop_siren():
    global siren_running
    siren_running = False
    if siren_thread:
        siren_thread.join()
    # Ensure both LEDs are off when the siren stops
    GPIO.output([RED_LED, BLUE_LED], (0, 0))

# Modify Move and Stop Functions
def move_forward():
    GPIO.output([AIN1, AIN2, BIN1, BIN2], (1, 0, 1, 0))
    pwmA.ChangeDutyCycle(40)
    pwmB.ChangeDutyCycle(40)
    GPIO.output([FLED1, FLED2], 1)
    start_siren()  # Start siren when moving forward

def move_backward():
    GPIO.output([AIN1, AIN2, BIN1, BIN2], (0, 1, 0, 1))
    pwmA.ChangeDutyCycle(40)
    pwmB.ChangeDutyCycle(40)
    GPIO.output([BLED1, BLED2], 1)
    start_siren()  # Start siren when moving backward

def stop_moving():
    pwmA.ChangeDutyCycle(0)
    pwmB.ChangeDutyCycle(0)
    GPIO.output([FLED1, FLED2, BLED1, BLED2], 0)
    stop_siren()  # Stop siren when not moving

def rotate_left():
    GPIO.output([AIN1, AIN2, BIN1, BIN2], (0, 1, 1, 0))
    pwmA.ChangeDutyCycle(40)
    pwmB.ChangeDutyCycle(40)

def rotate_right():
    GPIO.output([AIN1, AIN2, BIN1, BIN2], (1, 0, 0, 1))
    pwmA.ChangeDutyCycle(40)
    pwmB.ChangeDutyCycle(40)

def read_distance(trig_pin, echo_pin):
    GPIO.output(trig_pin, True)
    time.sleep(0.00001)
    GPIO.output(trig_pin, False)

    start_time = time.time()
    end_time = time.time()
    
    timeout = time.time() + 1  # 1 second timeout for safety
    
    while GPIO.input(echo_pin) == 0:
        start_time = time.time()
        if time.time() > timeout:
            return float('inf')
            
    while GPIO.input(echo_pin) == 1:
        end_time = time.time()
        if time.time() > timeout:
            return float('inf')
        
    time_elapsed = end_time - start_time
    distance = (time_elapsed * 34300) / 2

    return distance


def beep():
    GPIO.output(BUZZER, True)
    time.sleep(0.1)
    GPIO.output(BUZZER, False)


def check_turn_distance(turn_function):
    turn_function()  # Rotate the rover slightly in the direction
    time.sleep(0.5)  # Give some time to complete the rotation. Adjust as necessary
    distance = read_distance(FRONT_US_TRIG, FRONT_US_ECHO)
    stop_moving()  # Stop the rover after measuring
    return distance

def switch_to_autonomous():
    global t, ROVER_RUNNING

    # Ensure the previous thread (if any) is not running
    ROVER_RUNNING = False
    if t:
        t.join()

    # Start the new thread for autonomous mode
    ROVER_RUNNING = True
    t = Thread(target=rover_function)
    t.start()

def switch_to_manual():
    global ROVER_RUNNING
    # Stop the autonomous thread
    ROVER_RUNNING = False
    if t:
        t.join()
    # Ensure the rover is stopped
    stop_moving()


app = Flask(__name__)

def gen(camera):
    stream = io.BytesIO()
    for _ in camera.capture_continuous(stream, format='jpeg', use_video_port=True):
        # Return the current frame
        stream.seek(0)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + stream.read() + b'\r\n\r\n')
        # Reset the stream for the next capture
        stream.seek(0)
        stream.truncate()


@app.route('/command/<action>')
def command(action):
    global MODE  # Declare MODE as global to modify it
    front_distance = read_distance(FRONT_US_TRIG, FRONT_US_ECHO)

    if front_distance < 20:  # If obstacle is closer than 20cm
        beep()
        stop_moving()
        return '', 204  # Return a no-content response

    if action == 'forward':
        move_forward()
    elif action == 'backward':
        move_backward()
    elif action == 'left':
        rotate_left()
    elif action == 'right':
        rotate_right()
    elif action == 'stop':
        stop_moving()
    return '', 204  # Return a no-content response

@app.route('/mode/<new_mode>')
def change_mode(new_mode):
    global MODE
    if new_mode == 'autonomous':
        MODE = "AUTONOMOUS"
        switch_to_autonomous()
    elif new_mode == 'manual':
        MODE = "MANUAL"
        switch_to_manual()
    return '', 204  # Return a no-content response

@app.route('/command/pan_left')
def pan_left_route():
    pan_left()
    return '', 204

@app.route('/command/pan_right')
def pan_right_route():
    pan_right()
    return '', 204

@app.route('/command/tilt_up')
def tilt_up_route():
    tilt_up()
    return '', 204

@app.route('/command/tilt_down')
def tilt_down_route():
    tilt_down()
    return '', 204

@app.route('/distance/front')
def front_distance():
    distance = read_distance(FRONT_US_TRIG, FRONT_US_ECHO)
    return str(distance)

@app.route('/distance/back')
def back_distance():
    distance = read_distance(BACK_US_TRIG, BACK_US_ECHO)
    return str(distance)


@app.route('/video_feed')
def video_feed():
    # Start recording to a file 'video.h264' in the same directory. 
    # This will overwrite the file if it already exists.
    camera.start_recording('video.h264', format='h264')
    try:
        return Response(gen(camera), mimetype='multipart/x-mixed-replace; boundary=frame')
    finally:
        camera.stop_recording()  # Stop recording when the feed ends or there's an error



@app.route('/')
def index():
    return render_template('index.html')

def rover_function():
    global MODE
    
    try:
        while ROVER_RUNNING:  # Use the flag to check if the rover should be running
            if MODE == "AUTONOMOUS":
                
                forward_distance = read_distance(FRONT_US_TRIG, FRONT_US_ECHO)

                if forward_distance > 20:  # safe distance
                    move_forward()
                else:
                    beep()
                    move_backward()
                    time.sleep(1.5)  # Increase backward time to ensure rover gets clear of the obstacle
                    stop_moving()
                    time.sleep(1)  # Let the rover remain stopped for a bit

                    # Decision making for left or right
                    left_distance = check_turn_distance(rotate_left)
                    right_distance = check_turn_distance(rotate_right)

                    if left_distance > right_distance:
                        rotate_left()
                        time.sleep(1)  # Time for the rover to rotate fully to the left. Adjust based on your requirement
                        move_forward()
                    else:
                        rotate_right()
                        time.sleep(1)  # Time for the rover to rotate fully to the right. Adjust based on your requirement
                        move_forward()
                
            time.sleep(0.1)          
    except KeyboardInterrupt:
        stop_moving()
        pan_servo.stop()
        tilt_servo.stop()
        GPIO.cleanup()
    # Properly shut down Flask threads here if necessary
        sys.exit(0)  # Exit the program

    except Exception as e:
        print("Error in rover_function:", e)      



if __name__ == '__main__':
    try:
        # Start the rover function in a separate thread
        t = Thread(target=rover_function)
        t.start()

        # Start the Flask app for video streaming
        app.run(host='0.0.0.0', port=5000, threaded=True)
    finally:
        camera.close()  # Ensure the camera is closed properly after execution




