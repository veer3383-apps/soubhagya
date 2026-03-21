FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app
COPY . .

RUN pip install flask flask-cors pyswisseph playwright psycopg2-binary
RUN playwright install chromium

EXPOSE 8080

CMD ["python", "app.py"]
