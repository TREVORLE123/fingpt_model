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
git clone <YOUR_REPO_URL>.git
cd fingpt_model