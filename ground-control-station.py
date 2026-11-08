#!/usr/bin/env python3
import os
import sys
import json
import socket
import struct
import hashlib
import webbrowser
import time
from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSignal, QThread, QRectF, QFileSystemWatcher, QTimer
from PyQt5.QtGui import QPixmap, QPainter, QColor, QFont, QCursor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QGraphicsView,
    QGraphicsScene, QGraphicsPixmapItem, QFrame, QSplitter, QCheckBox,
    QComboBox
)

BASE_DATA_DIR = "gcs_mnm_data"
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 5055

os.makedirs(BASE_DATA_DIR, exist_ok=True)

def get_timestamp_session_name():
    """Generates a structured timestamp for the run session folder."""
    return f"session_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"

def recv_exact(conn, num_bytes):
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = conn.recv(min(65536, num_bytes - len(buf)))
        if not chunk:
            raise ConnectionError("Connection closed prematurely.")
        buf.extend(chunk)
    return bytes(buf)

def parse_txt_file(txt_path):
    info = {
        "image_id": "N/A", "timestamp": "N/A", "latitude": "N/A", "longitude": "N/A",
        "altitude_msl_m": "N/A", "altitude_rel_m": "N/A", "heading_deg": "N/A",
        "confidence": "N/A", "gps_status": "N/A", "raw_text": ""
    }
    
    if not os.path.exists(txt_path):
        info["raw_text"] = "No GPS data (.txt) file found for this image."
        return info
    
    try:
        with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
            info["raw_text"] = raw
            for line in raw.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    val = v.strip()
                    if val:
                        info[k.strip().lower()] = val
    except Exception as e:
        info["raw_text"] = f"Error reading file: {e}"
        
    return info


class ReceiverThread(QThread):
    sync_requested = pyqtSignal()
    status_changed = pyqtSignal(str)
    session_created = pyqtSignal(str)  # Signals UI when a new run starts

    def __init__(self):
        super().__init__()
        self.current_run_session = None
        self.last_seq_id = None

    def run(self):
        # Outer fault-tolerant loop: Prevents crashes if GCS is started before network adapters initialize.
        while True:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    server.bind((LISTEN_IP, LISTEN_PORT))
                    server.listen(2)
                    self.status_changed.emit(f"LISTENING ON PORT {LISTEN_PORT}")

                    while True:
                        conn, addr = server.accept()
                        self.status_changed.emit(f"AIR UNIT LINKED ({addr[0]})")
                        with conn:
                            try:
                                while True:
                                    raw_len = conn.recv(4)
                                    if not raw_len: break
                                    if len(raw_len) < 4: raw_len += recv_exact(conn, 4 - len(raw_len))

                                    header_len = struct.unpack("!I", raw_len)[0]
                                    header = json.loads(recv_exact(conn, header_len).decode("utf-8"))

                                    seq_id = str(header["id"])
                                    jpg_bytes = recv_exact(conn, header["jpg_size"])
                                    txt_bytes = recv_exact(conn, header["txt_size"])

                                    if hashlib.sha256(jpg_bytes).hexdigest() != header["jpg_sha256"] or \
                                       hashlib.sha256(txt_bytes).hexdigest() != header["txt_sha256"]:
                                        conn.sendall(f"ACK:{seq_id}:ERR_HASH\n".encode("utf-8"))
                                        continue

                                    # Session Creation Logic: Triggered ONLY on new data or sequence restart
                                    if self.current_run_session is None or (seq_id == "1" and self.last_seq_id != "1"):
                                        new_sess_name = get_timestamp_session_name()
                                        self.current_run_session = os.path.join(BASE_DATA_DIR, new_sess_name)
                                        os.makedirs(self.current_run_session, exist_ok=True)
                                        self.session_created.emit(new_sess_name)
                                    
                                    self.last_seq_id = seq_id

                                    final_jpg = os.path.join(self.current_run_session, header["jpg_name"])
                                    final_txt = os.path.join(self.current_run_session, header["txt_name"])
                                    
                                    tmp_jpg = final_jpg + ".tmp"
                                    tmp_txt = final_txt + ".tmp"

                                    with open(tmp_jpg, "wb") as f: f.write(jpg_bytes); f.flush(); os.fsync(f.fileno())
                                    with open(tmp_txt, "wb") as f: f.write(txt_bytes); f.flush(); os.fsync(f.fileno())

                                    os.replace(tmp_jpg, final_jpg)
                                    os.replace(tmp_txt, final_txt)

                                    conn.sendall(f"ACK:{seq_id}:OK\n".encode("utf-8"))
                                    self.sync_requested.emit()

                            except Exception as e:
                                self.status_changed.emit(f"LINK STANDBY ({e})")
            except Exception as e:
                self.status_changed.emit(f"NETWORK ERROR ({e}) - RETRYING...")
                time.sleep(2)


