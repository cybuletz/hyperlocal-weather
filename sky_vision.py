import os
import urllib.request
import io
import math
import time
import logging
from PIL import Image

logger = logging.getLogger("HyperLocalSkyVision")

class SkyVisionAnalyzer:
    def __init__(self, frigate_url: str = "http://frigate.local:5000", cameras: list = None):
        self.frigate_url = frigate_url.rstrip("/")
        default_cams = [c.strip() for c in os.environ.get("FRIGATE_CAMERAS", "sky_camera,yard_camera").split(",") if c.strip()]
        self.cameras = cameras or default_cams
        self.last_analysis_ts = 0
        self.cache_duration = 90  # 90 seconds cache
        self.cached_result = {
            "status": "clear",
            "cloud_cover_pct": 0.0,
            "oktas": 0,
            "condition": "Clear Sky",
            "luminance": 120.0,
            "is_night": False,
            "fog_detected": False,
            "primary_camera": self.cameras[0] if self.cameras else "sky_camera",
            "timestamp": 0
        }

    def analyze_sky(self, outdoor_humidity: float = 50.0) -> dict:
        now_ts = time.time()
        if now_ts - self.last_analysis_ts < self.cache_duration and self.cached_result.get("timestamp", 0) > 0:
            return self.cached_result

        for cam in self.cameras:
            try:
                url = f"{self.frigate_url}/api/{cam}/latest.jpg"
                req = urllib.request.Request(url, headers={"User-Agent": "HyperLocalSkyVision/1.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    img_bytes = resp.read()

                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                w, h = img.size

                # Crop upper 25% of frame (pure sky region)
                sky_crop = img.crop((0, 0, w, int(h * 0.25)))
                sky_thumb = sky_crop.resize((160, 40))

                pixels = list(sky_thumb.getdata())
                total_px = len(pixels)

                blue_px = 0
                cloud_px = 0
                lum_list = []

                for r, g, b in pixels:
                    y = 0.299 * r + 0.587 * g + 0.114 * b
                    lum_list.append(y)

                    if y > 25:  # Daytime threshold
                        ratio = r / (b + 0.001)
                        if ratio < 0.78 and b > 45:
                            blue_px += 1
                        else:
                            cloud_px += 1

                mean_lum = sum(lum_list) / total_px
                variance = sum((x - mean_lum) ** 2 for x in lum_list) / total_px
                std_dev = math.sqrt(variance)

                is_night = mean_lum < 20
                fog_detected = False
                if not is_night and std_dev < 14 and (outdoor_humidity or 50) >= 88:
                    fog_detected = True

                valid_day_px = blue_px + cloud_px
                if is_night:
                    cloud_pct = 0.0
                    oktas = 0
                    condition = "Night / Dark"
                elif valid_day_px > 0:
                    cloud_pct = round((cloud_px / valid_day_px) * 100, 1)
                    oktas = min(8, int(round((cloud_pct / 100.0) * 8)))
                    if oktas <= 1:
                        condition = "Clear Sky"
                    elif oktas == 2:
                        condition = "Few Clouds"
                    elif oktas in (3, 4):
                        condition = "Scattered Clouds"
                    elif oktas in (5, 6, 7):
                        condition = "Broken Clouds"
                    else:
                        condition = "Overcast"
                else:
                    cloud_pct = 0.0
                    oktas = 0
                    condition = "Clear Sky"

                if fog_detected:
                    condition = "Radiation Fog / Haze"

                res = {
                    "status": "ok",
                    "cloud_cover_pct": cloud_pct,
                    "oktas": oktas,
                    "condition": condition,
                    "luminance": round(mean_lum, 1),
                    "contrast_std": round(std_dev, 1),
                    "is_night": is_night,
                    "fog_detected": fog_detected,
                    "primary_camera": cam,
                    "timestamp": now_ts
                }
                self.cached_result = res
                self.last_analysis_ts = now_ts
                logger.info(f"SkyVision analysis complete via {cam}: {cloud_pct}% cloud cover ({oktas}/8 {condition})")
                return res

            except Exception as e:
                logger.warning(f"Error analyzing sky on camera {cam}: {e}")
                continue

        return self.cached_result

if __name__ == "__main__":
    analyzer = SkyVisionAnalyzer()
    out = analyzer.analyze_sky()
    print("Standalone SkyVision Output:", out)
