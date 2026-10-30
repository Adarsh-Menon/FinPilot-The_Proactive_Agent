Track 02
FinPilot
Build a Proactive Financial Agent

Mesh API is the official sponsor and using it is mandatory. Every LLM/AI call in your project must route through Mesh API. A submission that does not use Mesh API is not a valid submission.
The Challenge
Build FinPilot, a multi-agent system that ingests a user's transactions, understands their behavior, forecasts where their money is heading, simulates "what-if" decisions against their real data, and proactively surfaces specific, personalized recommendations.

This is not another budgeting dashboard. Apps that categorize transactions and show spending charts already exist and are very good at it. Their common limitation is that they are reactive and backward-looking: they tell you what already happened and wait for you to ask. The hard, valuable part of this challenge is forward-looking reasoning: understanding a person's behavior well enough to predict what will happen, to answer "can I afford this?" before they commit, and to proactively intervene with a specific action, not a generic tip.

The Story (what you are building, end to end)
A user uploads or logs their transactions.

A categorization agent labels each transaction (rent, food, subscriptions, impulse buys) with logic whose output genuinely depends on the transaction. An analysis agent studies behavior over time and detects patterns and anomalies: "food delivery is up 40% this month," "six subscriptions, two of them unused." A forecasting step projects where the user is heading if current behavior continues. An advisor agent reasons over all of this and produces specific, grounded recommendations: "cancel these two subscriptions to save this amount," "at this rate you miss your savings goal by March." The user can also ask "can I afford a trip next month?" and the system simulates it against their real trajectory and answers. A background job sends a scheduled digest with the key insight and one action. Every recommendation records why it was made.

What You Will Build
1. Multi-agent orchestration (the backbone)

A coordinator that routes work to specialized agents and assembles the result.
At least three genuinely distinct agents (for example categorizer, analyzer, forecaster, advisor). Distinct means each has its own job and its own prompt or its own tools — not the same prompt behind different names.
2. Ingestion and real categorization

Transactions are imported (CSV upload or manual logging) and categorized by logic that genuinely depends on the transaction, then stored. Hardcoded or random categories do not count.
3. Behavior analysis and anomaly detection

The system aggregates behavior over time and detects real patterns and anomalies in the user's own data — not generic observations.
4. Forecasting and the simulation loop (the core of this track)

The system projects the user's financial trajectory, and can simulate a "what-if" decision (a planned purchase, a savings goal) against that trajectory and report the effect. This forward-looking reasoning is the heart of the track and is what separates a real system from a categorization wrapper.
5. Proactive, grounded advice

Recommendations are specific and tied to the user's detected patterns and forecast, not generic advice. A generic "save more money" tip scores nothing; "cancel these specific items" scores high.
6. An auditable decision trail

Every recommendation records why it was made — which pattern or forecast triggered it.
7. Self-evaluation (we reward this heavily)

Build test data and measure how accurate your categorization and forecasts are, with the numbers recorded in the repo. A team that genuinely built this almost always measured whether it works.
Highlighted Bonus (optional, makes you stand out)
Structured agent framework (e.g. LangGraph) with an explicit analyze → forecast → advise flow.
Goal tracking with the advice adapting as the user progresses or falls behind.
Conversational layer that answers free-form money questions from the user's real data.
Scheduled proactive delivery (email or Telegram) of a personalized digest, driven by a real background scheduler (Celery / APScheduler / cron), not a manual button.
Suggested Stack (use what you are comfortable with)
Backend: Python (Flask or FastAPI)
Agents: LangGraph, CrewAI, AutoGen, or your own orchestrator
LLM access (mandatory): all LLM/AI calls must go through Mesh API, the official sponsor. Mesh is an OpenAI-compatible gateway (base_url="https://api.meshapi.ai/v1", key prefix rsk_): one key gives access to many models. Point the OpenAI SDK at Mesh and read your key from the MESH_API_KEY environment variable. Full guide: Mesh API docs.
Database: SQLite or PostgreSQL
Frontend: a simple web UI or API (Streamlit, Gradio, Jinja2, or a JS frontend)
Scheduling (bonus): Celery, APScheduler, or cron
You may use synthetic or sample transaction data. Keep your API keys in a local, gitignored .env. Since your code is what is evaluated, your credentials are never needed by us.

Submission Requirements
A public GitHub repository with all your code and a README explaining what you built, how the agents cooperate, and how to run it.
A short note on your key design decisions and your self-evaluation results (your test data and the numbers).
(Optional) A short demo video and a deployed URL — reviewed for shortlisted submissions.
What a Great Submission Looks Like
Distinct cooperating agents with a coordinator, not one prompt doing everything.
Categorization and anomaly detection that genuinely respond to the user's data.
A real forecast or simulation you can demonstrate ("if you keep this up, here is what happens").
Advice grounded in the user's own patterns, a visible reason for each recommendation, and honest accuracy numbers.
Build something you would be proud to show in an interview.

Using Mesh API (required)
Mesh API is mandatory — every LLM/AI call must go through it. Mesh is an OpenAI-compatible gateway: one key gives access to many models (GPT, Claude, Gemini, and more).

Create an API key (it starts with rsk_) on the Mesh dashboard, then point the OpenAI SDK at Mesh and read your key from the environment:

import os
from openai import OpenAI

