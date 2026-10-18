import math
from datetime import datetime, timezone

def calculate_solar_clear_sky(latitude: float, longitude: float, dt: datetime = None) -> float:
    """
    Estimates theoretical clear-sky Global Horizontal Irradiance (GHI in W/m2)
    using the Haurwitz solar position model.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Day of year
    day_of_year = dt.timetuple().tm_yday
    hour_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0

    # Solar declination (degrees)
    declination = 23.45 * math.sin(math.radians((360.0 / 365.0) * (284 + day_of_year)))

    # Equation of time (minutes)
    b = math.radians((360.0 / 365.0) * (day_of_year - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)

    # Solar time
    solar_time = hour_utc + (4.0 * longitude + eot) / 60.0
    hour_angle = 15.0 * (solar_time - 12.0)

    # Solar elevation angle
    lat_rad = math.radians(latitude)
    dec_rad = math.radians(declination)
    ha_rad = math.radians(hour_angle)

    sin_elevation = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    if sin_elevation <= 0.02:
        return 0.0

    elevation_deg = math.degrees(math.asin(sin_elevation))

    # Extraterrestrial irradiance (Solar constant adjusted for orbital eccentricity)
    i0 = 1367.0 * (1.0 + 0.033 * math.cos(math.radians(360.0 * day_of_year / 365.0)))

    # Haurwitz clear-sky parameterization
    airmass = 1.0 / max(0.02, sin_elevation)
    ghi_clear = max(0.0, 1098.0 * math.sin(math.radians(elevation_deg)) * math.exp(-0.057 * airmass))
    return round(min(ghi_clear, i0), 1)

def calculate_clearness_index(measured_solar: float, clear_sky_solar: float):
    """
    Calculates the Clearness Index Kt (0.0 to 1.0).
    Only valid during daylight when clear_sky_solar > 30 W/m2.
    """
    if clear_sky_solar < 30.0 or measured_solar is None:
        return None
    kt = measured_solar / max(1.0, clear_sky_solar)
    return round(max(0.0, min(1.0, kt)), 3)

def calculate_cloud_base_lcl(temp_c: float, dewpoint_c: float) -> int:
    """
    Calculates Lifting Condensation Level (LCL cloud base height in meters)
    via Espy's equation.
    """
    if temp_c is None or dewpoint_c is None:
        return None
    depression = max(0.0, temp_c - dewpoint_c)
    return int(round(125.0 * depression))

def calculate_barometric_tendency(history_records: list):
    """
    Calculates 3-hour pressure rate of change and trend description.
    history_records: list of (unix_ts, p_rel)
    """
    if not history_records or len(history_records) < 2:
        return 0.0, "Steady"

    now_ts = history_records[-1][0]
    p_now = history_records[-1][1]

    # Find reading closest to 3 hours ago (10800 seconds)
    target_ts = now_ts - 10800
    closest = min(history_records, key=lambda x: abs(x[0] - target_ts))
    
    elapsed_hours = max(0.5, (now_ts - closest[0]) / 3600.0)
    dp_normalized_3h = round((p_now - closest[1]) * (3.0 / elapsed_hours), 2)

    if dp_normalized_3h >= 2.0:
        desc = "Rising very rapidly"
    elif dp_normalized_3h >= 1.2:
        desc = "Rising rapidly"
    elif dp_normalized_3h >= 0.5:
        desc = "Rising steadily"
    elif dp_normalized_3h <= -2.0:
        desc = "Falling very rapidly"
    elif dp_normalized_3h <= -1.2:
        desc = "Falling rapidly"
    elif dp_normalized_3h <= -0.5:
        desc = "Falling steadily"
    else:
        desc = "Steady"

    return dp_normalized_3h, desc

def calculate_zambretti(p_rel: float, dp_3h: float, wind_deg: float, month: int) -> str:
    """
    Modernized Negretti & Zambra barometric forecasting algorithm.
    """
    if p_rel is None:
        return "Unknown"

    # Wind quadrant correction
    # North/East winds increase pressure tendency weight, South/West lower it
    wind_factor = 0.0
    if wind_deg is not None:
        if 45 <= wind_deg < 135:     # East / SE
            wind_factor = -1.0
        elif 135 <= wind_deg < 225:   # South / SW
            wind_factor = -2.0
        elif 225 <= wind_deg < 315:   # West / NW
            wind_factor = +1.0
        else:                         # North / NE
            wind_factor = +2.0

    # Season check: Winter (Oct - Mar)
    is_winter = month in [10, 11, 12, 1, 2, 3]

    p_norm = p_rel + wind_factor

    z_lookup = {
        1: "Settled fine weather",
        2: "Fine weather",
        3: "Fine, becoming less settled",
        4: "Fairly fine, improving",
        5: "Fairly fine, showers early",
        6: "Fairly fine, showery later",
        7: "Showery early, improving",
        8: "Changeable, mending",
        9: "Fairly fine, showers likely",
        10: "Showers becoming less frequent",
        11: "Changeable, some rain",
        12: "Fairly fine, showers likely",
        13: "Showery, unsettled later",
        14: "Rain at times, worse later",
        15: "Showers, becoming unsettled",
        16: "Changeable, some rain likely",
        17: "Showery, some rain at times",
        18: "Rain at times, becoming very unsettled",
        19: "Unsettled, rain at times",
        20: "Rain at frequent intervals",
        21: "Very unsettled, rain",
        22: "Stormy, much rain",
        23: "Frequent rain, stormy later",
        24: "Rain at frequent intervals, squalls",
        25: "Very stormy, severe gale warning",
        26: "Violent storm imminent"
    }

    if dp_3h >= 0.5: # Rising
        z = int(179.0 - (20.0 * p_norm / 129.0))
        z = max(1, min(10, z))
    elif dp_3h <= -0.5: # Falling
        z = int(130.0 - (10.0 * p_norm / 81.0))
        z = max(11, min(26, z))
    else: # Steady
        z = int(147.0 - (5.0 * p_norm / 37.0))
        z = max(1, min(22, z))

    return z_lookup.get(z, "Changeable, mixed conditions")

def check_storm_and_fog_alerts(p_rel: float, dp_3h: float, wind_speed: float, wind_gust: float, temp_c: float, dewpoint_c: float, hour_local: int):
    storm_alert = False
    storm_reason = ""
    if dp_3h <= -1.8 and (wind_gust or 0) > 25.0:
        storm_alert = True
        storm_reason = f"Rapid pressure drop ({dp_3h} hPa/3h) with gusting winds ({wind_gust} km/h). Squall line or convective cell imminent."
    elif dp_3h <= -2.5:
        storm_alert = True
        storm_reason = f"Severe barometric plunge ({dp_3h} hPa/3h). Intense storm system approaching."

    fog_alert = False
    fog_reason = ""
    if temp_c is not None and dewpoint_c is not None:
        depression = temp_c - dewpoint_c
        if depression <= 0.8 and (wind_speed or 0) <= 2.0 and (hour_local <= 8 or hour_local >= 21):
            fog_alert = True
            fog_reason = f"Atmosphere near 100% saturation (Dewpoint depression: {round(depression, 1)}°C) with calm winds. High radiation fog / frost probability."

    return {
        "storm_alert": storm_alert,
        "storm_reason": storm_reason,
        "fog_alert": fog_alert,
        "fog_reason": fog_reason
    }
