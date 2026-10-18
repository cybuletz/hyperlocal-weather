import urllib.request
import json
from datetime import datetime, timezone

def fetch_ensemble_forecast(lat: float, lon: float):
    """
    Pulls multi-model numerical weather predictions from Open-Meteo
    covering ECMWF IFS (9km), ICON-EU (7km), and GFS.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,precipitation,surface_pressure,cloud_cover,wind_speed_10m,wind_direction_10m"
        "&models=ecmwf_ifs025,icon_seamless,gfs_seamless"
        "&forecast_days=3&timezone=auto"
    )
    
    req = urllib.request.Request(url, headers={"User-Agent": "HyperLocalWeather/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode())

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return None

    # Parse and construct multi-model consensus
    forecast_timeline = []
    now_iso = datetime.now().isoformat()

    t_ecmwf = hourly.get("temperature_2m_ecmwf_ifs025", [])
    t_icon = hourly.get("temperature_2m_icon_seamless", [])
    t_gfs = hourly.get("temperature_2m_gfs_seamless", [])

    p_ecmwf = hourly.get("precipitation_probability_ecmwf_ifs025", [])
    p_icon = hourly.get("precipitation_probability_icon_seamless", [])
    p_gfs = hourly.get("precipitation_probability_gfs_seamless", [])

    rain_ecmwf = hourly.get("precipitation_ecmwf_ifs025", [])
    rain_icon = hourly.get("precipitation_icon_seamless", [])

    cloud_ecmwf = hourly.get("cloud_cover_ecmwf_ifs025", [])
    cloud_icon = hourly.get("cloud_cover_icon_seamless", [])

    wind_icon = hourly.get("wind_speed_10m_icon_seamless", [])
    wind_dir_icon = hourly.get("wind_direction_10m_icon_seamless", [])
    pressure_icon = hourly.get("surface_pressure_icon_seamless", [])

    for i, t_str in enumerate(times[:48]): # Next 48 hours
        t_vals = [v for v in [t_ecmwf[i] if i < len(t_ecmwf) else None,
                              t_icon[i] if i < len(t_icon) else None,
                              t_gfs[i] if i < len(t_gfs) else None] if v is not None]
        mean_temp = sum(t_vals) / len(t_vals) if t_vals else 15.0
        temp_spread = max(t_vals) - min(t_vals) if len(t_vals) >= 2 else 0.0

        p_vals = [v for v in [p_ecmwf[i] if i < len(p_ecmwf) else None,
                              p_icon[i] if i < len(p_icon) else None,
                              p_gfs[i] if i < len(p_gfs) else None] if v is not None]
        mean_precip_prob = int(round(sum(p_vals) / len(p_vals))) if p_vals else 0
        precip_spread = max(p_vals) - min(p_vals) if len(p_vals) >= 2 else 0

        mean_rain = max(rain_ecmwf[i] if i < len(rain_ecmwf) else 0.0,
                        rain_icon[i] if i < len(rain_icon) else 0.0)

        mean_cloud = int(round((cloud_ecmwf[i] + cloud_icon[i]) / 2.0)) if i < len(cloud_ecmwf) and i < len(cloud_icon) else 30

        # Determine HA condition string
        if mean_rain > 0.2 or mean_precip_prob > 60:
            condition = "rainy"
        elif mean_cloud > 75:
            condition = "cloudy"
        elif mean_cloud > 30:
            condition = "partlycloudy"
        else:
            condition = "sunny"

        forecast_timeline.append({
            "datetime": t_str,
            "temperature": round(mean_temp, 1),
            "precipitation_probability": mean_precip_prob,
            "precipitation": round(mean_rain, 1),
            "cloud_coverage": mean_cloud,
            "wind_speed": round(wind_icon[i], 1) if i < len(wind_icon) else 5.0,
            "wind_bearing": int(wind_dir_icon[i]) if i < len(wind_dir_icon) else 0,
            "pressure": round(pressure_icon[i], 1) if i < len(pressure_icon) else 1013.0,
            "condition": condition,
            "divergence": {
                "temp_spread": round(temp_spread, 1),
                "precip_spread": precip_spread,
                "ecmwf_temp": t_ecmwf[i] if i < len(t_ecmwf) else None,
                "icon_temp": t_icon[i] if i < len(t_icon) else None
            }
        })

    return forecast_timeline
