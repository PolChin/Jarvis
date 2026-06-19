# Phoenix demo - turnkey container.
#   Build once:   docker build -t phoenix-demo .
#   Run:          docker run --rm -p 8000:8000 phoenix-demo
#   Then open:    http://localhost:8000
# To enable the LLM, pass a key:
#   docker run --rm -p 8000:8000 -e GEMINI_API_KEY=xxxx phoenix-demo
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# bind to all interfaces so the published port is reachable from the host
ENV PHOENIX_HOST=0.0.0.0
ENV PHOENIX_PORT=8000
EXPOSE 8000

CMD ["python", "-m", "orchestrator.server"]
