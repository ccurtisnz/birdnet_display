# BirdNET Display

A Python-based web application designed to run on a Raspberry Pi connecting to a remote BirdNet-PI


## Features
- Designed for Raspberry Pi with a connected display.
- Integrates with BirdNet-PI
- Displays the IP address (including a QR code) of the Raspberry Pi on the webpage.
- Guided setup modal with QR code hand-off so you can enter the BirdNET-Pi base URL from a phone.
- Caches images for all birds in the species list so the app can work completely offline and still display birds.
- Simple and responsive web interface.
- Kiosk mode for dedicated display on a Raspberry Pi.
- System controls from the web interface (brightness, reboot, power off).

## Prerequisites



## Setup and Installation

To be redone


### Application Settings & Initial Configuration

The display guides you through the initial setup the first time it loads:

1. Power up the Pi and wait for the "Connect to BirdNET-Pi" modal.
2. Scan the on-screen QR code (or open the kiosk URL directly) to load the setup form on your phone or laptop.
3. Enter the BirdNET-Pi base URL (for example `http://192.168.1.213`), submit, and the kiosk will automatically refresh when the configuration is saved.

The only remaining tunable server-side values live near the top of `birdnet_display.py`:

-   `SERVER_PORT`: The port for the display web server (defaults to 5000).




### Web Interface Controls

The web interface provides several interactive controls accessible by clicking on the main display area to reveal a QR code and settings icon:

-   **Display Layout:** Change how bird detections are arranged on the screen (e.g., 1 bird, 3 birds, 4 tall, 4 grid).
-   **Screen Brightness:** Adjust the display brightness using a slider.
-   **System Controls:** Buttons for restarting or powering off the Raspberry Pi.



### Required Hardware

For the main housing, you will need:

- RPI 4B is confirmed other Pis might not fit (mainly thinking about the usb wifi adaptor)
- 5" DSI touch screen (eg. https://www.aliexpress.com/item/1005007091586628.html)
- USB-C connector holes perpendicular to connector (eg. D-type of https://www.aliexpress.com/item/1005005010606562.html)
- Heatsink I used:
	- GeeekPi Armor lite heatsink for Raspberry Pi 4 (https://52pi.com/products/52pi-cnc-extreme-heatsink-with-pwm-fan-for-raspberry-pi-4) though I had to drill out the threaded holes to allow mount the RPi to the screen

- 4x Threaded inserts M2.5xD3.5xL3
- 4x M2.5x8mm button head screws
- 4x M2.5x4mm button head screws
- 2x M2.5x4mm countersunk head screws
- 2x M2.5x8mm countersunk head screws
- 2x M2x6mm Button head screws

## Troubleshooting


## Credit to
[https://github.com/C4KEW4LK/birdnet_display](https://github.com/C4KEW4LK/birdnet_display)

## Disclaimer

This software is provided "as is" and is confirmed to work with my specific setup. However, it may not be compatible with other configurations. Your mileage may vary.

## License
MIT License
