# Scorely AI Viva Autograder
 
Scorely AI Viva Autograder is an intelligent academic assessment platform that automates two major workflows:
 
1. **Batch Written Evaluation** – AI-assisted grading of student answer sheets with structured feedback.
2. **Viva Session Management** – AI-generated viva question banks from uploaded study materials, with admin and student flows.
 
The system is designed for practical institutional use with production-focused error handling, clear result reporting, and deployment readiness.
 
---
 
## Core Features
 
- Batch grading for multiple student answer sheets
- AI-generated viva questions from PDF/TXT/DOCX content
- Question normalization for meaningful, readable viva prompts
- Difficulty and marks normalization per question
- Session-level result and report views in admin panel
- Robust **Session Expired** handling for API/auth/quota failures
- Frontend safeguards to prevent misleading “completed” states on failed runs
 
---
 
## Technology Stack
 
- **Backend:** Python, Flask, Flask-SocketIO
- **Frontend:** HTML, CSS, JavaScript
- **AI Integration:** OpenAI API
- **Database:** MySQL / SQLite (environment-driven)
- **Deployment Target:** AWS
 
---
 
## Project Structure
 
```text
scorely-ai-viva-autograder/
├── app.py
├── backend.py
├── database.py
├── requirements.txt
├── index.html
├── style.css
├── templates/
├── static/
└── README.md
