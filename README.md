# 📊 Automated Finance Newsletter Pipeline

A fully automated, zero-cost daily finance newsletter system powered by Python, Gemini AI, and GitHub Actions.

## ✨ Features
- 📡 RSS aggregation from top financial sources
- 🧹 Automated deduplication & content cleaning
- 🤖 AI-powered summarization via Gemini Free API
- 📧 Beautiful HTML/Markdown newsletter generation
- 🔄 Daily GitHub Actions automation
- 💾 Version-controlled newsletter archive

## 🛠 Tech Stack
| Component        | Technology           |
|------------------|----------------------|
| Language         | Python 3.12+         |
| AI               | Google Gemini Flash  |
| Email            | Gmail SMTP           |
| Scheduling       | GitHub Actions       |
| Templating       | Jinja2               |
| Version Control  | Git + GitHub CLI     |

## 🚀 Local Setup (Phase 1)
1. Clone or initialize the repository
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and fill credentials
5. `git add . && git commit -m "init"`
6. Deploy via GitHub Actions (covered in later phases)

## 📂 Project Structure