<p align="center"> <img src="./assets/1.png" alt="Ground Control Station Banner" width="100%"> </p>

# Ground Control Station (GCS)

A Python-based Ground Control Station designed to solve the challenge of receiving, managing, and visualizing aerial images and associated aircraft telemetry transmitted over a wireless data link.

The application provides a centralized interface where operators can automatically receive transmitted images, access their associated GPS coordinates and aircraft telemetry, inspect images in detail, and organize received data into individual mission sessions. It eliminates the need for manually collecting and matching image files with their corresponding positional information.

---

## Core Features
- **Real-Time Image Reception:** Automatically receives JPEG images transmitted through a TCP data link.
- **Telemetry Reception:** Receives and displays associated aircraft information, including latitude, longitude, altitude, heading, GPS status, and timestamps.
- **Image Visualization:** Provides an interactive image viewer with zoom, pan, and automatic image fitting.
- **Geographical Positioning:** Associates received images with their geographical coordinates and provides direct Google Maps integration.
- **Data Integrity Verification:** Uses SHA-256 checksums to verify received image and telemetry payloads.
- **Mission Management:** Automatically creates timestamped mission sessions and organizes received images and metadata.
- **Automatic Synchronization:** Detects newly received files and updates the graphical interface without requiring application restarts.
- **Historical Data Access:** Allows operators to browse previously recorded mission sessions and inspect their associated images and telemetry.

---

## Communication & Data Management

The GCS operates as a TCP server listening on port 5055, receiving image and telemetry payloads from the airborne transmitter.

Each received image is stored alongside its corresponding telemetry file using a shared identifier.

---

## Technology Stack

- **GUI Framework:** PyQt5 (QtWidgets, QtCore, QtGui)
- **Networking Protocol:** Custom TCP framing protocol with binary length prefixing and JSON metadata headers
- **Cryptographic Validation:** SHA-256 checksums (`hashlib`)
- **System Monitoring:** `QFileSystemWatcher` with debounced event processing
- **Target Platform:** Linux (Ubuntu 20.04/22.04 LTS), cross-platform compatible

---

## File Format & Telemetry Schema

Each detection event is committed to the active session folder under a synchronized identifier pair (`<id>.jpg` and `<id>.txt`):

```text
image_id: 1
timestamp: 2026-10-23 16:45:12.500
latitude: 39.9333635
longitude: 32.8597419
altitude_msl_m: 920.50
altitude_rel_m: 55.20
heading_deg: 142.5
confidence: 0.8950
gps_status: 3D_LOCK
```


## Installation & Setup

#### Prerequisites
- Python 3.8+
- PyQt5
```bash
pip install PyQt5
```


## Running the Station
#### Launch the Ground Control Station dashboard:
```bash
python3 ground-control-station.py
```
- The application will bind to `port 5055`, create a new timestamped mission session directory inside `gcs_data/`, and await incoming datalink connections from the airborne transmitter.
