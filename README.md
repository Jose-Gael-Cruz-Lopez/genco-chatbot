# Genco Intel Chatbot

A standalone RAG chatbot for Generation Conscious, embedded on their WordPress site via a `<script>` tag.

## Prerequisites

- Python 3.11+
- pip

## Setup

```bash
# 1. Create and activate virtualenv
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r backend/requirements.txt

# 3. Configure environment
cp .env.example .env
# Open .env and fill in all API keys
```

## Run the development server

```bash
cd backend
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`. Check health at `http://localhost:8000/health`.

## Run tests

```bash
cd backend
python -m pytest -v
```
