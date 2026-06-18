# Build context is the REPOSITORY ROOT (so the widget can be bundled), not backend/.
# Build with:  docker build -f backend/Dockerfile -t genco-chatbot .
FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app
# Bundle the widget so the backend can serve it at /widget/widget.js (the WordPress embed target).
# main.py resolves the widget dir to <repo-root>/widget/dist; in the image that is /widget/dist.
COPY widget/dist /widget/dist
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
