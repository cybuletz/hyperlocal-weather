import urllib.request
import json
import logging

logger = logging.getLogger("hyperlocal.ai")

def run_ai_resonance(ollama_url: str, model_name: str, telemetry: dict, nwp_summary: dict, radar_nowcast: dict, zambretti: str, sky_vision: dict = None):
    """
    Invokes local Ollama (llama3.2:1b) to generate a human-centric, engaging,
    plain-English weather concierge debrief and practical everyday guidance.
    """
    temp = telemetry.get('temperature', 12.0)
    rain_desc = radar_nowcast.get('description', 'Clear')
    if sky_vision is None:
        sky_vision = {"cloud_cover_pct": 0, "condition": "Clear"}

    prompt = f"""You are a friendly, witty, and deeply helpful personal weather concierge for a family home.
Analyze these live weather station sensors and provide an engaging, clear summary for a regular person (no technical weather jargon!).

CURRENT CONDITIONS:
- Outside Temp: {temp}°C (Feels like: {telemetry.get('feels_like')}°C)
- Humidity: {telemetry.get('humidity')}% | Wind: {telemetry.get('wind_speed')} km/h
- Pressure: {telemetry.get('relative_pressure')} hPa ({telemetry.get('pressure_tendency_desc')})
- Radar Nowcasting: {rain_desc}
- General Trend: {zambretti}
- Visual Sky Camera (Frigate): {sky_vision.get("cloud_cover_pct", 0)}% cloud cover ({sky_vision.get("condition", "Clear")} observed overhead)

IMPORTANT: Keep the badges extremely punchy and concise (maximum 2 to 3 words) so they fit cleanly inside UI cards.

Respond ONLY with valid JSON with these exact keys:
{{
  "human_summary": "A lively, engaging 2-sentence summary of what the weather actually feels like right now and for the next few hours.",
  "outfit_tip": "Punchy 2-word badge (e.g. Warm jacket, Light sweater, T-shirt & shorts)",
  "umbrella": "Punchy 2-word badge (e.g. Not needed, Keep handy, Take umbrella)",
  "car_wash": "Punchy 2-word badge (e.g. Safe to wash, Hold off (rain), Great day)",
  "smart_home_tip": "1 practical sentence for home comfort, windows, or ventilation.",
  "confidence_score": 92
}}
"""

    payload = json.dumps({
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.3,
            "num_predict": 180
        }
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            response_text = data.get("response", "{}").strip()
            parsed = json.loads(response_text)
            return parsed
    except Exception as e:
        logger.warning(f"Ollama resonance failed: {e}. Falling back to rule-based human advice.")
        outfit = "Warm jacket" if (temp or 10) < 14 else "Casual wear"
        umbrella = "Keep handy" if "rain" in zambretti.lower() else "Not needed"
        car_wash = "Hold off (rain)" if "rain" in zambretti.lower() else "Safe to wash"
        return {
            "human_summary": f"It is currently {temp}°C outside with calm winds. Conditions are {zambretti.lower()}.",
            "outfit_tip": outfit,
            "umbrella": umbrella,
            "car_wash": car_wash,
            "smart_home_tip": "Keep windows closed to retain indoor warmth tonight.",
            "confidence_score": 90
        }
