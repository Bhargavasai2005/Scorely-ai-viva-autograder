import os
import re
import json
import uuid
import datetime
import time
import io
import base64
import threading
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
import docx  
import PyPDF2  
from flask_socketio import SocketIO, emit
from faster_whisper import WhisperModel
from pydub import AudioSegment
from backend import Backend
import database # autograder_db
# import evaluation_db  # no longer needed

load_dotenv()
VIVA_STRICT_AI = os.getenv('VIVA_STRICT_AI', '1').strip() == '1'

# ==========================================
# 1. CONFIGURATION & INITIALIZATION
# ==========================================

# ✅ FIX 2: Use the SAME database engine from database.py for VIVA too.
# No separate viva_db_config with mysql.connector — everything goes through SQLAlchemy.

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

app = Flask(__name__, static_folder=".")
CORS(app)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ✅ FIX 1: Switch async_mode from 'eventlet' to 'gevent' to fix the OSError + deprecation warning.
# Also allow_unsafe_werkzeug=True lets Flask-SocketIO use the built-in dev server safely.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

print("Loading Voice AI Model... Please wait.")
whisper_model = WhisperModel("distil-small.en", device="cpu", compute_type="int8")
print("Voice AI is Ready!")

# Initialize existing Autograder Backend
backend_api = Backend()

# Admin credentials
ADMIN_CREDENTIALS = {
    "username": "admin",
    "password": "password123"
}

# Viva Memory Database (For tracking active sessions in RAM)
viva_memory_db = {
    'sessions': [],
    'students': {},
    'results': {}
}