client = OpenAI(base_url="https://api.meshapi.ai/v1", api_key=os.getenv("MESH_API_KEY"))
client.chat.completions.create(
    model="openai/gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
Add your key to a gitignored .env and as the GitHub secret MESH_API_KEY. Full guide: Mesh API docs.

Setup & Requirements
Follow this to make sure the automated checks pass and your project is eligible for evaluation.

Your repository must contain:

All your source code.
A requirements.txt (or pyproject.toml / Pipfile) listing a web framework (Flask or FastAPI) and the LLM client you use through Mesh API.
A README.md describing what you built, how your agents cooperate, your key design decisions, and your self-evaluation results.
A .gitignore that includes .env — never commit secrets.
Repository secrets (Settings → Secrets and variables → Actions) — new to GitHub secrets? Follow this short guide:

MESH_API_KEY — your Mesh API key. Mandatory; every LLM/AI call must go through Mesh.
SUBMISSION_TOKEN — your submission token, shown on your dashboard as soon as you register.
Your keys stay in your own repository and are only used by checks running in your own GitHub Actions — they are never sent to us.

Enabling the automated checks:

Download the workflow file: https://careerapi-production.krishnaik.in/api/ci/hackathons/agent-arena-2026/workflow.yml
Add it to your repository at .github/workflows/agent-arena-2026-checks.yml (create the .github/workflows/ folders if they don't exist).
Add the secrets listed above.
Push a commit. The checks run automatically — results appear in your repository's Actions tab and on your submission dashboard.
Critical checks (must pass for your commit to be eligible for evaluation):

Code compiles — all Python files are free of syntax errors.
Dependencies present — your requirements list a web framework and an LLM client.
Mesh API is used — your code routes LLM calls through Mesh (api.meshapi.ai base URL, or the meshapi SDK).
Mesh API key works — your MESH_API_KEY secret is a valid key. Verified with a metadata call that spends no credits.
Advisory checks (feedback only — these do not block you): no committed .env, a README is present, a .gitignore that ignores .env.

The automated checks are a first filter — passing them does not finalize your score. A failing critical check simply means "fix and push again"; it is not a penalty.

Mesh API — docs (mandatory LLM gateway)
LangGraph — agent orchestration
CrewAI
FastAPI
APScheduler — background jobs
Build FinPilot
above is given by hackathon conductors below is my notes
finpilot a proactive financial agent ,
input: users transaction (csv,excel, pdfor etc format) , manual logging , or bills or possbile ocr extraction
aim:understand their behavior , forecast where money is heading and predict and stop them
so agents we make
step 01: split data from excel/csv/xlsv pdf and store them in db with uuid
step02: categroizaion agent (depends on description on data) or if not in data ask the user what  is this expense means and also ask anythin profit/gain aswell if not understand in data from HITL.
step 03: investigator agent : ask for savings, plans for future the we store this information for the advisor agent
step 04: Analysis Agent(anomaly): anlysis find anomlies in transacrion like probably too much on food or subscription or etc dont mention it but make sure u keep that information but do ask user like if thereare subscription similar how long they are using
step 05 forcastor agent: if the current behavior continues show th problems like too much uneecessry on food or subscription or like sayin "if you keep this up.."
step 06: advisor agent: explains in what they should do with money takes details from the previous agents , help stratigize the minet , investment,savings,ipo smth useful for them , possible purchase in future if there transaction is good and if subscription are not used less and equal than 5 days tell to unsubscribe.
step 07: risk agent: checks above adviceis fit for user based on transaction create a balance sheet and p&l statement to finalise based on above basically agent acts as a 2nd opinion 
step 08: goals tracking agents : create goals to achieve and track next month like 20% less in spenting on outside food smth like that basicallly achievable and meaningfull goals if they achieve we giive them5 fina coins these coins can be redeemed as money or some incenitves like 5000 coins is 150 rs so a cashback for achieveing and tracking goals and remind their goals through email and telegram and whatsapp with a notification like if they keepbuying food on thursday night so with that information we need to send a notification using proactive scheduler for personal digest 

langgraph,fastapi,meshapi,db,rag(idk u tell me),js as frontent with auth basic we will build 2 interfaces one for customer that will a welcome page , chat interface , goals to be achieved checklist with dates , fina coins + referral pgm,we have coordinator agnt aswell  to give work . for user for every user it should be a multi tenant system like i am user a when i upload data a it should be with user a no user b should see or admin so we want to store data three formats short term memroy , long term memory and permanent memory, use caching if same questionsor smth similar iasked or given as output 

for admin we need to see a metric,latency overall, llmlangsmith we need to see the charts here and they cant see the users data only metrics and logs , observability only and golden must be creted for this aswell syntheitc 

use guardrails to prevent user pii and wrong information and output should not wrong informtion,

prompt versioning should be done aswell and shown in admin and admin needs ti see the agents working for user but wont see the data

user can see how the agents work one by one with data used

we can use mcp ot external tools to create balance  sheet and etc allow these tools for agents who needs it only so we need a security layer

if we need sandboxing in any step do lmk?

we need a kill switch for admin if advices are showing less evals or smth

we might need web search on good investment on stocks or property or side business or ipos so do that 

we are following a specs driven development so what i am typing rn should be store in specs folder with iteration_0.md file as we iterate on improvement we keep adding these file, we docs with detailed arch and design choices , readme.md file so this is plan