class ZoomableImageViewer(QGraphicsView):
    zoom_changed = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._pixmap_item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._pixmap_item)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#0d1117"))
        self.setFrameShape(QFrame.NoFrame)
        self._current_zoom = 1.0

    def set_image(self, jpg_path):
        if not jpg_path or not os.path.exists(jpg_path):
            self._pixmap_item.setPixmap(QPixmap())
            return
            
        pixmap = QPixmap(jpg_path)
        if not pixmap.isNull():
            self._pixmap_item.setPixmap(pixmap)
            self._scene.setSceneRect(QRectF(pixmap.rect()))
            self.fit_to_window()

    def fit_to_window(self):
        if not self._pixmap_item.pixmap().isNull():
            self.resetTransform()
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)
            self._current_zoom = 1.0
            self.zoom_changed.emit(self._current_zoom)

    def scale_by(self, factor):
        if not self._pixmap_item.pixmap().isNull():
            new_zoom = self._current_zoom * factor
            if 0.5 <= new_zoom <= 20.0:
                self.scale(factor, factor)
                self._current_zoom = new_zoom
                self.zoom_changed.emit(self._current_zoom)

    def wheelEvent(self, event):
        self.scale_by(1.25 if event.angleDelta().y() > 0 else 0.8)

    def mouseDoubleClickEvent(self, event):
        self.fit_to_window()
        super().mouseDoubleClickEvent(event)


class TacticalGCSWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SIYI ZR10 // Tactical Human Detection & Multi-Session GCS")
        self.resize(1400, 860)
        self.captures = {}
        self.current_coords = None
        self.active_session_dir = None

        self._apply_dark_theme()
        self._build_ui()
        
        # Debounced File Watcher
        self._sync_timer = QTimer()
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._sync_folder_state)
        self.watcher = QFileSystemWatcher()

        # Populate past sessions (Does not create a new one on startup)
        self._populate_sessions_dropdown()

        # Network Receiver targeting logic
        self.receiver = ReceiverThread()
        self.receiver.sync_requested.connect(self.trigger_sync)
        self.receiver.status_changed.connect(self.on_status_changed)
        self.receiver.session_created.connect(self.on_new_session_created)
        self.receiver.start()

        self.trigger_sync()

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #0f141c; color: #e6edf3; font-family: 'Segoe UI', 'Ubuntu', sans-serif; }
            QComboBox {
                background-color: #161b22; border: 1px solid #30363d; border-radius: 6px;
                padding: 5px 12px; color: #58a6ff; font-weight: bold; font-size: 13px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #161b22; selection-background-color: #1f6feb;
                selection-color: #ffffff; color: #e6edf3; border: 1px solid #30363d;
            }
            QListWidget { background-color: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 4px; font-size: 13px; outline: none; }
            QListWidget::item { padding: 12px; border-bottom: 1px solid #21262d; border-radius: 4px; }
            QListWidget::item:selected { background-color: #1f6feb; color: #ffffff; font-weight: bold; }
            QPushButton { background-color: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 8px 14px; color: #e6edf3; font-weight: 600; }
            QPushButton:hover { background-color: #30363d; border-color: #8b949e; }
            QPushButton#MapButton { background-color: #238636; border: 1px solid #2ea043; color: #ffffff; font-size: 14px; padding: 12px; font-weight: bold; }
            QPushButton#MapButton:hover { background-color: #2ea043; }
            QPushButton#MapButton:disabled { background-color: #21262d; border: 1px solid #30363d; color: #8b949e; }
            QPushButton#CoordsLink { text-align: left; background-color: #0d1117; border: 1px solid #1f6feb; color: #58a6ff; font-family: monospace; font-size: 14px; padding: 10px; }
            QPushButton#CoordsLink:disabled { border: 1px solid #30363d; color: #8b949e; }
            QFrame#Card { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; }
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        header = QFrame()
        header.setObjectName("Card")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)

        title_lbl = QLabel("SIYI ZR10 // MISSION ANALYZER")
        title_lbl.setFont(QFont("Segoe UI", 13, QFont.Bold))
        title_lbl.setStyleSheet("color: #58a6ff;")

        session_lbl = QLabel("SESSION:")
        session_lbl.setStyleSheet("font-weight: bold; color: #8b949e; margin-left: 20px;")

        self.session_selector = QComboBox()
        self.session_selector.currentIndexChanged.connect(self.on_session_changed)

        self.auto_select_cb = QCheckBox("Auto-Display Latest")
        self.auto_select_cb.setChecked(True)

        self.link_badge = QLabel("INITIALIZING...")
        self.link_badge.setStyleSheet("background-color: #1f2937; color: #3fb950; padding: 5px 12px; border-radius: 4px; font-weight: bold;")

        header_layout.addWidget(title_lbl)
        header_layout.addWidget(session_lbl)
        header_layout.addWidget(self.session_selector)
        header_layout.addStretch()
        header_layout.addWidget(self.auto_select_cb)
        header_layout.addSpacing(15)
        header_layout.addWidget(self.link_badge)
        main_layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)

        # Left: Detection List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        feed_title = QLabel("DETECTION LOG")
        feed_title.setStyleSheet("font-weight: bold; color: #8b949e;")
        self.capture_list = QListWidget()
        self.capture_list.currentItemChanged.connect(self.on_item_selected)
        left_layout.addWidget(feed_title)
        left_layout.addWidget(self.capture_list)

        # Center: Zoom Canvas
        center_card = QFrame()
        center_card.setObjectName("Card")
        center_layout = QVBoxLayout(center_card)
        center_layout.setContentsMargins(8, 8, 8, 8)

        self.viewer = ZoomableImageViewer()
        toolbar = QHBoxLayout()
        btn_zoom_in = QPushButton("Zoom In (+)")
        btn_zoom_out = QPushButton("Zoom Out (-)")
        btn_fit = QPushButton("Reset / Fit (Double-Click)")
        self.zoom_label = QLabel("Zoom: 1.0x  |  Scroll Wheel to Zoom, Drag to Pan")
        self.zoom_label.setStyleSheet("color: #8b949e;")

        btn_zoom_in.clicked.connect(lambda: self.viewer.scale_by(1.3))
        btn_zoom_out.clicked.connect(lambda: self.viewer.scale_by(0.77))
        btn_fit.clicked.connect(self.viewer.fit_to_window)
        self.viewer.zoom_changed.connect(lambda z: self.zoom_label.setText(f"Zoom: {z:.1f}x  |  Scroll Wheel to Zoom, Drag to Pan"))

        toolbar.addWidget(btn_zoom_in)
        toolbar.addWidget(btn_zoom_out)
        toolbar.addWidget(btn_fit)
        toolbar.addStretch()
        toolbar.addWidget(self.zoom_label)

        center_layout.addLayout(toolbar)
        center_layout.addWidget(self.viewer)

        # Right: Telemetry & Maps
        right_card = QFrame()
        right_card.setObjectName("Card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)

        gps_header = QLabel("AIRCRAFT GPS TELEMETRY (.TXT)")
        gps_header.setStyleSheet("font-weight: bold; color: #58a6ff; font-size: 13px;")
        right_layout.addWidget(gps_header)

        self.lbl_id = QLabel("Capture ID: --")
        self.lbl_time = QLabel("Timestamp: --")
        self.lbl_conf = QLabel("AI Confidence: --")
        self.lbl_alt = QLabel("Aircraft Altitude: --")
        self.lbl_hdg = QLabel("Aircraft Heading: --")

        for lbl in [self.lbl_id, self.lbl_time, self.lbl_conf, self.lbl_alt, self.lbl_hdg]:
            lbl.setStyleSheet("font-size: 13px; padding: 3px 0;")
            lbl.setWordWrap(True)
            right_layout.addWidget(lbl)

        coord_title = QLabel("CLICKABLE GPS COORDINATES:")
        coord_title.setStyleSheet("font-weight: bold; color: #8b949e; margin-top: 8px;")
        right_layout.addWidget(coord_title)

        self.btn_coords_link = QPushButton("NO DATA LOADED")
        self.btn_coords_link.setObjectName("CoordsLink")
        self.btn_coords_link.setCursor(QCursor(Qt.PointingHandCursor))
        self.btn_coords_link.clicked.connect(self.open_in_google_maps)
        self.btn_coords_link.setEnabled(False)
        right_layout.addWidget(self.btn_coords_link)

        self.btn_open_map = QPushButton("OPEN LOCATION IN GOOGLE MAPS")
        self.btn_open_map.setObjectName("MapButton")
        self.btn_open_map.setCursor(QCursor(Qt.PointingHandCursor))
        self.btn_open_map.clicked.connect(self.open_in_google_maps)
        self.btn_open_map.setEnabled(False)
        right_layout.addWidget(self.btn_open_map)

        raw_title = QLabel("RAW .TXT FILE CONTENT:")
        raw_title.setStyleSheet("font-weight: bold; color: #8b949e; margin-top: 10px;")
        right_layout.addWidget(raw_title)

        self.lbl_raw_txt = QLabel("")
        self.lbl_raw_txt.setStyleSheet("background-color: #0d1117; border: 1px solid #30363d; border-radius: 4px; padding: 10px; font-family: monospace; font-size: 12px; color: #c9d1d9;")
        self.lbl_raw_txt.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_raw_txt.setWordWrap(True)
        right_layout.addWidget(self.lbl_raw_txt, 1)

        splitter.addWidget(left_widget)
        splitter.addWidget(center_card)
        splitter.addWidget(right_card)
        splitter.setSizes([270, 760, 350])
        main_layout.addWidget(splitter, 1)

    def _update_watched_directory(self, folder_path):
        current_paths = self.watcher.directories()
        if current_paths:
            self.watcher.removePaths(current_paths)
        if folder_path and os.path.exists(folder_path):
            self.watcher.addPath(folder_path)
            # Reconnect signal dynamically safely
            try: self.watcher.directoryChanged.disconnect()
            except TypeError: pass
            self.watcher.directoryChanged.connect(self.trigger_sync)

    def _populate_sessions_dropdown(self):
        """Discovers existing session directories and defaults to the latest."""
        self.session_selector.blockSignals(True)
        self.session_selector.clear()

        all_entries = os.listdir(BASE_DATA_DIR)
        sessions = [
            d for d in all_entries 
            if os.path.isdir(os.path.join(BASE_DATA_DIR, d)) and d.startswith("session_")
        ]
        sessions.sort(reverse=True)

        for s in sessions:
            self.session_selector.addItem(s, os.path.join(BASE_DATA_DIR, s))

        if self.session_selector.count() > 0:
            self.session_selector.setCurrentIndex(0)
            self.active_session_dir = self.session_selector.currentData()
            self._update_watched_directory(self.active_session_dir)

        self.session_selector.blockSignals(False)

    def on_new_session_created(self, session_name):
        """Triggered by ReceiverThread when data sequence starts over."""
        self.session_selector.blockSignals(True)
        self.session_selector.insertItem(0, session_name, os.path.join(BASE_DATA_DIR, session_name))
        self.session_selector.setCurrentIndex(0)
        self.session_selector.blockSignals(False)
        self.on_session_changed(0)

    def on_session_changed(self, index):
        if index < 0: return
        selected_dir = self.session_selector.currentData()
        self.active_session_dir = selected_dir
        self._update_watched_directory(selected_dir)

        # Clear UI state for fresh session view
        self.captures.clear()
        self.capture_list.clear()
        self.viewer.set_image("")
        self.lbl_id.setText("Capture ID: --")
        self.lbl_raw_txt.setText("")
        self.btn_coords_link.setText("NO DATA LOADED")
        self.btn_coords_link.setEnabled(False)
        self.btn_open_map.setEnabled(False)

        self.trigger_sync()

    def trigger_sync(self):
        if self._sync_timer.isActive():
            self._sync_timer.stop()
        self._sync_timer.start(150)

    def _sync_folder_state(self):
        if not self.active_session_dir or not os.path.exists(self.active_session_dir): return
        
        current_files = set(os.listdir(self.active_session_dir))
        current_bases = {f[:-4] for f in current_files if f.lower().endswith(".jpg") and not f.endswith(".tmp")}
        
        items_to_remove = [seq_id for seq_id in self.captures.keys() if seq_id not in current_bases]
        for seq_id in items_to_remove:
            self.captures.pop(seq_id, None)
            for i in range(self.capture_list.count()):
                item = self.capture_list.item(i)
                if item and item.data(Qt.UserRole) == seq_id:
                    self.capture_list.takeItem(i)
                    break
                    
        if self.capture_list.count() == 0:
            self.viewer.set_image("")
            self.lbl_id.setText("Capture ID: --")
            self.lbl_raw_txt.setText("")
            self.btn_coords_link.setText("NO DATA LOADED")
            self.btn_coords_link.setEnabled(False)
            self.btn_open_map.setEnabled(False)

        sorted_bases = list(current_bases)
        sorted_bases.sort(key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else str(x)))

        last_added_item = None

        for base_name in sorted_bases:
            seq_id_str = str(base_name)
            jpg_p = os.path.join(self.active_session_dir, f"{base_name}.jpg")
            txt_p = os.path.join(self.active_session_dir, f"{base_name}.txt")
            
            info = parse_txt_file(txt_path=txt_p)
            lat, lon = info.get("latitude", "N/A"), info.get("longitude", "N/A")
            label_text = f"File: {seq_id_str} | GPS: {lat}, {lon}"

            if seq_id_str not in self.captures:
                self.captures[seq_id_str] = (jpg_p, txt_p)
                item = QListWidgetItem(label_text)
                item.setData(Qt.UserRole, seq_id_str)
                self.capture_list.addItem(item)
                last_added_item = item
            else:
                for i in range(self.capture_list.count()):
                    item = self.capture_list.item(i)
                    if item.data(Qt.UserRole) == seq_id_str:
                        item.setText(label_text)
                        if self.capture_list.currentItem() == item:
                            self.on_item_selected(item, None)
                        break

        if last_added_item and self.auto_select_cb.isChecked():
            self.capture_list.setCurrentItem(last_added_item)
        elif self.capture_list.count() > 0 and self.capture_list.currentItem() is None:
            self.capture_list.setCurrentItem(self.capture_list.item(0))

    def on_status_changed(self, status_text):
        self.link_badge.setText(status_text)

    def on_item_selected(self, current, previous):
        if not current: return
        seq_id = current.data(Qt.UserRole)
        if seq_id not in self.captures: return
            
        jpg_path, txt_path = self.captures[seq_id]
        self.viewer.set_image(jpg_path)

        info = parse_txt_file(txt_path)
        lat = info.get("latitude", "N/A")
        lon = info.get("longitude", "N/A")

        self.lbl_id.setText(f"Capture ID: {seq_id} ({os.path.basename(jpg_path)})")
        self.lbl_time.setText(f"Timestamp: {info.get('timestamp', 'N/A')}")
        
        conf_str = info.get('confidence', 'N/A')
        if conf_str != "N/A":
            try:
                self.lbl_conf.setText(f"AI Confidence: {float(conf_str) * 100:.1f}%")
            except ValueError:
                self.lbl_conf.setText(f"AI Confidence: {conf_str}")
        else:
            self.lbl_conf.setText("AI Confidence: N/A")

        self.lbl_alt.setText(f"Altitude: {info.get('altitude_rel_m', 'N/A')}m REL / {info.get('altitude_msl_m', 'N/A')}m MSL")
        self.lbl_hdg.setText(f"Heading: {info.get('heading_deg', 'N/A')}° ({info.get('gps_status', 'N/A')})")

        if lat == "N/A" or lon == "N/A":
            self.current_coords = None
            self.btn_coords_link.setText("NO GPS COORDINATES AVAILABLE")
            self.btn_coords_link.setEnabled(False)
            self.btn_open_map.setEnabled(False)
        else:
            self.current_coords = (lat, lon)
            self.btn_coords_link.setText(f"LAT: {lat}\nLON: {lon}  (Click to Map)")
            self.btn_coords_link.setEnabled(True)
            self.btn_open_map.setEnabled(True)

        self.lbl_raw_txt.setText(info.get("raw_text", "No Text Data Found."))

    def open_in_google_maps(self):
        if not self.current_coords: return
        lat, lon = self.current_coords
        webbrowser.open(f"https://www.google.com/maps?q={lat},{lon}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = TacticalGCSWindow()
    win.show()
    sys.exit(app.exec_())