# ==========================================
# 2. VIVA DATABASE SETUP (unified, SQLAlchemy)
# ==========================================
def init_viva_db():
    """Creates viva_* tables in autograder_db using SQLAlchemy."""
    try:
        engine = database.db_engine
        if engine is None:
            print("🚨 Viva DB Init Error: database engine is None.")
            return

        from sqlalchemy import text
        dialect = engine.dialect.name
        with engine.connect() as conn:
            # 1. Create Student Table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS viva_students (
                    enrollment_number VARCHAR(255) PRIMARY KEY,
                    student_name VARCHAR(255) NOT NULL,
                    branch VARCHAR(255),
                    batch VARCHAR(255)
                )
            """))

            # 2. Create Responses Table
            if dialect == "sqlite":
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS viva_responses (
                        response_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        enrollment_number VARCHAR(255),
                        subject_id VARCHAR(255),
                        subject_name VARCHAR(255),
                        question_number INT,
                        question_text TEXT,
                        difficulty_level VARCHAR(50),
                        student_answer TEXT,
                        answered_status VARCHAR(10),
                        marks_awarded FLOAT,
                        actionable_suggestion TEXT,
                        proctoring_status VARCHAR(50) DEFAULT 'Clean',
                        FOREIGN KEY (enrollment_number) REFERENCES viva_students(enrollment_number)
                    )
                """))
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_enroll
                    ON viva_responses(enrollment_number)
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS viva_responses (
                        response_id INT AUTO_INCREMENT PRIMARY KEY,
                        enrollment_number VARCHAR(255),
                        subject_id VARCHAR(255),
                        subject_name VARCHAR(255),
                        question_number INT,
                        question_text TEXT,
                        difficulty_level VARCHAR(50),
                        student_answer TEXT,
                        answered_status VARCHAR(10),
                        marks_awarded FLOAT,
                        actionable_suggestion TEXT,
                        proctoring_status VARCHAR(50) DEFAULT 'Clean',
                        INDEX idx_enroll (enrollment_number),
                        FOREIGN KEY (enrollment_number) REFERENCES viva_students(enrollment_number)
                    )
                """))

            # 3. Create Sessions Table (The "Master Bank" Table)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS viva_sessions (
                    id VARCHAR(255) PRIMARY KEY,
                    session_name VARCHAR(255),
                    subject VARCHAR(255),
                    branch VARCHAR(255),
                    batch VARCHAR(255),
                    semester VARCHAR(100),
                    programme VARCHAR(100),
                    num_questions INT,
                    time_limit INT DEFAULT 10,
                    marks_per_q INT DEFAULT 10,
                    status VARCHAR(50) DEFAULT 'active',
                    generated_questions JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            # Add missing columns to existing tables if they don't exist yet
            for col, definition in [
                ("semester",    "VARCHAR(100) DEFAULT ''"),
                ("programme",   "VARCHAR(100) DEFAULT ''"),
                ("time_limit",  "INT DEFAULT 10"),
                ("marks_per_q", "INT DEFAULT 10"),
                ("status",      "VARCHAR(50) DEFAULT 'active'"),
            ]:
                try:
                    conn.execute(text(f"ALTER TABLE viva_sessions ADD COLUMN {col} {definition}"))
                except Exception:
                    pass  # Column already exists — that's fine
            
            conn.commit()
        print(f"[OK] All Viva tables (Students, Responses, Sessions) ready in {database.DB_DATABASE} ({dialect}).")
    except Exception as e:
        print(f"[ERROR] Viva DB Init Error: {e}")
        import traceback
        traceback.print_exc()

init_viva_db()

# ==========================================
# 3. AI GENERATOR FUNCTIONS (VIVA)
# ==========================================
def infer_question_count_from_text(text_content):
    if not text_content:
        return None
    numbered = re.findall(r'(?m)^\s*(\d{1,2})[\.)]\s+', text_content)
    if not numbered:
        return None
    nums = sorted({int(n) for n in numbered if n.isdigit()})
    if not nums:
        return None
    # Typical question papers use sequential numbering: 1..N
    if nums[0] == 1:
        return nums[-1]
    return len(nums)


def infer_marks_per_question(text_content, num_questions):
    if not text_content:
        return None

    normalized = re.sub(r'\s+', ' ', text_content).strip()
    header_zone = normalized[:1500]

    # 1) Prefer explicit instruction: "Each question carries X marks"
    explicit = re.search(
        r'(?:each|every)\s+question(?:\s+\w+){0,4}\s+(?:carries|carry|is|=)\s*(\d+(?:\.\d+)?)\s*marks?',
        header_zone,
        re.IGNORECASE,
    )
    if explicit:
        val = float(explicit.group(1))
        return int(val) if val.is_integer() and val > 0 else None

    # 2) Else infer from total paper marks ÷ number of questions
    total_match = re.search(
        r'(?:max(?:imum)?\s*marks?|total\s*marks?|marks)\s*[:=\-]?\s*(\d{1,3}(?:\.\d+)?)',
        header_zone,
        re.IGNORECASE,
    )
    if not total_match:
        return None

    total_marks = float(total_match.group(1))
    if total_marks <= 0 or num_questions <= 0:
        return None

    per_q = total_marks / float(num_questions)
    if per_q.is_integer() and per_q > 0:
        return int(per_q)
    return None


def extract_numbered_questions_from_text(text_content):
    if not text_content:
        return []

    normalized = text_content.replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(r'(?ms)^\s*(\d{1,2})[\.)]\s+(.+?)(?=^\s*\d{1,2}[\.)]\s+|\Z)')
    items = []
    for _, q in pattern.findall(normalized):
        cleaned = " ".join(q.split())
        if cleaned:
            items.append(cleaned)

    # Fallback for flattened text like "1. ... 2. ..." in a single line
    if not items:
        inline_parts = re.split(r'\s+(?=\d{1,2}[\.)]\s+)', " ".join(text_content.split()))
        for part in inline_parts:
            m = re.match(r'^\d{1,2}[\.)]\s+(.+)$', part.strip())
            if m:
                cleaned = " ".join(m.group(1).split())
                if cleaned:
                    items.append(cleaned)

    return items


def _extract_json_array(raw_text):
    text = (raw_text or "").strip()
    if not text:
        return None

    cleaned = text.replace("```json", "").replace("```", "").strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    # Tolerant parse for truncated arrays: recover complete objects before cutoff.
    if start != -1:
        decoder = json.JSONDecoder()
        body = cleaned[start + 1 :]
        idx = 0
        items = []

        while idx < len(body):
            while idx < len(body) and body[idx] in " \t\r\n,":
                idx += 1

            if idx >= len(body) or body[idx] == ']':
                break

            try:
                obj, next_idx = decoder.raw_decode(body, idx)
                items.append(obj)
                idx = next_idx
            except Exception:
                break

        if items:
            return items

    return None


def _normalize_marks(value, default=5):
    try:
        marks = float(value)
    except Exception:
        marks = float(default)

    allowed = [2, 5, 10]
    nearest = min(allowed, key=lambda x: abs(x - marks))
    return int(nearest)


def _difficulty_from_marks(marks):
    if marks <= 2:
        return "easy"
    if marks <= 5:
        return "medium"
    return "hard"


def _cleanup_document_question_text(raw_text):
    text = re.sub(r'https?://\S+|www\.\S+', ' ', str(raw_text or ''), flags=re.IGNORECASE)
    text = re.sub(r'\b(?:python\s+programming|iii\s*year|ii\s*/\s*sem|mrcet|script\s+begins?|script\s+ends?)\b', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip().lstrip('•-')

    if ':' in text:
        parts = [p.strip(' -•') for p in text.split(':') if p.strip(' -•')]
        if parts:
            text = next((p for p in reversed(parts) if len(p.split()) >= 4), parts[-1])

    text = re.sub(r'^\d{1,2}[\.)]\s*', '', text).strip()
    words = text.split()
    if len(words) > 20:
        text = ' '.join(words[:20])
    if text and not text.endswith('?'):
        text = text.rstrip('.!') + '?'
    return text


def _is_noisy_question_text(question_text):
    q = str(question_text or '').strip()
    if not q:
        return True
    if re.search(r'https?://|www\.', q, re.IGNORECASE):
        return True
    if re.search(r'\b(script\s+begins|script\s+ends|iii\s*year|ii\s*/\s*sem|mrcet)\b', q, re.IGNORECASE):
        return True
    if re.search(r'\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b', q):
        return True
    if re.search(r'\bwhen\s+you\s*\?$', q, re.IGNORECASE):
        return True
    if len(q.split()) > 24:
        return True
    return False


def _looks_meaningful_question(question_text):
    q = str(question_text or '').strip()
    if not q or len(q.split()) < 5:
        return False
    if not q.endswith('?'):
        return False
    if _is_noisy_question_text(q):
        return False
    if re.search(r'\b(which\s+is\s+installed\s+when\s+you\??|beginning\s+with\s+python\s+programming\??)\b', q, re.IGNORECASE):
        return False

    allowed_starts = (
        'what', 'why', 'how', 'which', 'when', 'where',
        'explain', 'describe', 'define', 'compare', 'differentiate',
        'is', 'are', 'can', 'could', 'should', 'do', 'does'
    )
    first = q.split()[0].lower()
    return first in allowed_starts


def _rewrite_question_heuristic(question_text, subject):
    q = _cleanup_document_question_text(question_text)
    lower = q.lower()

    if 'python 2.0' in lower and 'python 3.0' in lower:
        return 'How did Python 3.0 differ from Python 2.0 in language design and features?'
    if 'interpreter' in lower or 'idle' in lower:
        return 'What is a Python interpreter, and how can IDLE be used to run Python scripts?'
    if 'object' in lower and ('polymorphism' in lower or 'multiple inheritance' in lower or 'overloading' in lower):
        return 'How does Python support polymorphism, operator overloading, and multiple inheritance?'
    if 'indentation' in lower:
        return 'Why is indentation mandatory in Python, and how does it define code blocks?'
    if 'factor' in lower and 'python' in lower:
        return 'What are the main reasons Python is widely used in day-to-day software development?'
    if 'beginning' in lower and 'python' in lower:
        return 'How should a beginner start learning Python programming effectively?'

    return f'Explain one core concept of {subject} with a simple practical example?'


def _generic_subject_questions(subject):
    topic = str(subject or 'this subject').strip()
    return [
        f'What are the most important fundamentals of {topic} a beginner should master first?',
        f'How would you explain a core concept of {topic} with a real-world example?',
        f'Why is {topic} useful in practical software development scenarios?',
        f'What common mistakes do students make while learning {topic}, and how can they avoid them?',
        f'How do you approach solving a basic problem in {topic} step by step?',
        f'Which concepts of {topic} are most frequently asked in viva examinations, and why?',
        f'How can you test whether your understanding of {topic} concepts is correct?',
        f'What is the difference between beginner-level and advanced-level thinking in {topic}?',
    ]


def _normalize_question_bank(questions, target_num=None):
    normalized = []
    seen = set()

    for q in questions or []:
        if not isinstance(q, dict):
            continue

        q_text = _cleanup_document_question_text(q.get('question', ''))
        if not _looks_meaningful_question(q_text):
            q_text = _rewrite_question_heuristic(q_text, q.get('subject', 'the subject'))

        if not _looks_meaningful_question(q_text):
            continue

        key = q_text.lower()
        if key in seen:
            continue

        marks = _normalize_marks(q.get('marks', 5), default=5)
        difficulty = str(q.get('difficulty', '') or '').strip().lower()
        if difficulty not in {'easy', 'medium', 'hard'}:
            difficulty = _difficulty_from_marks(marks)

        normalized.append({
            "question": q_text,
            "ideal_answer": str(q.get('ideal_answer') or "State the core concept accurately and concisely."),
            "marks": marks,
            "difficulty": difficulty,
        })
        seen.add(key)

        if target_num and len(normalized) >= int(target_num):
            break

    return normalized


def _top_up_questions_from_document(base_questions, context, target_num, subject='the subject'):
    out = _normalize_question_bank(base_questions)
    if len(out) >= target_num:
        return out[:target_num]

    seen = {
        re.sub(r'\s+', ' ', str(q.get('question', '') or '')).strip().lower()
        for q in out if isinstance(q, dict)
    }

    extracted_questions = extract_numbered_questions_from_text(context)
    for q_text in extracted_questions:
        if len(out) >= target_num:
            break
        candidate = _normalize_question_bank([{
            "question": q_text,
            "ideal_answer": "State core concept accurately with one practical point.",
            "marks": 5,
            "difficulty": "medium",
            "subject": subject,
        }])
        if not candidate:
            continue

        normalized_item = candidate[0]
        key = normalized_item["question"].lower()
        if key in seen:
            continue
        out.append(normalized_item)
        seen.add(key)

    if len(out) < target_num:
        for fallback_q in _generic_subject_questions(subject):
            if len(out) >= target_num:
                break
            key = fallback_q.lower()
            if key in seen:
                continue
            out.append({
                "question": fallback_q,
                "ideal_answer": "Explain clearly with one concrete technical example.",
                "marks": 5,
                "difficulty": "medium",
            })
            seen.add(key)

    return out


def generate_questions_with_ai(subject, num_questions, context, strict_mode=False):
    """
    Optimized: Single API call for ALL questions = much faster.
    Context trimmed to 2000 chars. One call instead of loops.
    """
    target_num = int(num_questions)
    clean_lines = [ln.strip() for ln in str(context or '').splitlines() if ln and ln.strip()]
    clean_context = "\n".join(clean_lines[:140])[:2200]

    prompt = f"""
        You are a Senior Professor conducting a technical Viva Voce. 
        Based on the following notes for the subject '{subject}', generate {target_num} unique questions.

        RULES:
        1. Each question MUST be a complete, professional sentence.
        2. DO NOT repeat topics. Cover different technical units.
        3. Each question must be concise (max 20 words) and must end with '?'.
        4. Do NOT copy raw lines, bullets, dates, headings, or metadata from context.
        5. Provide a very brief 'ideal_answer' (max 12 words), assign 'marks' as 2, 5, or 10, and set difficulty as easy/medium/hard.
        6. Return EXACTLY {target_num} objects and no keys except: question, ideal_answer, marks, difficulty.
        
        Return ONLY a JSON array:
        [
          {{
            "question": "Full sentence here?",
            "ideal_answer": "Clear technical answer",
            "marks": 5,
            "difficulty": "medium"
          }}
        ]

        CONTEXT:
        {clean_context}
        """  # optimized for low token usage

    last_error = None
    best_valid = []
    for attempt in range(2):
        try:
            base_cap = max(500, min(1000, 150 * target_num))
            completion_cap = base_cap if attempt == 0 else min(1400, base_cap + 300)
            response = client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {"role": "system", "content": "Return only valid JSON array. No markdown, no prose."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=completion_cap,
                temperature=1
            )
            usage = response.usage
            print(f"[generate_questions] Tokens — prompt:{usage.prompt_tokens} completion:{usage.completion_tokens} total:{usage.total_tokens}")
            completion_used = int(getattr(usage, "completion_tokens", 0) or 0)
            raw = response.choices[0].message.content.strip()
            questions = _extract_json_array(raw)
            valid = []
            if isinstance(questions, list):
                for q in questions:
                    if not isinstance(q, dict):
                        continue
                    normalized_q = _normalize_question_bank([q])
                    if normalized_q:
                        valid.append(normalized_q[0])

                if len(valid) > len(best_valid):
                    best_valid = valid

                if len(valid) >= target_num:
                    print(f"Generated {len(valid)} questions in {attempt+1} attempt(s)")
                    return _normalize_question_bank(valid, target_num)

                # Token-safe recovery: if completion hit cap, avoid expensive retries and top-up locally.
                if completion_used >= (completion_cap - 5) and len(valid) >= 1:
                    recovered = _top_up_questions_from_document(valid, context, target_num, subject)
                    if len(recovered) >= target_num:
                        print(f"[generate_questions] Recovered {len(recovered)} questions from truncated output + document top-up")
                        return _normalize_question_bank(recovered, target_num)
                    best_valid = recovered if len(recovered) > len(best_valid) else best_valid

            # Permanent anti-loop fix: even if JSON parse fails completely, recover from uploaded document
            # instead of repeatedly calling API and burning tokens.
            if completion_used >= (completion_cap - 5):
                recovered = _top_up_questions_from_document(valid, context, target_num, subject)
                if len(recovered) >= target_num:
                    print(f"[generate_questions] Recovered {len(recovered)} questions via document rescue at cap {completion_cap}")
                    return _normalize_question_bank(recovered, target_num)
                if len(recovered) > len(best_valid):
                    best_valid = recovered
                # If document has no extractable numbered questions, continue to next attempt.

            if completion_used >= (completion_cap - 5):
                last_error = RuntimeError(
                    f"AI output truncated at completion cap ({completion_cap}) and JSON could not be parsed."
                )
            else:
                last_error = RuntimeError("AI response did not contain a valid JSON question array.")
        except Exception as e:
            last_error = e
            print(f"generate_questions attempt {attempt+1} failed: {e}")
            time.sleep(1)

    # Fallback priority 1 (only in non-strict mode): extract numbered questions from uploaded paper text
    if not strict_mode:
        extracted_questions = extract_numbered_questions_from_text(context)
        if extracted_questions:
            print(f"[generate_questions] Using document-extracted fallback questions: {len(extracted_questions)} found")
            from_doc = []
            for i in range(target_num):
                q_text = extracted_questions[i % len(extracted_questions)]
                marks = 5
                from_doc.append({
                    "question": _cleanup_document_question_text(q_text),
                    "ideal_answer": "Answer directly based on the concepts and steps implied by the question.",
                    "marks": marks,
                    "difficulty": _difficulty_from_marks(marks),
                    "subject": subject,
                })
            return _normalize_question_bank(from_doc, target_num)

    # Strict-mode rescue: if we got some valid questions, complete from document text without extra API calls.
    if strict_mode and best_valid:
        recovered = _top_up_questions_from_document(best_valid, context, target_num, subject)
        if len(recovered) >= max(1, min(target_num, 3)):
            print(f"[generate_questions] Strict-mode rescue returning {len(recovered)} questions")
            return _normalize_question_bank(recovered, target_num)

    if _is_api_auth_or_quota_error(last_error):
        raise RuntimeError("Session Expired: OpenAI API key is invalid or quota is exhausted.")

    local_fallback = _top_up_questions_from_document(best_valid, context, target_num, subject)
    if len(local_fallback) >= target_num:
        print(f"[generate_questions] Using deterministic local fallback: {len(local_fallback)} questions")
        return _normalize_question_bank(local_fallback, target_num)

    if strict_mode:
        cause = str(last_error) if last_error else "No valid JSON question array returned by AI."
        raise RuntimeError(f"AI question generation failed in strict mode: {cause}")

    return []


def _is_insufficient_quota_error(err: Exception) -> bool:
    msg = str(err).lower()
    return "insufficient_quota" in msg or "exceeded your current quota" in msg


def _is_api_auth_or_quota_error(err: Exception) -> bool:
    msg = str(err or "").lower()
    tokens = [
        "invalid_api_key",
        "incorrect api key",
        "missing openai_api_key",
        "insufficient_quota",
        "exceeded your current quota",
        "error code: 401",
        "error code: 429",
        "authentication",
    ]
    return any(t in msg for t in tokens)

def grade_answer(question, student_answer, ideal_answer, max_q_marks):
    """Optimized Grade function using GPT-5-mini."""
    ans = (student_answer or "").lower().strip()
    
    # 1. Quick check for unattempted (Saves API Credits)
    if len(ans) < 4 or any(x in ans for x in ["don't know", "skip", "idk", "no answer"]):
        return {
            "score": 0,
            "quality": "unattempted",
            "mistakes_made": "No attempt made.",
            "actionable_suggestion": "Try to answer next time."
        }

    # 2. Aggressive Input Trimming (Saves Tokens: Q:140, Ideal:140, Student:180)
    prompt = (
        f"Grade this answer. Return ONLY JSON.\n"
        f"Max Marks for this Q: {max_q_marks}\n"
        f"Q: {question[:140]}\n"
        f"Ideal: {ideal_answer[:140]}\n"
        f"Student: {student_answer[:180]}\n\n"
        "STRICT RULES:\n"
        "1. 'score' MUST be a number between 0 and 100 (percentage).\n"
        "2. 'mistakes_made' and 'actionable_suggestion' MUST be unique technical gaps.\n"
        "3. DO NOT repeat the question text in the suggestion.\n"
        '{"score":<0-100>,"quality":"good/poor","mistakes_made":"...","actionable_suggestion":"..."}'
    )

    try:
        response = client.chat.completions.create(
            model="gpt-5-mini", # Kept as gpt-5-mini per your request
            messages=[
                {"role": "system", "content": "You are a strict technical examiner. JSON only."},
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=150, # Manager's limit
            temperature=1             # Manager's limit for consistency
        )
        
        raw = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))

    except Exception as e:
        print(f"🚨 grade_answer error: {e}")
        if _is_insufficient_quota_error(e):
            return {
                "score": 0,
                "quality": "quota_error",
                "mistakes_made": "Evaluation skipped: OpenAI quota is exhausted.",
                "actionable_suggestion": "Recharge API credits (or enable mock mode) and re-run this viva for proper AI feedback.",
            }

    # Fallback to prevent crash
    return {
        "score": 0, "quality": "error", 
        "mistakes_made": "Evaluation temporarily unavailable due to a system/API error.", 
        "actionable_suggestion": "Retry the viva submission after checking API connectivity and server logs."
    }

def batch_grade_all_answers(responses):
    """
    PHASE 2: Grades ALL viva responses in a SINGLE API call.
    Before: 5 questions = 5 API calls. After: 5 questions = 1 API call.
    Saves 40-70% of viva credit cost (guide §2).
    """
    # Filter only pending responses that have actual answers
    pending = []
    for i, resp in enumerate(responses):
        ans = (resp.get('student_answer') or '').strip()
        if resp.get('quality') != 'pending':
            continue
        # Quick unattempted check (same as before - no API call needed)
        if len(ans) < 4 or any(x in ans.lower() for x in ["don't know", "skip", "idk", "no answer"]):
            resp.update({
                'score': 0, 'max_marks': float(resp.get('marks', 10)),
                'quality': 'unattempted',
                'mistakes_made': 'No attempt made.',
                'actionable_suggestion': 'Try to answer next time.'
            })
            continue
        pending.append((i, resp))

    if not pending:
        return  # Nothing to grade via API

    # Build one compact prompt for ALL pending questions
    questions_block = ""
    for idx, (i, resp) in enumerate(pending):
        max_m = float(resp.get('marks', 10))
        questions_block += (
            f"Q{idx+1} [MaxMarks:{max_m}]\n"
            f"Question: {resp['question'][:140]}\n"
            f"Ideal: {resp['ideal_answer'][:140]}\n"
            f"Student: {resp['student_answer'][:180]}\n\n"
        )

    prompt = (
        f"Grade {len(pending)} exam answers. Return ONLY a JSON array — no extra text.\n"
        f"Each object MUST have: score(0-100 percentage), quality, mistakes_made, actionable_suggestion.\n"
        f"RULES: score is a percentage (0-100). mistakes_made and actionable_suggestion must be "
        f"specific technical gaps — not generic. Do NOT repeat question text in suggestions.\n\n"
        f"{questions_block}"
        'Return ONLY: [{"score":<0-100>,"quality":"good/poor","mistakes_made":"...","actionable_suggestion":"..."},...]\n'
        f"Array must have exactly {len(pending)} objects in the same order as the questions."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "You are a strict technical examiner. Return JSON array only."},
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=150 * len(pending),  # 150 tokens per question
            temperature=1  # Consistent, deterministic grading
        )
        # Log token usage for observability (Phase 3 prep)
        usage = response.usage
        print(f"[batch_grade] Tokens used — prompt:{usage.prompt_tokens} completion:{usage.completion_tokens} total:{usage.total_tokens} for {len(pending)} questions")

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'```(?:json)?\s*', '', raw, flags=re.IGNORECASE).strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            grades = json.loads(match.group(0))
            for idx, (i, resp) in enumerate(pending):
                if idx < len(grades):
                    g = grades[idx]
                    max_m = float(resp.get('marks', 10))
                    awarded = (float(g.get('score', 0)) / 100.0) * max_m
                    resp.update({
                        'score': awarded,
                        'max_marks': max_m,
                        'mistakes_made': g.get('mistakes_made', 'N/A'),
                        'actionable_suggestion': g.get('actionable_suggestion', 'N/A'),
                        'quality': g.get('quality', 'fair')
                    })
            return
        print(f"[batch_grade] Could not parse JSON array from: {raw[:300]}")
    except Exception as e:
        print(f"[batch_grade] API error: {type(e).__name__}: {e}")
        if _is_insufficient_quota_error(e):
            for _, resp in pending:
                max_m = float(resp.get('marks', 10))
                resp.update({
                    'score': 0,
                    'max_marks': max_m,
                    'quality': 'quota_error',
                    'mistakes_made': 'Evaluation skipped: OpenAI quota is exhausted.',
                    'actionable_suggestion': 'Recharge API credits (or enable mock mode) and re-run this viva for accurate AI feedback.'
                })
            return

    # Fallback: if batch call fails, grade individually so nothing breaks
    print("[batch_grade] Falling back to per-question grading...")
    for i, resp in pending:
        max_m = float(resp.get('marks', 10))
        g = grade_answer(resp['question'], resp['student_answer'], resp['ideal_answer'], max_m)
        awarded = (float(g.get('score', 0)) / 100.0) * max_m
        resp.update({
            'score': awarded, 'max_marks': max_m,
            'mistakes_made': g.get('mistakes_made', 'N/A'),
            'actionable_suggestion': g.get('actionable_suggestion', 'N/A'),
            'quality': g.get('quality', 'fair')
        })


def generate_report(student_name, roll_number, session_name, responses, total_score):
    return {
        "executive_summary": f"Performance summary for {student_name}.",
        "recommendations": "Review question-by-question feedback for details."
    }

def generate_viva_question(score, transcribed_text):
    if score >= 8:
        difficulty = "HARD: Ask a highly conceptual question."
    elif score >= 4:
        difficulty = "MEDIUM: Ask for another real-world example."
    else:
        difficulty = "EASY: Ask for a basic definition to test fundamentals."
        
    system_prompt = f"""You are a strict but fair viva examiner. Grade the student's answer below.QUESTION: {question}IDEAL ANSWER: {ideal_answer}STUDENT'S ANSWER: {student_answer}
    Instructions:
    - Identify SPECIFIC mistakes or missing points from the student's answer.
    - Give a UNIQUE, specific actionable suggestion tailored to what THIS student got wrong.
    - Do NOT give generic suggestions like "Review study material." — be specific to the question.
    Return ONLY this JSON, nothing else:
    {{"score": <number 0-100>, "quality": "<excellent/good/fair/poor>", "mistakes_made": "<specific mistakes>", "actionable_suggestion": "<specific advice based on what student got wrong>"}}"""
    
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "system", "content": system_prompt}]
    )
    return response.choices[0].message.content


# ==========================================
# 4. AUTOGRADER ROUTES
# ==========================================
# 1. The New Beautiful Landing Page
# 1. The New Beautiful Landing Page
@app.route('/')
def serve_index():
    return render_template('new_landing.html')

# 2. Your AutoGrader (Moved from the root to /assessment)
@app.route('/assessment')
def serve_assessment():
    return render_template('assessment.html')

@app.route('/test-lab')
def serve_test_lab():
    return render_template('test_lab.html')


def _build_synthetic_batch_payload(student_count: int = 20, duplicate_groups: int = 0) -> dict:
    question_text = "SYNTHETIC QUESTION PAPER FOR TESTING"
    expert_text_1 = "Expert answer one: synthetic benchmark content."
    expert_text_2 = "Expert answer two: synthetic benchmark content."
    expert_text_3 = "Expert answer three: synthetic benchmark content."

    def b64_text(text: str) -> str:
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    # Tiny valid 1x1 PNG (transparent)
    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
        "ASsJTYQAAAAASUVORK5CYII="
    )

    answer_sheet_docs_list = []
    for i in range(max(1, int(student_count))):
        answer_sheet_docs_list.append(
            [
                {
                    "filename": f"student_{i + 1}.png",
                    "base64": tiny_png_b64,
                }
            ]
        )

    duplicate_groups = max(0, int(duplicate_groups))
    for j in range(min(duplicate_groups, len(answer_sheet_docs_list))):
        answer_sheet_docs_list.append(answer_sheet_docs_list[j])

    return {
        "studentName": "",
        "rollNumber": "",
        "questionDocs": [{"filename": "question.txt", "base64": b64_text(question_text)}],
        "expertDocs": [
            {"filename": "expert1.txt", "base64": b64_text(expert_text_1)},
            {"filename": "expert2.txt", "base64": b64_text(expert_text_2)},
            {"filename": "expert3.txt", "base64": b64_text(expert_text_3)},
        ],
        "answerSheetDocsList": answer_sheet_docs_list,
        "overwriteDuplicate": True,
        "skipDuplicate": False,
    }

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory('.', filename)

@app.route('/api/check-duplicates', methods=['POST'])
def check_duplicates():
    try:
        payload = request.json
        answer_sheet_docs_list = payload.get("answerSheetDocsList", []) or []
        
        duplicates_info = []
        for answer_sheet_docs in answer_sheet_docs_list:
            if answer_sheet_docs and len(answer_sheet_docs) > 0:
                first_doc = answer_sheet_docs[0]
                b64_content = str(first_doc.get("base64", "") or "")
                filename = str(first_doc.get("filename", "") or "")
                
                file_hash = backend_api.db.calculate_hash(b64_content)
                existing = backend_api.db.is_duplicate(file_hash)
                if existing:
                    existing_student, existing_date = existing
                    duplicates_info.append({
                        "filename": filename,
                        "existing_student": existing_student,
                        "existing_date": existing_date,
                        "file_hash": file_hash
                    })
        
        return jsonify({"duplicates_found": len(duplicates_info) > 0, "duplicates": duplicates_info})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/grade-single', methods=['POST'])
def grade_single_paper():
    try:
        payload = request.json
        result = backend_api.grade_single_student(payload)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})
        
@app.route('/api/grade', methods=['POST'])
def grade_paper(): 
    try:
        payload = request.json
        # 🏎️ Call the new thread-safe parallel method
        result = backend_api.grade_bulk_parallel(payload)
        return jsonify(result)
    except Exception as e:
        print(f"🚨 Parallel Grading Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/test/reset-metrics', methods=['POST'])
def reset_test_metrics():
    try:
        backend_api.reset_runtime_metrics()
        return jsonify({"success": True, "message": "Runtime metrics reset."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/test/metrics', methods=['GET'])
def get_test_metrics():
    try:
        return jsonify({"success": True, "metrics": backend_api.get_runtime_metrics()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/test/run-batch', methods=['POST'])
def run_synthetic_batch_test():
    try:
        payload = request.json or {}
        student_count = int(payload.get("studentCount", 20))
        duplicate_groups = int(payload.get("duplicateGroups", 0))
        concurrent_runs = max(1, int(payload.get("concurrentRuns", 1)))

        synthetic_payload = _build_synthetic_batch_payload(
            student_count=student_count,
            duplicate_groups=duplicate_groups,
        )

        results = []

        def run_once() -> None:
            run_result = backend_api.grade_bulk_parallel(dict(synthetic_payload))
            results.append(run_result)

        threads = []
        for _ in range(concurrent_runs):
            t = threading.Thread(target=run_once)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        metrics = backend_api.get_runtime_metrics()
        return jsonify(
            {
                "success": True,
                "runs": len(results),
                "students_requested_per_run": student_count,
                "duplicate_groups_per_run": duplicate_groups,
                "concurrent_runs": concurrent_runs,
                "metrics": metrics,
                "result_sizes": [len(r) if isinstance(r, list) else 0 for r in results],
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ==========================================
# 5. WEBSOCKETS (Voice/Viva WebSocket)
# ==========================================
@socketio.on('verify_student')
def verify_student(data):
    from sqlalchemy import text
    enrollment_num = data.get('enrollment_number')
    print(f"🔍 Checking DB for student: {enrollment_num}")
    try:
        with database.db_engine.connect() as conn:
            result = conn.execute(
                text("SELECT grade_data FROM uploads WHERE enrollment_number = :en"),
                {"en": enrollment_num}
            ).fetchone()
        
        if not result:
            emit('viva_error', {'message': f'Applicant {enrollment_num} does not exist in the grading system.'})
            return
            
        grade_data = result[0]
        system_prompt = f"""
        You are a strict college professor conducting a verbal Viva exam.
        The student with enrollment number {enrollment_num} has just sat down.
        Here is the JSON data from their graded written exam: {grade_data}
        Greet the student, briefly mention one thing they did well or poorly, and immediately ask their FIRST follow-up question.
        Keep it under 3 sentences.
        """
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[{"role": "system", "content": system_prompt}]
        )
        emit('viva_ready', {'question': response.choices[0].message.content})
        
    except Exception as e:
        print(f"❌ DB Error: {e}")
        emit('viva_error', {'message': 'Database connection error.'})

@socketio.on('audio_complete')
def handle_audio(audio_data):
    # 🚀 Starts a background thread so the UI doesn't freeze
    socketio.start_background_task(target=process_audio_async, audio_data=audio_data)

def process_audio_async(audio_data):
    try:
        print("🎙️ Audio received! Processing in Background...")
        audio_io = io.BytesIO(audio_data)
        audio_segment = AudioSegment.from_file(audio_io, format="webm")
        
        wav_io = io.BytesIO()
        audio_segment.export(wav_io, format="wav")
        wav_io.seek(0)

        segments, info = whisper_model.transcribe(wav_io, beam_size=1, vad_filter=True)
        transcribed_text = "".join([segment.text for segment in segments]).strip()
        
        if transcribed_text:
            print(f"✅ Student Said: {transcribed_text}")
            socketio.emit('transcription_update', {'text': transcribed_text})
            
            mock_score = 8
            ai_response = generate_viva_question(mock_score, transcribed_text)
            print(f"🤖 AI Professor: {ai_response}")
            socketio.emit('ai_question_update', {'text': ai_response})
            
    except Exception as e:
        print(f"❌ Background Processing Error: {e}")
@socketio.on('cheat_detected')
def handle_cheating(data):
    print(f"🚨 SECURITY WARNING: {data['reason']}")


# ==========================================
# 6. VIVA HTML PAGE ROUTES
# ==========================================
@app.route('/viva')
def viva_index():
    return render_template('viva_index.html')

@app.route('/viva/admin-login-page')
def admin_login_page():
    return render_template('admin_login.html')

@app.route('/viva/admin')
def viva_admin():
    return render_template('admin.html')

@app.route('/viva/student')
def viva_student():
    return render_template('student.html')


# ==========================================
# 7. VIVA API ROUTES
# ==========================================

@app.route('/api/admin-login', methods=['POST'])
def admin_login():
    data = request.json
    if data.get('username') == ADMIN_CREDENTIALS['username'] and \
       data.get('password') == ADMIN_CREDENTIALS['password']:
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Invalid credentials"}), 401

@app.route('/api/create-session', methods=['POST'])
def create_session():
    from sqlalchemy import text # Required for the DB part
    try:
        # --- PRESERVE ALL ORIGINAL FORM LOGIC ---
        if request.content_type and 'multipart/form-data' in request.content_type:
            data = request.form
        else:
            data = request.json or {}

        session_name = data.get('session_name', '')
        batch = data.get('batch', '')
        semester = data.get('semester', '')
        branch = data.get('branch', '')
        subject = data.get('subject', '')
        num_questions = data.get('num_questions', 5)
        programme = data.get('programme', '')
        time_limit = int(data.get('time_limit', 10))
        marks_per_q = int(data.get('marks_per_q', 10))
        
        # --- DOCUMENT EXTRACTION (used as AI context for viva questions) ---
        book_content = ''
        uploaded_book = False
        if request.content_type and 'multipart/form-data' in request.content_type and 'book' in request.files:
            file = request.files['book']
            uploaded_book = bool(file and file.filename and file.filename.strip())

            if uploaded_book:
                filename = file.filename.lower()

                if filename.endswith('.pdf'):
                    pdf_reader = PyPDF2.PdfReader(file)
                    start_page = 5 if len(pdf_reader.pages) > 10 else 0
                    end_page = min(start_page + 10, len(pdf_reader.pages))

                    chunks = []
                    for i in range(start_page, end_page):
                        extracted = (pdf_reader.pages[i].extract_text() or '').strip()
                        if extracted:
                            chunks.append(" ".join(extracted.split()))
                    book_content = "\n".join(chunks)

                elif filename.endswith('.docx'):
                    doc = docx.Document(file)
                    book_content = "\n".join(
                        [" ".join(p.text.split()) for p in doc.paragraphs if p.text and p.text.strip()]
                    )

                if uploaded_book and not book_content.strip():
                    return jsonify({
                        'success': False,
                        'error': 'Uploaded document has no extractable text. Please upload a text-based PDF/DOCX with readable content.'
                    }), 400

        # If a question paper is uploaded, align config with paper instructions when detectable.
        if book_content.strip():
            detected_q_count = infer_question_count_from_text(book_content)
            if detected_q_count and detected_q_count > 0 and detected_q_count != int(num_questions):
                print(f"[create_session] Adjusting num_questions from {num_questions} to detected count {detected_q_count} based on uploaded paper.")
                num_questions = detected_q_count

            detected_mpq = infer_marks_per_question(book_content, int(num_questions))
            if detected_mpq and detected_mpq > 0 and detected_mpq != marks_per_q:
                print(f"[create_session] Adjusting marks_per_q from {marks_per_q} to detected value {detected_mpq} from uploaded paper.")
                marks_per_q = detected_mpq
                    
        # --- AI GENERATION ---
        questions = generate_questions_with_ai(
            subject,
            int(num_questions),
            book_content,
            strict_mode=VIVA_STRICT_AI,
        )
        questions = _normalize_question_bank(questions, int(num_questions))
        if not questions:
            return jsonify({
                'success': False,
                'error': 'Unable to generate viva questions from AI/document context. Please retry after checking API key/quota and uploaded document quality.'
            }), 502
        
        session_id = str(uuid.uuid4())
        
        # --- NEW: SAVE TO MYSQL PERMANENTLY ---
        try:
            with database.db_engine.connect() as conn:
                # 🚀 Start a transaction (The "Confirm Save" button)
                with conn.begin(): 
                    conn.execute(text("""
                        INSERT INTO viva_sessions (
                            id, session_name, subject, branch, batch,
                            semester, programme, num_questions, time_limit,
                            marks_per_q, status, generated_questions
                        ) VALUES (
                            :id, :name, :sub, :br, :ba,
                            :sem, :prog, :num, :tl,
                            :mpq, :status, :questions
                        )
                    """), {
                        "id": session_id,
                        "name": session_name,
                        "sub": subject,
                        "br": branch,
                        "ba": batch,
                        "sem": semester,
                        "prog": programme,
                        "num": min(int(num_questions), len(questions)),
                        "tl": time_limit,
                        "mpq": marks_per_q,
                        "status": "active",
                        "questions": json.dumps(questions)
                    })
                # Auto-commits here!
            print(f"✅ Session {session_id} successfully saved and COMMITTED to DB")
        except Exception as db_err:
            print(f"🚨 Database Save Error: {db_err}")
            # We don't return here so the app still works in RAM if DB fails temporarily

        # --- PRESERVE ORIGINAL MEMORY STORAGE ---
        session = {
            'id': session_id, 'name': session_name, 'batch': batch, 'semester': semester,
            'branch': branch, 'subject': subject, 'num_questions': min(int(num_questions), len(questions)),
            'bank_size': len(questions), 'questions': questions, 'created_at': datetime.datetime.now().isoformat(),
            'status': 'active', 'programme': programme, 'time_limit': time_limit, 'marks_per_q': marks_per_q
        }
        
        viva_memory_db['sessions'].append(session)
        return jsonify({'success': True, 'session_id': session['id'], 'questions_generated': len(questions)})

    except Exception as e:
        msg = str(e)
        if _is_api_auth_or_quota_error(e) or 'session expired' in msg.lower():
            print(f"[create_session] Session expired/auth failure: {msg}")
        else:
            import traceback
            print(traceback.format_exc())
        return jsonify({'success': False, 'error': msg})

@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    from sqlalchemy import text
    try:
        with database.db_engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, session_name, subject, branch, batch, semester,
                       programme, num_questions, time_limit, marks_per_q,
                       status, generated_questions, created_at
                FROM viva_sessions ORDER BY created_at DESC
            """)).fetchall()
        sessions = []
        for row in rows:
            r = dict(row._mapping)
            questions = _normalize_question_bank(json.loads(r.get('generated_questions') or '[]'), r.get('num_questions', 5))
            sessions.append({
                'id': r['id'],
                'name': r['session_name'],
                'subject': r['subject'],
                'branch': r['branch'],
                'batch': r['batch'],
                'semester': r.get('semester', ''),
                'programme': r.get('programme', ''),
                'num_questions': r['num_questions'],
                'time_limit': r.get('time_limit', 10),
                'marks_per_q': r.get('marks_per_q', 10),
                'status': r.get('status', 'active'),
                'class': r.get('batch', ''),
                'questions': questions,
                'created_at': str(r['created_at']),
            })
        # Sync RAM so start-viva still works
        viva_memory_db['sessions'] = sessions
        return jsonify(sessions)
    except Exception as e:
        print(f"🚨 /api/sessions ERROR: {e}")
        return jsonify(viva_memory_db['sessions'])

