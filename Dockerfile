FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache \
    curl \
    nginx \
    jpeg-dev \
    zlib-dev \
    freetype-dev \
    build-base

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /var/www/weather/api && \
    cp web/index.html /var/www/weather/index.html && \
    cp nginx/weather.conf /etc/nginx/http.d/default.conf

EXPOSE 8080

CMD ["sh", "-c", "nginx && python3 main.py"]
