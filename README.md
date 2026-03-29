# Legislative Auditor — Kazakhstan

**Decentrathon 5.0** | AI-powered legislative audit system for the Republic of Kazakhstan

## What It Does

Government analysts select a legal domain (Healthcare, Finance, Labor, etc.) and the system:

1. **Retrieves** law fragments from [adilet.zan.kz](https://adilet.zan.kz) via the Nia API
2. **Audits** them with Claude to find outdated norms, contradictions, and redundancies
3. **Ranks** problems by severity on a dashboard
4. **Proposes fixes** on demand — click any problem to get a concrete legislative amendment

## Quick Start

```bash
cd legislative-ai

# 1. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set API keys
cp .env.example .env
# Edit .env with your NIA_API_KEY and ANTHROPIC_API_KEY

# 4. (First time only) Trigger Nia to crawl adilet.zan.kz
curl -X POST http://localhost:8000/api/nia/index

# 5. Run the server
python -m backend.main
```

Open **http://localhost:8000** in your browser.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/domains` | List available legal domains |
| `POST` | `/api/nia/index` | Trigger Nia crawl of adilet.zan.kz |
| `POST` | `/api/audit/run` | Start audit for a domain |
| `GET` | `/api/audit/status?domain=...` | Poll audit progress |
| `GET` | `/api/audit/results?domain=...` | Get paginated problem list |
| `POST` | `/api/fix` | Generate a legislative fix for a problem |

## Architecture

```
Browser Dashboard  →  FastAPI Backend  →  Nia API (law retrieval)
                                       →  Anthropic API (audit + fix)
```

- **Nia** indexes and searches the Kazakhstan legal corpus at adilet.zan.kz
- **Claude** analyzes law fragments for outdated norms, contradictions, and redundancies
- **FastAPI** orchestrates the pipeline and serves the single-page dashboard

## Tech Stack

- **Backend**: Python, FastAPI, httpx, Anthropic SDK
- **Frontend**: Vanilla JS, Tailwind CSS
- **AI**: Claude (audit analysis + fix generation)
- **Data**: Nia API (legal corpus search)