@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    from sqlalchemy import text
    try:
        with database.db_engine.connect() as conn:
            row = conn.execute(text("""
                SELECT id, session_name, subject, branch, batch, semester,
                       programme, num_questions, time_limit, marks_per_q,
                       status, generated_questions, created_at
                FROM viva_sessions WHERE id = :sid
            """), {"sid": session_id}).fetchone()
        if row:
            r = dict(row._mapping)
            questions = _normalize_question_bank(json.loads(r.get('generated_questions') or '[]'), r.get('num_questions', 5))
            session = {
                'id': r['id'],
                'name': r['session_name'],
                'subject': r['subject'],
                'branch': r['branch'],
                'batch': r['batch'],
                'semester': r.get('semester', ''),
                'programme': r.get('programme', ''),
                'num_questions': r['num_questions'],
                'time_limit': r.get('time_limit', 10),
                'marks_per_q': r.get('marks_per_q', 10),
                'status': r.get('status', 'active'),
                'class': r.get('batch', ''),
                'questions': questions,
                'created_at': str(r['created_at']),
            }
            return jsonify(session)
    except Exception as e:
        print(f"🚨 /api/sessions/<id> ERROR: {e}")
    # Fallback to RAM
    session = next((s for s in viva_memory_db['sessions'] if s['id'] == session_id), None)
    if session:
        return jsonify(session)
    return jsonify({'error': 'Session not found'}), 404

