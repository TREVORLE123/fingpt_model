📘 FinGPT Massive Options Screener API

Beginner-friendly options screener built on FastAPI + Massive + Azure Container Apps

This backend:
	•	Calls Massive for SPY option chain snapshots
	•	Picks the top 5 option contracts (volume, premium, OI, IV)
	•	Generates a beginner-friendly explanation
	•	Has debug tools + secure endpoints for Copilot or frontends
	•	Deploys cleanly to Azure Container Apps

⸻

🚀 1. What this app does

When a user asks something like:

“Run an options screener for today and explain the most important signals.”

The backend:
	1.	Calls Massive’s SPY option snapshot
	2.	Scores each contract
	3.	Picks the top 5
	4.	Formats them as readable lines
	5.	Generates an educational explanation about what they mean

Example screener output:

Top option signals from Massive (pre-filtered):
- O:SPY251202C00680000 | expiry=2025-12-02, strike=680, volume=81188, OI=2908, IV=0.09, delta=0.69, premium=1.88
- O:SPY251202C00681000 | ...


🧰 2. Requirements
	•	Python 3.10+ (3.11 recommended)
	•	A Massive API Key
	•	(optional) An API Gateway key for restricting /api/chat
	•	(optional) Azure CLI for deployment

⸻

📥 3. Clone the project
git clone https://github.com/TREVORLE123/fingpt_model.git
cd fingpt_model


🧪 4. Create virtual environment

macOS / Linux

python3 -m venv .venv
source .venv/bin/activate

WINDOWS: 

python -m venv .venv
.\.venv\Scripts\Activate.ps1

📦 5. Install dependencies

If you have a requirements.txt:

🔐 6. Create your .env

In the project root:

touch .env

Add:
MASSIVE_API_KEY=YOUR_MASSIVE_API_KEY
API_GATE_KEY=OPTIONAL_SECURE_KEY


▶️ 7. Run the API locally

uvicorn main:app --reload --port 8000

http://127.0.0.1:8000


🔍 8. Test the Massive screener via debug endpoint
curl "http://127.0.0.1:8000/debug/screener"

Example Output

{
  "raw_count": 100,
  "top_signals_count": 5,
  "top_signals": [...],
  "formatted_for_prompt": "Top option signals from Massive..."
}


💬 9. Test the OPEN /chat endpoint

This endpoint does not require an API key:

curl -X POST "http://127.0.0.1:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Run an options screener for today and explain the most important signals.","max_tokens":220,"temperature":0.7}'

  🔐 10. Test the SECURE /api/chat endpoint

If you set API_GATE_KEY:
curl -X POST "http://127.0.0.1:8000/api/chat" \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_GATE_KEY" \
  -d '{"prompt":"Run an options screener for today and explain the most important signals."}'

  🌩️ 12. Deploying to Azure Container Apps (simple version)

1. Login to Azure

az login

2. Create resource group (only once)

az group create -n trevor-fingpt-rg -l eastus

3. Deploy the container app

From inside your project folder:

az containerapp up \
  --name fingpt-api \
  --resource-group trevor-fingpt-rg \
  --source . \
  --ingress external \
  --target-port 8000

  Azure will:
	•	Build a Docker image
	•	Push it to ACR
	•	Deploy the container
	•	Give you a public URL

It’ll say something like:

Browse to your container app at:
https://fingpt-api.<RANDOM>.eastus.azurecontainerapps.io

4. Test on Azure

Screener:

curl "https://YOUR_APP_URL/debug/screener"

Chat:

curl -X POST "https://YOUR_APP_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"run an options screener for today"}'


Secure endpoint:
curl -X POST "https://YOUR_APP_URL/api/chat" \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_GATE_KEY" \
  -d '{"prompt":"run an options screener for today"}'


🔄 13. Updating the app (redeploy)

Any time you change code:
az containerapp up \
  --name fingpt-api \
  --resource-group trevor-fingpt-rg \
  --source . \
  --ingress external \
  --target-port 8000


🎯 14. Future improvements
	•	Add PUTs scanning
	•	Add symbol list support
	•	Add 0DTE-only filter
	•	Replace generate_answer with a real LLM (GPT-4.1-mini, FinGPT, etc.)
	•	Add caching layer for Massive results
	•	Add multi-symbol screening (using symbols.txt)

⸻
