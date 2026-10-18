import json
import logging
import paho.mqtt.client as mqtt

logger = logging.getLogger("hyperlocal.mqtt")

class MqttPublisher:
    def __init__(self, host: str, port: int, user: str, password: str, discovery_prefix: str = "homeassistant", base_topic: str = "hyperlocal/weather"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.discovery_prefix = discovery_prefix
        self.base_topic = base_topic
        self.client = mqtt.Client(client_id="hyperlocal-weatherd")
        if user and password:
            self.client.username_pw_set(user, password)
        self.connected = False

    def start(self):
        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                logger.info("Connected successfully to Mosquitto MQTT broker.")
                self.connected = True
                self.publish_discovery()
            else:
                logger.error(f"MQTT connection failed with code {rc}")

        self.client.on_connect = on_connect
        try:
            self.client.connect(self.host, self.port, 60)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker {self.host}:{self.port}: {e}")

    def publish_discovery(self):
        dev = {
            "identifiers": ["hyperlocal_weather_pve123"],
            "name": "HyperLocal Meteorological Engine",
            "model": "HP2551-Kalman-AI-Pro",
            "manufacturer": "Proxmox Micro-Climate Daemon"
        }

        # 1. Main Calibrated Weather Sensor
        weather_hub = {
            "name": "HyperLocal Calibrated Weather",
            "unique_id": "hyperlocal_calibrated_weather",
            "state_topic": f"{self.base_topic}/state",
            "json_attributes_topic": f"{self.base_topic}/attributes",
            "icon": "mdi:weather-partly-cloudy",
            "device": dev
        }
        self.client.publish(f"{self.discovery_prefix}/sensor/hyperlocal_calibrated_weather/config", json.dumps(weather_hub), retain=True)

        # 2. MyHome Native Weather Sensor (Populates custom:myhome-outdoor-weather-v2)
        myhome_weather = {
            "name": "MyHome Weather",
            "unique_id": "sensor_myhome_weather",
            "state_topic": f"{self.base_topic}/myhome/state",
            "json_attributes_topic": f"{self.base_topic}/myhome/attributes",
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "icon": "mdi:weather-partly-cloudy",
            "device": dev
        }
        self.client.publish(f"{self.discovery_prefix}/sensor/myhome_weather/config", json.dumps(myhome_weather), retain=True)

        # 3. Intelligence & advisory sensors
        sensors = [
            ("ai_reasoning", "HyperLocal AI Synthesis", "mdi:brain", None, None),
            ("ai_arbitration", "HyperLocal Model Arbitration", "mdi:scale-balance", None, None),
            ("confidence_score", "HyperLocal Forecast Confidence", "mdi:percent-outline", None, "%"),
            ("smart_home_advice", "HyperLocal Smart Home Advice", "mdi:home-lightning-bolt", None, None),
            ("zambretti_trend", "HyperLocal Zambretti Forecast", "mdi:chart-timeline-variant", None, None),
            ("pressure_tendency_3h", "HyperLocal 3h Pressure Tendency", "mdi:gauge", None, "hPa"),
            ("clearness_index", "HyperLocal Clearness Index Kt", "mdi:white-balance-sunny", None, None),
            ("cloud_base_m", "HyperLocal Cloud Base LCL", "mdi:clouds", "distance", "m"),
            ("rain_onset_eta", "HyperLocal Rain Onset ETA", "mdi:weather-rainy", None, None),
            ("human_outfit_tip", "Daily Outfit & Outdoor Advisory", "mdi:tshirt-crew", None, None),
            ("umbrella_needed", "Umbrella Advisory", "mdi:umbrella", None, None),
            ("car_wash_index", "Car Wash Advisory", "mdi:car-wash", None, None),
            ("sky_camera_cloud_cover", "Frigate Sky Optical Cloud Cover", "mdi:camera-metering-center", None, "%"),
            ("sky_camera_condition", "Frigate Sky Visual Condition", "mdi:weather-partly-cloudy", None, None)
        ]

        for sid, sname, icon, dev_class, unit in sensors:
            cfg = {
                "name": sname,
                "unique_id": f"hyperlocal_{sid}",
                "state_topic": f"{self.base_topic}/sensor/{sid}/state",
                "json_attributes_topic": f"{self.base_topic}/sensor/{sid}/attributes",
                "icon": icon,
                "device": dev
            }
            if unit:
                cfg["unit_of_measurement"] = unit
            if dev_class:
                cfg["device_class"] = dev_class

            self.client.publish(f"{self.discovery_prefix}/sensor/hyperlocal_{sid}/config", json.dumps(cfg), retain=True)

        # 4. Binary Safety Alerts
        binary_sensors = [
            ("storm_alert", "HyperLocal Storm & Squall Alert", "mdi:weather-lightning", "safety"),
            ("fog_alert", "HyperLocal Radiation Fog Alert", "mdi:weather-fog", "safety"),
            ("visual_fog_alert", "Frigate Optical Fog & Haze Alert", "mdi:weather-fog", "safety")
        ]

        for bid, bname, icon, dev_class in binary_sensors:
            cfg = {
                "name": bname,
                "unique_id": f"hyperlocal_{bid}",
                "state_topic": f"{self.base_topic}/binary_sensor/{bid}/state",
                "json_attributes_topic": f"{self.base_topic}/binary_sensor/{bid}/attributes",
                "icon": icon,
                "device_class": dev_class,
                "device": dev
            }
            self.client.publish(f"{self.discovery_prefix}/binary_sensor/hyperlocal_{bid}/config", json.dumps(cfg), retain=True)

        logger.info("MQTT discovery payloads published.")

    def publish_state(self, condition: str, attributes: dict):
        self.client.publish(f"{self.base_topic}/state", condition, retain=True)
        self.client.publish(f"{self.base_topic}/attributes", json.dumps(attributes), retain=True)

    def publish_myhome_weather(self, temp: float, attributes: dict):
        self.client.publish(f"{self.base_topic}/myhome/state", str(temp), retain=True)
        self.client.publish(f"{self.base_topic}/myhome/attributes", json.dumps(attributes), retain=True)

    def publish_sensor(self, sensor_id: str, value, attributes: dict = None):
        self.client.publish(f"{self.base_topic}/sensor/{sensor_id}/state", str(value), retain=True)
        if attributes:
            self.client.publish(f"{self.base_topic}/sensor/{sensor_id}/attributes", json.dumps(attributes), retain=True)

    def publish_binary_sensor(self, binary_id: str, is_on: bool, attributes: dict = None):
        val = "ON" if is_on else "OFF"
        self.client.publish(f"{self.base_topic}/binary_sensor/{binary_id}/state", val, retain=True)
        if attributes:
            self.client.publish(f"{self.base_topic}/binary_sensor/{binary_id}/attributes", json.dumps(attributes), retain=True)
