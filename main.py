import json
import time
import math
import logging
from datetime import datetime
import urllib.request

from physics import (
    calculate_barometric_tendency,
    calculate_zambretti,
    calculate_solar_clear_sky,
    calculate_clearness_index,
    calculate_cloud_base_lcl,
    check_storm_and_fog_alerts
)
from kalman import BiasKalmanFilter
from nwp_client import fetch_ensemble_forecast
from radar_nowcast import get_precipitation_nowcast
from ai_resonance import run_ai_resonance
from mqtt_publisher import MqttPublisher
from notifier import WeatherNotifier
from sky_vision import SkyVisionAnalyzer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("hyperlocal.main")

def load_env(env_path=None):
    import os
    if not env_path:
        for p in [os.environ.get("ENV_FILE"), ".env", "/opt/hyperlocal-weather/.env"]:
            if p and os.path.exists(p):
                env_path = p
                break
    if env_path and os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

def load_config():
    import os
    load_env()
    cfg = {}
    config_path = os.environ.get("CONFIG_PATH", "config.json" if os.path.exists("config.json") else "/opt/hyperlocal-weather/config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    if "homeassistant" not in cfg:
        cfg["homeassistant"] = {}
    cfg["homeassistant"]["url"] = os.environ.get("HA_URL", cfg["homeassistant"].get("url", "http://homeassistant.local:8123"))
    cfg["homeassistant"]["token"] = os.environ.get("HA_TOKEN", cfg["homeassistant"].get("token", ""))

    if "mqtt" not in cfg:
        cfg["mqtt"] = {}
    cfg["mqtt"]["host"] = os.environ.get("MQTT_HOST", cfg["mqtt"].get("host", "homeassistant.local"))
    cfg["mqtt"]["port"] = int(os.environ.get("MQTT_PORT", cfg["mqtt"].get("port", 1883)))
    cfg["mqtt"]["username"] = os.environ.get("MQTT_USERNAME", cfg["mqtt"].get("username", ""))
    cfg["mqtt"]["password"] = os.environ.get("MQTT_PASSWORD", cfg["mqtt"].get("password", ""))
    cfg["mqtt"]["discovery_prefix"] = os.environ.get("MQTT_DISCOVERY_PREFIX", cfg["mqtt"].get("discovery_prefix", "homeassistant"))
    cfg["mqtt"]["base_topic"] = os.environ.get("MQTT_BASE_TOPIC", cfg["mqtt"].get("base_topic", "hyperlocal/weather"))

    if "ollama" not in cfg:
        cfg["ollama"] = {}
    cfg["ollama"]["url"] = os.environ.get("OLLAMA_URL", cfg["ollama"].get("url", "http://localhost:11434"))
    cfg["ollama"]["model"] = os.environ.get("OLLAMA_MODEL", cfg["ollama"].get("model", "llama3.2:1b"))

    if "frigate" not in cfg:
        cfg["frigate"] = {}
    cfg["frigate"]["url"] = os.environ.get("FRIGATE_URL", cfg["frigate"].get("url", "http://homeassistant.local5:5000"))

    return cfg

def fetch_ha_states(ha_url: str, ha_token: str, entity_map: dict):
    telemetry = {}
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json"
    }
    for key, entity_id in entity_map.items():
        try:
            req = urllib.request.Request(f"{ha_url}/api/states/{entity_id}", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                state_str = data.get("state")
                if state_str not in ["unavailable", "unknown", None]:
                    try:
                        telemetry[key] = float(state_str)
                    except ValueError:
                        telemetry[key] = state_str
                else:
                    telemetry[key] = None
        except Exception as e:
            logger.debug(f"Error reading {entity_id}: {e}")
            telemetry[key] = None
    return telemetry

def main():
    logger.info("Starting HyperLocal Weather Daemon (Physics + Kalman + Radar + SkyVision + AI Resonance)...")
    config = load_config()

    mqtt_cfg = config["mqtt"]
    publisher = MqttPublisher(
        host=mqtt_cfg["host"],
        port=mqtt_cfg["port"],
        user=mqtt_cfg.get("username"),
        password=mqtt_cfg.get("password"),
        discovery_prefix=mqtt_cfg.get("discovery_prefix", "homeassistant"),
        base_topic=mqtt_cfg.get("base_topic", "hyperlocal/weather")
    )
    publisher.start()

    temp_kalman = BiasKalmanFilter(process_noise=0.03, measurement_noise=0.3)
    rh_kalman = BiasKalmanFilter(process_noise=0.05, measurement_noise=0.8)

    loc = config["location"]
    lat, lon = loc["latitude"], loc["longitude"]

    ha_cfg = config["homeassistant"]
    ha_url = ha_cfg["url"]
    ha_token = ha_cfg["token"]
    notifier = WeatherNotifier(ha_url, ha_token)
    sky_analyzer = SkyVisionAnalyzer()
    
    entity_map = config["entities"]
    if "feels_like" not in entity_map:
        entity_map["feels_like"] = "sensor.hp2551ae_pro_v2_0_0_feels_like_temperature"

    ollama_cfg = config.get("ollama", {})
    ollama_url = ollama_cfg.get("url", "http://localhost:11434")
    ollama_model = ollama_cfg.get("model", "llama3.2:1b")

    pressure_history = []
    cached_forecast = []
    last_nwp_fetch = 0
    last_ai_resonance = 0
    sky_res = {
        "status": "ok",
        "cloud_cover_pct": 0.0,
        "oktas": 0,
        "condition": "Analyzing...",
        "luminance": 100.0,
        "contrast_std": 30.0,
        "is_night": False,
        "fog_detected": False,
        "primary_camera": "outside_sky_cam"
    }
    ai_state = {
        "human_summary": "Calm conditions outside. Microclimate telemetry fully synchronized.",
        "outfit_tip": "Warm jacket",
        "umbrella": "Keep handy",
        "car_wash": "Hold off (rain)",
        "smart_home_tip": "Keep windows closed to retain indoor warmth tonight.",
        "confidence_score": 90
    }

    while True:
        now_ts = time.time()
        now_dt = datetime.now()

        # 1. Fetch live HP2551 telemetry from Home Assistant
        telemetry = fetch_ha_states(ha_url, ha_token, entity_map)
        p_rel = telemetry.get("relative_pressure")
        t_out = telemetry.get("outdoor_temperature")
        t_feels = telemetry.get("feels_like")
        rh_out = telemetry.get("outdoor_humidity")
        dew_out = telemetry.get("dewpoint")
        solar = telemetry.get("solar_radiation")
        wind_spd = telemetry.get("wind_speed")
        wind_gst = telemetry.get("wind_gust")
        wind_dir = telemetry.get("wind_direction")
        rain_rate = telemetry.get("rain_rate", 0.0)
        daily_rain = telemetry.get("daily_rain", 0.0)
        vpd = telemetry.get("vpd")

        if p_rel is not None:
            pressure_history.append((now_ts, p_rel))
            cutoff = now_ts - 14400
            pressure_history = [p for p in pressure_history if p[0] >= cutoff]

        # 2. Atmospheric Physics calculations
        dp_3h, dp_desc = calculate_barometric_tendency(pressure_history)
        zambretti = calculate_zambretti(p_rel, dp_3h, wind_dir, now_dt.month)
        ghi_clear = calculate_solar_clear_sky(lat, lon, now_dt)
        clearness_idx = calculate_clearness_index(solar, ghi_clear)
        cloud_base_m = calculate_cloud_base_lcl(t_out, dew_out)
        alerts = check_storm_and_fog_alerts(p_rel, dp_3h, wind_spd, wind_gst, t_out, dew_out, now_dt.hour)

        # 2b. Optical Sky Vision (Frigate AI Camera Analysis)
        try:
            sky_res = sky_analyzer.analyze_sky(outdoor_humidity=rh_out if rh_out is not None else 50.0)
        except Exception as e:
            logger.error(f"Error analyzing sky vision: {e}")

        # 3. NWP & Radar Fetch (every 10 minutes = 600s)
        if now_ts - last_nwp_fetch >= 600 or not cached_forecast:
            try:
                raw_forecast = fetch_ensemble_forecast(lat, lon)
                if raw_forecast:
                    h0_temp = raw_forecast[0]["temperature"]
                    temp_bias = temp_kalman.update(t_out, h0_temp)
                    logger.info(f"Kalman Temperature Bias updated: {temp_bias}C (Station: {t_out}C, Model 0h: {h0_temp}C)")

                    for hour_idx, entry in enumerate(raw_forecast):
                        decay = math.exp(-0.08 * hour_idx)
                        entry["temperature"] = round(entry["temperature"] + (temp_bias * decay), 1)

                    cached_forecast = raw_forecast
                    last_nwp_fetch = now_ts
            except Exception as e:
                logger.error(f"Error updating NWP ensemble: {e}")

        # 4. Radar Nowcasting (every loop)
        radar_nowcast = get_precipitation_nowcast(lat, lon, rain_rate, vpd)

        # 5. AI Resonance (Every 2 hours or immediate on start or severe trigger)
        trigger_ai = False
        if last_ai_resonance == 0:
            trigger_ai = True
        elif now_ts - last_ai_resonance >= 7200:
            trigger_ai = True
        elif abs(dp_3h) >= 1.5 and (now_ts - last_ai_resonance >= 1800):
            trigger_ai = True
        elif radar_nowcast.get("status") == "approaching" and (now_ts - last_ai_resonance >= 1800):
            trigger_ai = True

        if trigger_ai:
            nwp_summary = {
                "spread_summary": f"Temp spread: {cached_forecast[0].get('divergence', {}).get('temp_spread', 0)}C" if cached_forecast else "Normal spread"
            }
            phys_telemetry = {
                "temperature": t_out,
                "feels_like": t_feels,
                "humidity": rh_out,
                "dewpoint": dew_out,
                "relative_pressure": p_rel,
                "pressure_tendency_3h": dp_3h,
                "pressure_tendency_desc": dp_desc,
                "wind_speed": wind_spd,
                "wind_gust": wind_gst,
                "wind_direction": wind_dir,
                "solar_radiation": solar,
                "clearness_index": clearness_idx,
                "vpd": vpd
            }
            logger.info("Triggering AI Resonance Concierge with local Ollama...")
            ai_res = run_ai_resonance(ollama_url, ollama_model, phys_telemetry, nwp_summary, radar_nowcast, zambretti, sky_vision=sky_res)
            if ai_res:
                ai_state = ai_res
                last_ai_resonance = now_ts
                logger.info(f"AI Concierge complete: {ai_state.get('human_summary')}")

        # 6. Publish to Home Assistant MQTT
        current_condition = "rainy" if rain_rate and rain_rate > 0.1 else (cached_forecast[0]["condition"] if cached_forecast else "partlycloudy")
        weather_attributes = {
            "temperature": t_out,
            "humidity": rh_out,
            "pressure": p_rel,
            "wind_speed": wind_spd,
            "wind_bearing": wind_dir,
            "visibility": 10.0,
            "forecast": cached_forecast[:24] if cached_forecast else []
        }
        publisher.publish_state(current_condition, weather_attributes)

        # MyHome Native Weather Sensor
        myhome_attrs = {
            "temperature": t_out,
            "feels_like": t_feels if t_feels is not None else t_out,
            "wind_speed_kmh": wind_spd,
            "wind_bearing": wind_dir,
            "humidity": rh_out,
            "dew_point": dew_out,
            "pressure": p_rel,
            "rain_today": daily_rain,
            "rain_rate": rain_rate,
            "summary": ai_state.get("human_summary"),
            "outfit": ai_state.get("outfit_tip"),
            "umbrella": ai_state.get("umbrella"),
            "car_wash": ai_state.get("car_wash")
        }
        publisher.publish_myhome_weather(t_out if t_out is not None else 12.0, myhome_attrs)

        # Human-Centric Concierge Sensors
        publisher.publish_sensor("ai_reasoning", ai_state.get("human_summary", "Calm conditions outside."))
        publisher.publish_sensor("human_outfit_tip", ai_state.get("outfit_tip", "Warm jacket"))
        publisher.publish_sensor("umbrella_needed", ai_state.get("umbrella", "Keep handy"))
        publisher.publish_sensor("car_wash_index", ai_state.get("car_wash", "Hold off (rain)"))
        publisher.publish_sensor("smart_home_advice", ai_state.get("smart_home_tip", "Keep windows closed."))
        publisher.publish_sensor("confidence_score", ai_state.get("confidence_score", 90))

        # Atmospheric Physics & Radar Sensors
        publisher.publish_sensor("zambretti_trend", zambretti)
        publisher.publish_sensor("pressure_tendency_3h", dp_3h, {"description": dp_desc})
        publisher.publish_sensor("clearness_index", clearness_idx if clearness_idx is not None else 0.0, {"theoretical_clear_sky": ghi_clear})
        publisher.publish_sensor("cloud_base_m", cloud_base_m if cloud_base_m is not None else 0)
        
        # Optical Sky Camera Sensors
        publisher.publish_sensor("sky_camera_cloud_cover", sky_res.get("cloud_cover_pct", 0.0), sky_res)
        publisher.publish_sensor("sky_camera_condition", sky_res.get("condition", "Unknown"))
        publisher.publish_binary_sensor("visual_fog_alert", sky_res.get("fog_detected", False), {"camera": sky_res.get("primary_camera", "outside_sky_cam")})

        # Publish punchy badge as state and full details as attributes
        radar_badge = radar_nowcast.get("badge", radar_nowcast.get("description", "Clear (next 2h)"))
        publisher.publish_sensor("rain_onset_eta", radar_badge, radar_nowcast)

        # Binary Alerts
        publisher.publish_binary_sensor("storm_alert", alerts["storm_alert"], {"reason": alerts["storm_reason"]})
        publisher.publish_binary_sensor("fog_alert", alerts["fog_alert"], {"reason": alerts["fog_reason"]})

        # 6b. Export live telemetry for local mini web server
        try:
            import os
            web_data = {
                "timestamp": now_ts,
                "datetime": now_dt.isoformat(),
                "temperature": t_out,
                "feels_like": t_feels,
                "humidity": rh_out,
                "dewpoint": dew_out,
                "pressure": p_rel,
                "pressure_tendency_3h": dp_3h,
                "pressure_tendency_desc": dp_desc,
                "wind_speed": wind_spd,
                "wind_gust": wind_gst,
                "wind_direction": wind_dir,
                "solar_radiation": solar,
                "clearness_index": clearness_idx,
                "cloud_base_m": cloud_base_m,
                "rain_rate": rain_rate,
                "daily_rain": daily_rain,
                "zambretti": zambretti,
                "sky_vision": sky_res,
                "radar_nowcast": radar_nowcast,
                "ai_state": ai_state,
                "alerts": alerts
            }
            telemetry_dir = os.environ.get("TELEMETRY_DIR", "/var/www/weather/api" if os.path.exists("/var/www/weather") else "web/api")
            os.makedirs(telemetry_dir, exist_ok=True)
            tmp_path = os.path.join(telemetry_dir, "telemetry.json.tmp")
            final_path = os.path.join(telemetry_dir, "telemetry.json")
            with open(tmp_path, "w") as f:
                json.dump(web_data, f, indent=2)
            os.replace(tmp_path, final_path)
        except Exception as e:
            logger.debug(f"Error saving web telemetry: {e}")

        # 7. Dispatched Proxmox Autonomous Notifications (Weekday 8AM, Rain Nowcast, Squalls, Frost)
        try:
            notifier.check_and_dispatch(telemetry, ai_state, radar_nowcast, alerts, cached_forecast)
        except Exception as e:
            logger.error(f"Error checking notifier: {e}")

        # Sleep before next cycle (30 seconds)
        time.sleep(30)

if __name__ == "__main__":
    main()