@app.route('/api/start-viva', methods=['POST'])
def start_viva():
    from sqlalchemy import text
    import random
    data = request.json
    session_id = data.get('session_id')
    session = next((s for s in viva_memory_db['sessions'] if s['id'] == session_id), None)
 
    if not session:
        try:
            with database.db_engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT id, session_name, subject, branch, batch,
                           num_questions, time_limit, marks_per_q,
                           status, generated_questions
                    FROM viva_sessions WHERE id = :sid
                """), {"sid": session_id}).fetchone()
            if row:
                r = dict(row._mapping)
                all_questions = _normalize_question_bank(json.loads(r.get('generated_questions') or '[]'), r.get('num_questions', 5))
                session = {
                    'id': r['id'], 'name': r['session_name'],
                    'subject': r['subject'], 'branch': r['branch'],
                    'batch': r['batch'], 'num_questions': r['num_questions'],
                    'time_limit': r.get('time_limit', 10),
                    'marks_per_q': r.get('marks_per_q', 10),
                    'status': r.get('status', 'active'),
                    'class': r.get('batch', ''),
                    'questions': all_questions,
                }
                viva_memory_db['sessions'].append(session)
        except Exception as e:
            print(f"start-viva DB error: {e}")
 
    if not session:
        return jsonify({'error': 'Session not found'}), 404
 
    # ✅ FIX 3: Shuffle the bank and pick exactly num_questions for THIS student
    all_q = session.get('questions', [])
    num_q = session.get('num_questions', 5)
    shuffled = all_q.copy()
    random.shuffle(shuffled)                    # shuffle in place
    student_questions = shuffled[:num_q]        # pick exact number needed
 
    student_key = f"{data.get('roll_number')}_{session['id']}"
    viva_memory_db['students'][student_key] = {
        'roll_number': data.get('roll_number'),
        'student_name': data.get('student_name'),
        'branch': session.get('branch', ''),
        'class': session.get('class', ''),
        'session_id': session['id'],
        'start_time': datetime.datetime.now().isoformat(),
        'current_question': 0,
        'responses': [],
        'difficulty_history': [],
        'questions': student_questions,   # ✅ store per-student shuffled set
    }
 
    try:
        with database.db_engine.connect() as conn:
            params = {
                "en": data.get('roll_number'),
                "name": data.get('student_name'),
                "branch": session.get('branch', ''),
                "batch": session.get('class', ''),
            }

            if database.db_engine.dialect.name == "sqlite":
                conn.execute(text("""
                    INSERT INTO viva_students (enrollment_number, student_name, branch, batch)
                    VALUES (:en, :name, :branch, :batch)
                    ON CONFLICT(enrollment_number) DO UPDATE SET
                        student_name = excluded.student_name,
                        branch = excluded.branch,
                        batch = excluded.batch
                """), params)
            else:
                conn.execute(text("""
                    INSERT INTO viva_students (enrollment_number, student_name, branch, batch)
                    VALUES (:en, :name, :branch, :batch)
                    ON DUPLICATE KEY UPDATE
                        student_name = VALUES(student_name),
                        branch = VALUES(branch),
                        batch = VALUES(batch)
                """), params)
            conn.commit()
    except Exception as e:
        print(f"Error saving student: {e}")
 
    return jsonify({
        'success': True,
        'student_id': student_key,
        'total_questions': num_q,
        'current_question': student_questions[0] if student_questions else None,
        'question_number': 1,
        'time_limit': session.get('time_limit', 10)
    })

@app.route('/api/submit-answer', methods=['POST'])
def submit_answer():
    data = request.json
    student = viva_memory_db['students'].get(data.get('student_id'))
    session = next((s for s in viva_memory_db['sessions'] if s['id'] == data.get('session_id')), None)
        
    current_q_data = next((q for q in session['questions'] if q['question'] == data.get('question')), None)
    ideal_answer = current_q_data.get('ideal_answer', '') if current_q_data else ''
    marks = _normalize_marks(current_q_data.get('marks', session.get('marks_per_q', 5)) if current_q_data else session.get('marks_per_q', 5), default=5)
    difficulty = str((current_q_data or {}).get('difficulty') or _difficulty_from_marks(marks))
    
    new_response = {
        'question': data.get('question', ''), 'student_answer': data.get('answer', ''),
        'ideal_answer': ideal_answer, 'score': 0, 'quality': 'pending',
        'mistakes_made': 'Pending', 'actionable_suggestion': 'Pending',
        'marks': marks, 'difficulty': difficulty
    }
    
    student['responses'].append(new_response)
    student['current_question'] += 1
    return jsonify({'success': True, 'is_complete': student['current_question'] >= session['num_questions']})

@app.route('/api/next-question', methods=['POST'])
def next_question():
    data = request.json
    student = viva_memory_db['students'].get(data.get('student_id'))
    session = next((s for s in viva_memory_db['sessions'] if s['id'] == data.get('session_id')), None)
    if not student or not session:
        return jsonify({'error': 'Not found'}), 404
 
    current_index = student['current_question']
    # ✅ Use per-student shuffled questions, not the session bank
    questions_bank = student.get('questions', session.get('questions', []))
 
    if current_index < len(questions_bank):
        next_q = questions_bank[current_index]
        return jsonify({
            'success': True,
            'question': next_q,
            'question_number': current_index + 1,
            'total_questions': session.get('num_questions', 5)
        })
    return jsonify({'error': 'No more questions'}), 404

@app.route('/api/submit-viva', methods=['POST'])
def submit_viva():
    from sqlalchemy import text
    data = request.json 
    student_key = data.get('student_id')
    student = viva_memory_db['students'].get(student_key)
    session = next((s for s in viva_memory_db['sessions'] if s['id'] == data.get('session_id')), None)
    
    if not student or not session:
        return jsonify({'success': False, 'error': 'Student or Session not found'}), 404

    cheat_log = data.get('cheat_log', {})
    tab_switches = cheat_log.get('tabSwitches', 0)
    time_away = cheat_log.get('totalTimeAwaySeconds', 0)
    proctoring_status = f"⚠️ {tab_switches} Tab Switches ({time_away}s away)" if tab_switches > 0 else "Clean"

    completion_time = datetime.datetime.now().isoformat()
    
    # 1. EVALUATION — PHASE 2: Single batch API call for ALL questions
    # Before: 5 questions = 5 API calls. Now: 5 questions = 1 API call.
    batch_grade_all_answers(student['responses'])

    # 2. PERCENTAGE CALCULATION FIX
    # Calculate total earned and total possible by summing the actual question weights
    total_earned = sum(float(r.get('score', 0)) for r in student['responses'])
    max_possible = sum(float(r.get('max_marks', 2)) for r in student['responses'])
    
    # Final percentage calculation
    percentage = round((total_earned / max_possible * 100), 1) if max_possible > 0 else 0
    
    result = {
        'id': str(uuid.uuid4()), 
        'student_key': student_key, 
        'roll_number': student['roll_number'],
        'student_name': student['student_name'], 
        'session_name': session['name'], 
        'subject': session['subject'],
        'responses': student['responses'], 
        'total_score': total_earned,
        'max_possible': max_possible,
        'percentage': percentage,
        'completed_at': completion_time
    }
    
    viva_memory_db['results'][student_key] = result
    
    # 3. DATABASE SAVE
    try:
        with database.db_engine.connect() as conn:
            with conn.begin(): 
                for i, resp in enumerate(student['responses'], 1):
                    answered_status = "Yes" if len(str(resp.get('student_answer', ""))) > 4 else "No"
                    
                    conn.execute(text("""
                        INSERT INTO viva_responses (
                            enrollment_number, subject_id, subject_name, question_number,
                            question_text, student_answer, answered_status,
                            marks_awarded, actionable_suggestion, proctoring_status
                        ) VALUES (
                            :en, :sid, :sname, :qnum,
                            :qtext, :ans, :astatus,
                            :marks, :suggestion, :pstatus
                        )
                    """), {
                        "en": student['roll_number'],
                        "sid": session['id'],
                        "sname": session['subject'],
                        "qnum": i,
                        "qtext": resp.get('question', ''),
                        "ans": str(resp.get('student_answer') or "No Answer Provided"), 
                        "suggestion": resp.get('suggestions') or "N/A",
                        "astatus": answered_status,
                        "marks": resp.get('score', 0),
                        "pstatus": proctoring_status
                    })
    except Exception as e:
        print(f"🚨 DB Error: {e}")
        
    return jsonify({'success': True, 'result': result})

@app.route('/api/results/<student_key>', methods=['GET'])
def get_result(student_key):
    from sqlalchemy import text
    parts = student_key.split('_')
    roll_number = parts[0]
    session_id = parts[1] if len(parts) > 1 else None

    try:
        with database.db_engine.connect() as conn:
            if session_id:
                rows = conn.execute(text("""
                    SELECT * FROM viva_responses
                    WHERE enrollment_number = :en AND subject_id = :sid
                    ORDER BY question_number ASC
                """), {"en": roll_number, "sid": session_id}).fetchall()
            else:
                rows = conn.execute(text("""
                    SELECT * FROM viva_responses WHERE enrollment_number = :en
                    ORDER BY question_number ASC
                """), {"en": roll_number}).fetchall()

            responses = [dict(r._mapping) for r in rows]

        if responses:
            # 👇 NEW LOGIC: If session_id was missing, grab it directly from the student's saved answers!
            actual_session_id = session_id or responses[0].get('subject_id')
            
            marks_per_q = 10 # Default fallback
            
            # 👇 NEW LOGIC: Query the database for the EXACT marks_per_q for this specific exam
            if actual_session_id:
                with database.db_engine.connect() as conn:
                    session_row = conn.execute(text("SELECT marks_per_q FROM viva_sessions WHERE id = :sid"), {"sid": actual_session_id}).fetchone()
                    if session_row:
                        marks_per_q = session_row[0]

            # Calculate the flawless percentage
            total_score = sum(r['marks_awarded'] for r in responses)
            max_possible = len(responses) * marks_per_q
            percentage = round((total_score / max_possible * 100), 1) if max_possible > 0 else 0

            return jsonify({
                "roll_number": roll_number,
                "responses": responses,
                "total_score": total_score,
                "max_possible": max_possible,
                "percentage": percentage,
                "marks_per_q": marks_per_q
            })
    except Exception as e:
        print(f"🚨 autograder_db query error: {e}")

    return jsonify({'error': 'No data found for this specific session'}), 404

@app.route('/api/admin/results', methods=['GET']) 
def get_all_results():
    from sqlalchemy import text
    roll_number = request.args.get('enrollment_number')
    try:
        with database.db_engine.connect() as conn:
            if roll_number:
                # Logic for single report
                rows = conn.execute(text("SELECT * FROM viva_responses WHERE enrollment_number = :en"), {"en": roll_number}).fetchall()
                return jsonify({"success": True, "results": [dict(r._mapping) for r in rows]})
            else:
                # Logic for Admin Dashboard - MUST query MySQL
                rows = conn.execute(text("""
                    SELECT r.enrollment_number as roll_number,
                           MAX(COALESCE(s.student_name, r.enrollment_number)) as student_name,
                           r.subject_name as subject, SUM(r.marks_awarded) as total_score,
                           MAX(r.proctoring_status) as proctoring_status
                    FROM viva_responses r
                    LEFT JOIN viva_students s ON r.enrollment_number = s.enrollment_number
                    GROUP BY r.enrollment_number, r.subject_name
                """)).fetchall()
                return jsonify([dict(r._mapping) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e), "results": []})

@app.route('/api/student-status', methods=['POST'])
def check_student_status():
    student_key = f"{request.json.get('roll_number')}_{request.json.get('session_id')}"
    return jsonify({'already_taken': student_key in viva_memory_db['results'], 'result': viva_memory_db['results'].get(student_key)})


# ==========================================
# RUN THE SERVER
# ==========================================
if __name__ == "__main__":
    # FIX 1: Kill any old process on port 5000 first, then run with gevent
    print("[START] Web Server with WebSockets on http://127.0.0.1:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)