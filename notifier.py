import time
import json
import logging
import urllib.request
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

logger = logging.getLogger("HyperLocalNotifier")

class WeatherNotifier:
    def __init__(self, ha_url: str, ha_token: str, state_file: str = "/opt/hyperlocal-weather/data/notifier_state.json", timezone_str: str = "Europe/Bucharest"):
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token
        self.state_file = state_file
        self.timezone_str = timezone_str
        try:
            self.tz = ZoneInfo(timezone_str) if ZoneInfo else None
        except Exception:
            self.tz = None
        self.state = {
            "last_morning_date": "",
            "last_rain_alert": 0,
            "last_storm_alert": 0,
            "last_frost_alert": 0
        }
        self.load_state()

    def load_state(self):
        try:
            with open(self.state_file, "r") as f:
                self.state.update(json.load(f))
        except Exception:
            pass

    def save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f)
        except Exception as e:
            logger.error(f"Failed to save notifier state: {e}")

    def send_ha_service(self, domain: str, service: str, data: dict):
        url = f"{self.ha_url}/api/services/{domain}/{service}"
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.ha_token}",
                "Content-Type": "application/json"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in (200, 201):
                    logger.info(f"Successfully called {domain}.{service}")
                    return True
        except Exception as e:
            logger.error(f"Error calling {domain}.{service}: {e}")
        return False

    def check_and_dispatch(self, telemetry: dict, ai_state: dict, radar_nowcast: dict, alerts: dict, cached_forecast: list):
        now_ts = time.time()
        now_dt = datetime.now(self.tz) if self.tz else datetime.now()
        today_str = now_dt.strftime("%Y-%m-%d")

        t_out = telemetry.get("outdoor_temperature")
        wind_gst = telemetry.get("wind_gust") or 0.0
        rain_rate = telemetry.get("rain_rate") or 0.0

        # 1. Scheduled Morning Weekday Briefing at 08:00 (Mon=0 .. Fri=4)
        # Fires on the first cycle at or after 08:00 AM local time
        if now_dt.weekday() < 5 and now_dt.hour >= 8:
            if self.state.get("last_morning_date") != today_str:
                afternoon_max = t_out or 15.0
                if cached_forecast:
                    temps = [f.get("temperature", t_out) for f in cached_forecast[:10] if f.get("temperature") is not None]
                    if temps:
                        afternoon_max = max(temps)

                msg = (
                    f"{ai_state.get('human_summary', 'Calm conditions outside.')}\n\n"
                    f"🌡️ Morning: {round(t_out, 1) if t_out is not None else '—'}°C | Afternoon Peak: {round(afternoon_max, 1)}°C\n"
                    f"👕 Outfit: {ai_state.get('outfit_tip', 'Warm layers')}\n"
                    f"☂️ Umbrella: {ai_state.get('umbrella', 'Not needed')}\n"
                    f"🚗 Car Wash: {ai_state.get('car_wash', 'Safe to wash')}\n"
                    f"🏡 Living Tip: {ai_state.get('smart_home_tip', 'Check ventilation.')}"
                )

                # Send to Xiaomi 15
                self.send_ha_service("notify", "mobile_app_xiaomi15", {
                    "title": "☀️ Morning Weather Concierge",
                    "message": msg,
                    "data": {
                        "clickAction": "/dashboard-mobile/weather",
                        "tag": "weather_morning_briefing"
                    }
                })

                # Send to Persistent Notification
                self.send_ha_service("persistent_notification", "create", {
                    "title": "☀️ Morning Weather Concierge",
                    "message": msg,
                    "notification_id": "weather_morning_briefing"
                })

                self.state["last_morning_date"] = today_str
                self.save_state()
                logger.info(f"Dispatched Weekday Morning Briefing at {now_dt.strftime('%H:%M:%S')} local time from Proxmox CT 123.")

        # 2. Imminent Rain Alert (Approaching radar within 15-45m, not raining yet)
        if radar_nowcast.get("status") == "approaching" and rain_rate <= 0.1:
            if now_ts - self.state.get("last_rain_alert", 0) > 3600:
                eta_min = radar_nowcast.get("eta_minutes", 20)
                desc = radar_nowcast.get("description", f"Rain expected in ~{eta_min}m")
                umbrella = ai_state.get("umbrella", "Keep handy")
                
                title = f"🌧️ Rain Approaching in ~{eta_min} min"
                msg = f"{desc}\n\n☂️ Umbrella: {umbrella}\n💡 Advice: Close roof windows and check outdoor spaces."

                self.send_ha_service("notify", "mobile_app_xiaomi15", {
                    "title": title,
                    "message": msg,
                    "data": {
                        "clickAction": "/dashboard-mobile/weather",
                        "tag": "weather_rain_alert",
                        "group": "weather_alerts"
                    }
                })
                self.send_ha_service("persistent_notification", "create", {
                    "title": title,
                    "message": msg,
                    "notification_id": "weather_rain_alert"
                })

                self.state["last_rain_alert"] = now_ts
                self.save_state()
                logger.info(f"Dispatched Imminent Rain Alert (ETA ~{eta_min}m) from Proxmox CT 123.")

        # 3. Severe Storm & Squall Alert (Barometric drop or high wind gust)
        if alerts.get("storm_alert") or wind_gst > 45.0:
            if now_ts - self.state.get("last_storm_alert", 0) > 3600:
                reason = alerts.get("storm_reason") or f"High wind gust ({round(wind_gst, 1)} km/h) detected."
                title = "⛈️ Severe Weather & Squall Alert"
                msg = (
                    f"Severe weather detected!\n"
                    f"💨 Wind Gust: {round(wind_gst, 1)} km/h\n"
                    f"📉 Details: {reason}\n\n"
                    f"⚠️ Recommendation: Secure patio furniture, awnings, and loose outdoor items."
                )

                self.send_ha_service("notify", "mobile_app_xiaomi15", {
                    "title": title,
                    "message": msg,
                    "data": {
                        "importance": "high",
                        "priority": "high",
                        "clickAction": "/dashboard-mobile/weather",
                        "tag": "weather_storm_alert",
                        "group": "weather_alerts"
                    }
                })
                self.send_ha_service("persistent_notification", "create", {
                    "title": title,
                    "message": msg,
                    "notification_id": "weather_storm_alert"
                })

                self.state["last_storm_alert"] = now_ts
                self.save_state()
                logger.info("Dispatched Severe Storm Alert from Proxmox CT 123.")

        # 4. Frost & Freeze Warning (Outdoor temp < 2.0°C)
        if t_out is not None and t_out < 2.0:
            if now_ts - self.state.get("last_frost_alert", 0) > 43200:
                title = f"❄️ Frost & Freeze Warning ({round(t_out, 1)}°C)"
                msg = (
                    f"Outdoor temperature has dropped to {round(t_out, 1)}°C.\n\n"
                    f"🚗 Watch out for black ice on roadways and frozen windshields.\n"
                    f"🌱 Protect frost-sensitive plants and outdoor water taps."
                )

                self.send_ha_service("notify", "mobile_app_xiaomi15", {
                    "title": title,
                    "message": msg,
                    "data": {
                        "clickAction": "/dashboard-mobile/weather",
                        "tag": "weather_frost_alert",
                        "group": "weather_alerts"
                    }
                })
                self.send_ha_service("persistent_notification", "create", {
                    "title": title,
                    "message": msg,
                    "notification_id": "weather_frost_alert"
                })

                self.state["last_frost_alert"] = now_ts
                self.save_state()
                logger.info(f"Dispatched Frost Advisory ({round(t_out, 1)}°C) from Proxmox CT 123.")
