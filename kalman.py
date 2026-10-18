import math

class BiasKalmanFilter:
    def __init__(self, process_noise: float = 0.04, measurement_noise: float = 0.4):
        self.q = process_noise
        self.r = measurement_noise
        self.bias_estimate = 0.0
        self.error_cov = 1.0
        self.initialized = False

    def update(self, station_value: float, model_value: float):
        if station_value is None or model_value is None:
            return self.bias_estimate

        measurement = station_value - model_value

        if not self.initialized:
            self.bias_estimate = measurement
            self.error_cov = self.r
            self.initialized = True
            return self.bias_estimate

        # Prediction step
        p_pred = self.error_cov + self.q

        # Measurement update
        kalman_gain = p_pred / (p_pred + self.r)
        self.bias_estimate = self.bias_estimate + kalman_gain * (measurement - self.bias_estimate)
        self.error_cov = (1.0 - kalman_gain) * p_pred
        return round(self.bias_estimate, 2)

    def calibrate_curve(self, raw_curve: list, decay_rate: float = 0.08) -> list:
        """
        Applies exponentially decaying bias correction across future forecast hours.
        raw_curve: list of dicts with 'temp', 'humidity', etc.
        """
        calibrated = []
        for i, val in enumerate(raw_curve):
            decay = math.exp(-decay_rate * i)
            adjusted = round(val + (self.bias_estimate * decay), 1)
            calibrated.append(adjusted)
        return calibrated
