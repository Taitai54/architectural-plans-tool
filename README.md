# Architectural Plans Tool

A web-based tool for uploading architectural PDF plans, extracting them as SVG, and using AI (OpenAI GPT-4 Vision) to analyse and annotate them.

## Stack

- **Backend**: Python, FastAPI
- **Frontend**: Vanilla HTML/CSS/JS (single file)
- **AI**: OpenAI GPT-4 Vision API
- **PDF tools**: `pdf2svg`, `pdftoppm`

## Setup

### Requirements

- Python 3.10+
- `pdf2svg` and `pdftoppm` installed on your system
  - macOS: `brew install pdf2svg poppler`
  - Ubuntu: `apt install pdf2svg poppler-utils`

### Install Python dependencies

```bash
cd backend
pip install -r requirements.txt
```

### Configure environment

Create a `backend/.env` file:

```
OPENAI_API_KEY=your_openai_api_key_here
```

## Running

```bash
./run.sh
```

This starts the FastAPI backend which also serves the frontend. Open your browser at `http://localhost:8000`.

## Usage

1. Upload an architectural PDF plan via the sidebar
2. The plan is converted to SVG and displayed in the viewer
3. Use the AI chat to ask questions or request annotations on the plan
