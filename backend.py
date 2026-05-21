import base64
import io
import json
import os
import pathlib
import re
import hashlib  # <--- ADDED: To generate file fingerprints
import datetime # <--- ADDED: To track time
import time
from typing import Any, Dict, List
import fitz
import webview
from docx import Document
from openai import OpenAI
from dotenv import load_dotenv
import database
import boto3
import asyncio ## <-- recently added 
import threading
import concurrent.futures

def upload_file_to_s3(file_bytes, filename):
    """Uploads a file to an S3 bucket and returns the public URL."""
    print(f"🕵️ DEBUG CHECK - Region: {os.getenv('AWS_REGION')} | Key: {os.getenv('AWS_ACCESS_KEY_ID')}")
    try:
        s3 = boto3.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION")
        )
        bucket_name = os.getenv("AWS_S3_BUCKET")
        
        # Upload the file
        s3.put_object(
            Bucket=bucket_name, 
            Key=filename, 
            Body=file_bytes,
            ContentType='application/pdf'
        )
        
        # This creates the link: https://.../filename.pdf
        s3_url = f"{os.getenv('AWS_S3_URL')}/{filename}"
        return s3_url
    except Exception as e:
        print(f"❌ S3 Upload Error: {e}")
        return None

load_dotenv()

# --- DATABASE MANAGER CLASS (NEW) ---
class DatabaseManager:
    def __init__(self):
        self._init_table()

    def _get_connection(self):
        if database.db_engine is None:
            raise Exception("🚨 Database Engine is not initialized. Please check your .env file and MySQL service.")
        return database.db_engine.raw_connection()

    def _init_table(self):
        """Creates the uploads table with ALL the new client columns."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            is_sqlite = database.db_engine.dialect.name == "sqlite"
            if is_sqlite:
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash VARCHAR(255) UNIQUE NOT NULL,
                    student_name TEXT,
                    filename TEXT,
                    enrollment_number VARCHAR(100),
                    subject_code VARCHAR(100),
                    program VARCHAR(100),
                    branch VARCHAR(100),
                    batch VARCHAR(100),
                    subject VARCHAR(255),
                    academic_session VARCHAR(100),
                    copy_number VARCHAR(100),
                    upload_date TEXT,
                    grade_data TEXT
                )
                ''')
            else:
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS uploads (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    file_hash VARCHAR(255) UNIQUE NOT NULL,
                    student_name TEXT,
                    filename TEXT,
                    enrollment_number VARCHAR(100),
                    subject_code VARCHAR(100),
                    program VARCHAR(100),
                    branch VARCHAR(100),
                    batch VARCHAR(100),
                    subject VARCHAR(255),
                    academic_session VARCHAR(100),
                    copy_number VARCHAR(100),
                    upload_date TEXT,
                    grade_data LONGTEXT
                )
                ''')
            conn.commit()
        finally:
            conn.close()

    def calculate_hash(self, base64_string):
        """Generates a unique MD5 fingerprint for the file content."""
        if not base64_string:
            return None
        return hashlib.md5(base64_string.encode('utf-8')).hexdigest()

    def is_duplicate(self, file_hash):
        """Checks the database to see if this exact file has already been uploaded."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            is_sqlite = database.db_engine.dialect.name == "sqlite"
            placeholder = "?" if is_sqlite else "%s"
            # Look for the file hash in the database
            cursor.execute(f"SELECT student_name, upload_date FROM uploads WHERE file_hash = {placeholder}", (file_hash,))
            result = cursor.fetchone()
            
            # If we found it, return the student name and date for the SweetAlert popup!
            if result:
                return result[0], result[1] 
            
            return None
        except Exception as e:
            print(f"❌ DB Duplicate Check Error: {e}")
            return None
        finally:
            conn.close() 

    def save_record(self, file_hash, student_name, filename, s3_url, grade_data):
        """Saves the graded result and extracts the specific columns for SQL Workbench."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            is_sqlite = database.db_engine.dialect.name == "sqlite"
            upload_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            grade_json_str = json.dumps(grade_data)
            stored_filename = s3_url or filename
            
            # Extract the specific fields the AI found
            e_no = grade_data.get("enrollment_number", "Not Found")
            s_code = grade_data.get("subject_code", "Not Found")
            prog = grade_data.get("program", "Not Found")
            br = grade_data.get("branch", "Not Found")
            bat = grade_data.get("batch", "Not Found")
            sub = grade_data.get("subject", "Not Found")
            sess = grade_data.get("academic_session", "Not Found")
            c_no = grade_data.get("copy_number", "Not Found")

            params = (
                file_hash,
                student_name,
                stored_filename,
                e_no,
                s_code,
                prog,
                br,
                bat,
                sub,
                sess,
                c_no,
                upload_date,
                grade_json_str,
            )

            # Insert into database. If the file already exists, overwrite it.
            if is_sqlite:
                cursor.execute('''
                    INSERT INTO uploads (file_hash, student_name, filename, enrollment_number, subject_code, program, branch, batch, subject, academic_session, copy_number, upload_date, grade_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_hash) DO UPDATE SET
                        student_name = excluded.student_name,
                        filename = excluded.filename,
                        enrollment_number = excluded.enrollment_number,
                        subject_code = excluded.subject_code,
                        program = excluded.program,
                        branch = excluded.branch,
                        batch = excluded.batch,
                        subject = excluded.subject,
                        academic_session = excluded.academic_session,
                        copy_number = excluded.copy_number,
                        upload_date = excluded.upload_date,
                        grade_data = excluded.grade_data
                ''', params)
            else:
                cursor.execute('''
                    INSERT INTO uploads (file_hash, student_name, filename, enrollment_number, subject_code, program, branch, batch, subject, academic_session, copy_number, upload_date, grade_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        student_name=VALUES(student_name),
                        filename=VALUES(filename),
                        enrollment_number=VALUES(enrollment_number),
                        subject_code=VALUES(subject_code),
                        program=VALUES(program),
                        branch=VALUES(branch),
                        batch=VALUES(batch),
                        subject=VALUES(subject),
                        academic_session=VALUES(academic_session),
                        copy_number=VALUES(copy_number),
                        upload_date=VALUES(upload_date),
                        grade_data=VALUES(grade_data)
                ''', params)
            
            conn.commit()
        except Exception as e:
            print(f"❌ DB Save Error: {e}")
        finally:
            conn.close()
# --- END DATABASE MANAGER ---

BATCH_SYSTEM_PROMPT = """
You are an expert exam autograder and data extraction tool.

CRITICAL INSTRUCTION - STEP 1: SCAN QUESTION PAPER INSTRUCTIONS
Before evaluating any answers, you MUST read the Question Paper document.
- THE QUESTION PAPER IS THE ABSOLUTE GROUND TRUTH. 
- Count the exact number of questions present on the official Question Paper. 
- MAP ANSWERS TO OFFICIAL QUESTIONS: You must map the student's answers to the questions found on the official Question Paper. Use your best judgment to link a student's answer to the correct official question, even if the student's numbering is messy, missing, or slightly different (e.g., 'Answer 1', 'Sol 1', 'Q.1'). If the Question Paper has 5 questions, your output should aim to have results for Q1 through Q5. Do not invent completely new questions (like Q6) unless the student has explicitly written out a brand new question that is not on the test.
- Identify the choice pattern (e.g., "Attempt any 6 Questions out of 10").
- IDENTIFY MARKS PER QUESTION: Look for a multiplication pattern like "6X10=60" or "5X20=100". 
- If such a pattern exists (e.g., 6X10), the second number (10) is the EXACT marks per question. 
- ONLY if no such pattern exists, divide the Max Marks by the number of questions to be attempted.
- Never assume fixed marks from past examples. Derive marks only from the uploaded question paper.

CRITICAL INSTRUCTION - STEP 2: READ THE COVER PAGE (PAGE 1)
The first page of the student's PDF is a SAGE UNIVERSITY cover page. You MUST use your vision capabilities to read the handwritten ink. Extract these exact fields. If messy, make your best logical guess. Do NOT output 'Not Found' unless the page is completely blank.
- Copy Number: The 6-digit number at the top right next to "S. No." (e.g., 314639, 314634)
- Enrollment Number: (e.g., 24MGT2BBA0164, 24MGT2BBA0099)
- Program: (e.g., BBA, MBA)
- Branch: (e.g., Core)
- Batch: (e.g., 2024-27)
- Subject: (e.g., Quantitative & Qualitative Aptitude)
- Subject Code: (e.g., MGTDSQQA001P)
- Academic Session: (e.g., 2025-26)

CRITICAL INSTRUCTION - STEP 3: GRADE THE EXAM & FEEDBACK QUALITY
- SCORE CEILING: The 'score' awarded MUST NOT exceed the 'max_marks' identified for that specific question. If a question is worth 10 marks, the score must be between 0 and 10.
- TECHNICAL PRECISION: For each question, evaluate the technical accuracy against standard academic definitions.
- UNIQUE FEEDBACK (ANTI-REPETITION): You are STRICTLY FORBIDDEN from using the same 'suggestions' or 'explanation' for different questions. 
    - WRONG: "Revisit the concept of [Question Text] in your notes." (This is what you have now).
    - RIGHT: "The student correctly identified the syntax but failed to explain the memory allocation logic of Unions. Study memory offsets."
- SUGGESTIONS: This must be a specific technical concept or a bridge to a related sub-topic, not just a repeat of the question.

OUTPUT FORMAT & THE SKELETON RULE:
You MUST return ONLY valid JSON. Use EXACTLY these lowercase keys.

*** CRITICAL ARRAY ENFORCEMENT ***
1. Count the exact number of questions on the official Question Paper.
2. Your "results" array MUST contain EXACTLY that many objects. If the Question Paper has 5 questions, your array MUST contain exactly 5 objects (Q1, Q2, Q3, Q4, Q5).
3. Do NOT lump multiple answers into a single question object.
4. If a student forgot to number their first answer (e.g., they just start writing the definition of a rational number), use your logical deduction to map it to Q1. 

{
  "student_name": "name",
  "copy_number": "string",
  "enrollment_number": "string",
  "program": "string",
  "branch": "string",
  "batch": "string",
  "subject": "string",
  "subject_code": "string",
  "academic_session": "string",
  "results": [
    {
      "question_id": "string",
      "question_text": "text",
      "page_numbers": [1, 2],
      "score": <number representing awarded marks>,
      "max_marks": <number representing calculated max marks per question>,
      "explanation": "text",
      "coverage_summary": "text",
      "suggestions": "text"
    }
  ]
}
"""
class Backend:
    """
    This class is exposed to the JS layer via pywebview (js_api=Backend()).
    Methods here can be called from JS as window.pywebview.api.<method>.
    """

    def __init__(self, model_name: str = "gpt-5-mini"):
        self.model_name = model_name
        self._api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.mock_mode = os.getenv("MOCK_GRADING", "0").strip().lower() in {"1", "true", "yes", "on"}
        self._client = OpenAI(api_key=self._api_key, timeout=300.0) if self._api_key else None
        
        # Initialize Database Manager
        self.db = DatabaseManager() 
        
        # 🛑 ADDED: The AWS Bouncer to protect OpenAI Rate Limits
        self.gpt_semaphore = threading.Semaphore(3)

        self._metrics_lock = threading.Lock()
        self._runtime_metrics = {
            "mock_mode": self.mock_mode,
            "grade_bulk_parallel_calls": 0,
            "grade_full_paper_calls": 0,
            "openai_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "recent_bulk_runs": [],
        }

    def reset_runtime_metrics(self) -> None:
        with self._metrics_lock:
            self._runtime_metrics = {
                "mock_mode": self.mock_mode,
                "grade_bulk_parallel_calls": 0,
                "grade_full_paper_calls": 0,
                "openai_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "recent_bulk_runs": [],
            }

    def get_runtime_metrics(self) -> Dict[str, Any]:
        with self._metrics_lock:
            snapshot = dict(self._runtime_metrics)
            snapshot["recent_bulk_runs"] = list(self._runtime_metrics.get("recent_bulk_runs", []))
        return snapshot

    def _record_token_usage(self, input_tokens: int, output_tokens: int, total_tokens: int) -> None:
        with self._metrics_lock:
            self._runtime_metrics["openai_calls"] += 1
            self._runtime_metrics["input_tokens"] += max(0, int(input_tokens))
            self._runtime_metrics["output_tokens"] += max(0, int(output_tokens))
            self._runtime_metrics["total_tokens"] += max(0, int(total_tokens))

    def _record_bulk_run(self, requested: int, unique: int, results: int, elapsed_ms: int) -> None:
        run = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "requested_students": requested,
            "unique_students": unique,
            "results_returned": results,
            "elapsed_ms": elapsed_ms,
        }
        with self._metrics_lock:
            history = self._runtime_metrics["recent_bulk_runs"]
            history.append(run)
            if len(history) > 20:
                del history[:-20]

    @staticmethod
    def _build_mock_model_output(student_name: str) -> Dict[str, Any]:
        results = []
        for i in range(1, 6):
            results.append(
                {
                    "question_id": f"Q{i}",
                    "question_text": f"Mock question {i}",
                    "page_numbers": [i],
                    "score": 7,
                    "max_marks": 10,
                    "explanation": f"Mock explanation for question {i}.",
                    "coverage_summary": f"Mock coverage summary for question {i}.",
                    "suggestions": f"Mock suggestion for question {i}.",
                }
            )
        return {
            "student_name": student_name,
            "copy_number": "MOCK-COPY",
            "enrollment_number": "MOCK-ENROLL",
            "program": "MOCK-PROGRAM",
            "subject": "MOCK-SUBJECT",
            "branch": "MOCK-BRANCH",
            "batch": "MOCK-BATCH",
            "subject_code": "MOCK-CODE",
            "academic_session": "MOCK-SESSION",
            "results": results,
        }

    def save_pdf_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        pdf_b64 = (payload.get("pdfBase64", "") or "").strip()
        suggested_filename = (payload.get("suggestedFilename", "") or "").strip()

        if not pdf_b64:
            return {"error": "Missing pdfBase64."}

        if not suggested_filename:
            suggested_filename = "examination-report.pdf"
        if not suggested_filename.lower().endswith(".pdf"):
            suggested_filename += ".pdf"

        try:
            pdf_bytes = base64.b64decode(pdf_b64)
        except Exception as e:
            return {"error": f"Failed to decode pdfBase64: {e}"}

        if not webview.windows:
            return {"error": "No active app window available to show a save dialog."}

        window = webview.windows[0]
        try:
            save_path = window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=suggested_filename,
                file_types=("PDF Files (*.pdf)",),
            )
        except Exception as e:
            return {"error": f"Failed to open save dialog: {e}"}

        if not save_path:
            return {"error": "Save cancelled."}

        if isinstance(save_path, (list, tuple)):
            save_path = save_path[0] if save_path else ""

        try:
            with open(save_path, "wb") as f:
                f.write(pdf_bytes)
        except Exception as e:
            return {"error": f"Failed to write PDF: {e}"}

        return {"ok": True, "path": save_path}

    def save_csv_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        csv_b64 = (payload.get("csvBase64", "") or "").strip()
        suggested_filename = (payload.get("suggestedFilename", "") or "").strip()

        if not csv_b64:
            return {"error": "Missing csvBase64."}

        if not suggested_filename:
            suggested_filename = "examination-report.csv"
        if not suggested_filename.lower().endswith(".csv"):
            suggested_filename += ".csv"

        try:
            csv_bytes = base64.b64decode(csv_b64)
        except Exception as e:
            return {"error": f"Failed to decode csvBase64: {e}"}

        if not webview.windows:
            return {"error": "No active app window available to show a save dialog."}

        window = webview.windows[0]
        try:
            save_path = window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=suggested_filename,
                file_types=("CSV Files (*.csv)",),
            )
        except Exception as e:
            return {"error": f"Failed to open save dialog: {e}"}

        if not save_path:
            return {"error": "Save cancelled."}

        if isinstance(save_path, (list, tuple)):
            save_path = save_path[0] if save_path else ""

        try:
            with open(save_path, "wb") as f:
                f.write(csv_bytes)
        except Exception as e:
            return {"error": f"Failed to write CSV: {e}"}

        return {"ok": True, "path": save_path}

    def grade_full_paper(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        print(f"🕒 [DEBUG] Grading STARTED for student at: {datetime.datetime.now().strftime('%H:%M:%S')}")
        with self._metrics_lock:
            self._runtime_metrics["grade_full_paper_calls"] += 1

        student_name = (payload.get("studentName", "") or "").strip()
        question_docs = payload.get("questionDocs", []) or []
        expert_docs = payload.get("expertDocs", []) or []
        answer_sheet_docs_list = payload.get("answerSheetDocsList", []) or []

        if not self._client and not self.mock_mode:
            return {"error": "Missing OPENAI_API_KEY."}
        if not question_docs:
            return {"error": "Missing question paper document."}
        if len(question_docs) > 1:
            return {"error": "Only 1 question paper is allowed."}
        if len(expert_docs) != 3:
            return {"error": "Expected exactly 3 expert answer documents."}
        if not answer_sheet_docs_list:
            return {"error": "Missing student answer sheet document(s)."}

        # Extract Question Text
        try:
            question_texts: List[str] = []
            for doc in question_docs:
                question_texts.append(
                    self._extract_typed_doc_text(
                        filename=str(doc.get("filename", "")),
                        b64=str(doc.get("base64", ""))
                    )
                )
        except Exception as e:
            return {"error": f"Failed to read question paper: {e}"}

        inferred_q_count = self._infer_question_count_from_texts(question_texts)
        inferred_mpq = self._infer_marks_per_question_from_texts(question_texts, inferred_q_count)
        inferred_total_marks = self._infer_total_marks_from_texts(question_texts)

        # Extract Expert Text
        try:
            expert_texts: List[str] = []
            for doc in expert_docs:
                expert_texts.append(
                    self._extract_typed_doc_text(
                        filename=str(doc.get("filename", "")),
                        b64=str(doc.get("base64", ""))
                    )
                )
        except Exception as e:
            return {"error": f"Failed to read expert answer documents: {e}"}

        # Because of our Parallel Orchestrator, this list only contains 1 student per thread
        answer_sheet_docs = answer_sheet_docs_list[0]
        filename = str(answer_sheet_docs[0].get("filename", "") or "") if answer_sheet_docs else ""
        b64_content = answer_sheet_docs[0].get("base64", "") if answer_sheet_docs else ""
        
        # S3 Upload
        s3_link = None
        if b64_content:
            try:
                file_bytes = base64.b64decode(b64_content)
                s3_link = upload_file_to_s3(file_bytes, filename)
                print(f"✅ S3 Upload successful: {s3_link}")
            except Exception as e:
                print(f"⚠️ S3 Upload failed: {e}")

        # Naming logic
        filename_stem = pathlib.Path(filename).stem if filename else "Student"
        student_specific_name = f"{student_name} - {filename_stem}" if student_name else filename_stem

        try:
            pages = self._extract_answer_sheet_pages(answer_sheet_docs)
        except Exception as e:
            return {"error": f"Failed to process answer sheet for {student_specific_name}: {e}"}

        if not pages:
            return {"error": f"No pages found in answer sheet for {student_specific_name}."}

        prompt_text = self._build_batch_user_prompt(
            student_name=student_specific_name,
            question_papers_text=question_texts,
            expert_texts=expert_texts,
        )

        content: List[Dict[str, Any]] = [{"type": "input_text", "text": prompt_text}]
        for page in pages:
            content.append({"type": "input_text", "text": f"--- PAGE {page['page_number']} ---"})
            if page.get("type") == "text":
                content.append({"type": "input_text", "text": str(page.get("text", "") or "")})
            else:
                content.append({
                    "type": "input_image",
                    "image_url": f"data:{page.get('mime', 'image/png')};base64,{page['base64']}"
                })

        parsed: Dict[str, Any]
        raw_content = ""
        if self.mock_mode:
            text_token_estimate = sum(len(str(page.get("text", ""))) for page in pages if page.get("type") == "text") // 4
            image_token_estimate = 1200 * sum(1 for page in pages if page.get("type") != "text")
            input_tokens = max(400, text_token_estimate + image_token_estimate)
            output_tokens = 450
            total_tokens = input_tokens + output_tokens
            self._record_token_usage(input_tokens, output_tokens, total_tokens)
            print(f"[✓ tokens][mock] input:{input_tokens} output:{output_tokens} total:{total_tokens} | {student_specific_name}")

            parsed = self._build_mock_model_output(student_specific_name)
            raw_content = json.dumps(parsed)
        else:
            try:
                response = self._client.responses.create(
                    model=self.model_name,
                    instructions=BATCH_SYSTEM_PROMPT.strip(),
                    input=[{"role": "user", "content": content}],
                )
            except Exception as e:
                return {"error": f"Error calling OpenAI for {student_specific_name}: {e}"}

            # ✅ Phase 2/3: Log token usage per student for credit monitoring
            try:
                usage = response.usage
                input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                total_tokens = int(getattr(usage, "total_tokens", 0) or (input_tokens + output_tokens))
                self._record_token_usage(input_tokens, output_tokens, total_tokens)
                print(f"[✓ tokens] input:{input_tokens} output:{output_tokens} total:{total_tokens} | {student_specific_name}")
            except Exception:
                pass

            try:
                raw_content = response.output_text or ""
            except Exception:
                raw_content = ""

            if not raw_content:
                try:
                    raw_content = response.output[0].content[0].text
                except Exception:
                    raw_content = str(response)

            parsed = self._parse_model_json(raw_content)
        if not isinstance(parsed, dict) or "results" not in parsed:
            return {
                "error": f"Model output did not match expected JSON shape for {student_specific_name}.",
                "raw_model_response": raw_content,
            }

        normalized_results = self._normalize_results_marking(
            parsed.get("results", []),
            inferred_mpq,
            inferred_total_marks,
        )

        result_data = {
            "student_name": parsed.get("student_name", student_specific_name),
            "copy_number": parsed.get("copy_number") or parsed.get("Copy Number") or "Not Found",
            "enrollment_number": parsed.get("enrollment_number") or parsed.get("Enrollment Number") or "Not Found",
            "program": parsed.get("program") or parsed.get("Program") or "Not Found",
            "subject": parsed.get("subject") or parsed.get("Subject") or "Not Found",
            "branch": parsed.get("branch") or parsed.get("Branch") or "Not Found",
            "batch": parsed.get("batch") or parsed.get("Batch") or "Not Found",
            "subject_code": parsed.get("subject_code") or parsed.get("Subject Code") or "Not Found",
            "academic_session": parsed.get("academic_session") or parsed.get("Academic Session") or "Not Found",
            "student_index": payload.get("student_index", 0),
            "results": normalized_results,
            "raw_model_response": raw_content,
        }

        # Save to DB
        file_hash = self.db.calculate_hash(b64_content)
        if file_hash:
            final_path = s3_link if s3_link else filename
            self.db.save_record(
                file_hash=file_hash,
                student_name=student_specific_name,
                filename=filename,
                s3_url=final_path,
                grade_data=result_data
            )

        print(f"✅ [DEBUG] Grading FINISHED for {student_specific_name} at: {datetime.datetime.now().strftime('%H:%M:%S')}")

        return {
            "success": True,
            "student": result_data
        }
    def check_bulk_duplicates(self, payload):
        """Toll Booth: Only alerts the user if the file exists in the PAST database."""
        answer_sheets = payload.get("answerSheetDocsList", [])
        duplicates = []
        
        # Keep track so we don't show 3 popups for the same file
        seen_in_this_batch = set()

        for sheet_group in answer_sheets:
            if not sheet_group: 
                continue
            
            first_doc = sheet_group[0]
            filename = str(first_doc.get("filename", ""))
            b64_content = str(first_doc.get("base64", ""))
            
            # If we already checked this file in this loop, skip it
            if not b64_content or filename in seen_in_this_batch: 
                continue
                
            file_hash = self.db.calculate_hash(b64_content)
            existing = self.db.is_duplicate(file_hash)
            
            if existing:
                existing_student, existing_date = existing
                duplicates.append({
                    "filename": filename,
                    "existing_student": existing_student,
                    "existing_date": existing_date
                })
            
            seen_in_this_batch.add(filename)
                
        return {
            "duplicates_found": len(duplicates) > 0,
            "duplicates": duplicates
        }
    def grade_bulk_parallel(self, payload):
        """High-speed parallel grading engine with Advanced Auto-Cleaner."""
        print(f"🚀 Starting Thread-Safe Parallel Batch...")
        answer_sheets = payload.get("answerSheetDocsList", [])
        run_started = time.time()
        with self._metrics_lock:
            self._runtime_metrics["grade_bulk_parallel_calls"] += 1
        
        # 🧹 ADVANCED AUTO-CLEANER: Catch duplicate names AND duplicate bytes!
        unique_answer_sheets = []
        seen_names = set()
        seen_hashes = set()
        
        for sheet_group in answer_sheets:
            if not sheet_group: 
                continue
                
            first_doc = sheet_group[0]
            filename = str(first_doc.get("filename", ""))
            b64_content = str(first_doc.get("base64", ""))
            
            # Calculate the unique digital fingerprint of the "matter inside"
            file_hash = self.db.calculate_hash(b64_content)
            
            # If the name is the same OR the exact bytes (matter) are the same
            if filename in seen_names or file_hash in seen_hashes:
                print(f"🗑️ [AUTO-CLEAN] Silently dropping accidental duplicate: {filename}")
                continue # Skip the duplicate completely!
                
            seen_names.add(filename)
            seen_hashes.add(file_hash)
            unique_answer_sheets.append(sheet_group)
            
        # 1. Create a specific payload for every single UNIQUE student
        tasks = []
        for sheet_group in unique_answer_sheets:
            student_payload = payload.copy()
            student_payload["answerSheetDocsList"] = [sheet_group]
            tasks.append(student_payload)
            
        # 2. Run all unique students simultaneously
        results = []
        # 👇 AWS SIR'S SUBMIT & AS_COMPLETED UPDATE 👇
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:  # ✅ Phase 1: was 7, reduced to 3 to control credit burn rate
            # Submit all tasks independently
            futures = {executor.submit(self.grade_full_paper, task): task for task in tasks}
            
            # Gather them safely as they finish
            for future in concurrent.futures.as_completed(futures):
                try:
                    # Safely append the successful result
                    results.append(future.result())
                except Exception as e:
                    # Fault tolerance: Catch crashes without breaking the whole batch
                    print(f"🚨 Thread Error: {e}")
                    results.append({"error": str(e), "success": False})

        elapsed_ms = int((time.time() - run_started) * 1000)
        self._record_bulk_run(
            requested=len(answer_sheets),
            unique=len(unique_answer_sheets),
            results=len(results),
            elapsed_ms=elapsed_ms,
        )

        return results

    def grade_single_student(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Grade a single student answer sheet - used for sequential processing."""
        student_name = (payload.get("studentName", "") or "").strip()
        question_docs = payload.get("questionDocs", []) or []
        expert_docs = payload.get("expertDocs", []) or []
        answer_sheet_docs = payload.get("answerSheetDocs", []) or []
        skip_duplicate = payload.get("skipDuplicate", False)
        overwrite_duplicate = payload.get("overwriteDuplicate", False)

        if not self._client:
            return {
                "error": "Missing OPENAI_API_KEY environment variable. Set it and restart the app."
            }

        if not question_docs:
            return {"error": "Missing question paper document."}
        if len(question_docs) > 1:
            return {"error": "Only 1 question paper is allowed."}
        if len(expert_docs) != 3:
            return {"error": "Expected exactly 3 expert answer documents."}
        if not answer_sheet_docs:
            return {"error": "Missing student answer sheet document."}

        # Check for duplicate if not skipping
        if answer_sheet_docs and len(answer_sheet_docs) > 0:
            first_doc = answer_sheet_docs[0]
            b64_content = str(first_doc.get("base64", "") or "")
            filename = str(first_doc.get("filename", "") or "")
            
            file_hash = self.db.calculate_hash(b64_content)
            existing = self.db.is_duplicate(file_hash)
            
            if existing and not overwrite_duplicate:
                existing_student, existing_date = existing
                return {
                    "duplicate_found": True,
                    "filename": filename,
                    "existing_student": existing_student,
                    "existing_date": existing_date,
                    "message": f"This file already exists in database. Student: {existing_student}, Date: {existing_date}"
                }

        try:
            question_texts: List[str] = []
            for doc in question_docs:
                question_texts.append(
                    self._extract_typed_doc_text(
                        filename=str(doc.get("filename", "")),
                        b64=str(doc.get("base64", "")),
                    )
                )
        except Exception as e:
            return {"error": f"Failed to read question paper: {e}"}

        inferred_q_count = self._infer_question_count_from_texts(question_texts)
        inferred_mpq = self._infer_marks_per_question_from_texts(question_texts, inferred_q_count)
        inferred_total_marks = self._infer_total_marks_from_texts(question_texts)

        try:
            expert_texts: List[str] = []
            for doc in expert_docs:
                expert_texts.append(
                    self._extract_typed_doc_text(
                        filename=str(doc.get("filename", "")),
                        b64=str(doc.get("base64", "")),
                    )
                )
        except Exception as e:
            return {"error": f"Failed to read expert answer documents: {e}"}

        # Extract student info
        filename = ""
        b64_content = ""
        s3_link = None
        
        if answer_sheet_docs and len(answer_sheet_docs) > 0:
            filename = str(answer_sheet_docs[0].get("filename", "") or "")
            b64_content = answer_sheet_docs[0].get("base64", "")
        
        # Upload to S3
        try:
            if b64_content:
                file_bytes = base64.b64decode(b64_content)
                s3_link = upload_file_to_s3(file_bytes, filename)
                print(f"✅ S3 Upload successful: {s3_link}")
        except Exception as e:
            s3_link = None
            print(f"⚠️ S3 Upload failed: {e}")
        
        # Extract base name without extension
        if filename:
            filename_stem = pathlib.Path(filename).stem
        else:
            filename_stem = "Student"
        
        if student_name:
            student_specific_name = f"{student_name} - {filename_stem}"
        else:
            student_specific_name = filename_stem

        try:
            pages = self._extract_answer_sheet_pages(answer_sheet_docs)
        except Exception as e:
            return {"error": f"Failed to process answer sheet for {student_specific_name}: {e}"}

        if not pages:
            return {"error": f"No pages found in answer sheet for {student_specific_name}."}

        prompt_text = self._build_batch_user_prompt(
            student_name=student_specific_name,
            question_papers_text=question_texts,
            expert_texts=expert_texts,
        )

        content: List[Dict[str, Any]] = [
                {"type": "input_text", "text": prompt_text},
            ]
        for page in pages:
                content.append({"type": "input_text", "text": f"--- PAGE {page['page_number']} ---"})
                
                if page.get("type") == "text":
                    content.append({"type": "input_text", "text": str(page.get("text", "") or "")})
                else:
                    content.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{page.get('mime', 'image/png')};base64,{page['base64']}"
                        }
                    )

        try:
            print(f"⏳ {student_specific_name} is waiting at the GPT Bouncer...")
            # 👇 AWS SIR'S SEMAPHORE UPDATE 👇
            with self.gpt_semaphore:
                print(f"🟢 {student_specific_name} entered the API! Calling OpenAI...")
                response = self._client.responses.create(
                    model=self.model_name,
                    instructions=BATCH_SYSTEM_PROMPT.strip(),
                    input=[{"role": "user", "content": content}],
                )
            # 👆 END OF SEMAPHORE UPDATE 👆
        except Exception as e:
            return {"error": f"Error calling OpenAI for {student_specific_name}: {e}"}

        raw_content = ""
        # ✅ Phase 2/3: Log token usage per student for credit monitoring
        try:
            usage = response.usage
            print(f"[✓ tokens] input:{usage.input_tokens} output:{usage.output_tokens} total:{usage.total_tokens} | {student_specific_name}")
        except Exception:
            pass

        try:
            raw_content = response.output_text or ""
        except Exception:
            raw_content = ""

        if not raw_content:
            try:
                raw_content = response.output[0].content[0].text  # type: ignore[attr-defined]
            except Exception:
                raw_content = str(response)

        parsed = self._parse_model_json(raw_content)
        if not isinstance(parsed, dict) or "results" not in parsed:
            return {
                "error": f"Model output did not match expected JSON shape for {student_specific_name}.",
                "raw_model_response": raw_content,
            }

        normalized_results = self._normalize_results_marking(
            parsed.get("results", []),
            inferred_mpq,
            inferred_total_marks,
        )

        if not parsed.get("student_name"):
            parsed["student_name"] = student_specific_name

        # Store result
        result_data = {
                "student_name": parsed.get("student_name", student_specific_name),
                "copy_number": parsed.get("copy_number") or parsed.get("Copy Number") or "Not Found",
                "enrollment_number": parsed.get("enrollment_number") or parsed.get("Enrollment Number") or "Not Found",
                "program": parsed.get("program") or parsed.get("Program") or "Not Found",
                "subject": parsed.get("subject") or parsed.get("Subject") or "Not Found",
                "branch": parsed.get("branch") or parsed.get("Branch") or "Not Found",
                "batch": parsed.get("batch") or parsed.get("Batch") or "Not Found",
                "subject_code": parsed.get("subject_code") or parsed.get("Subject Code") or "Not Found",
                "academic_session": parsed.get("academic_session") or parsed.get("Academic Session") or "Not Found",
                "student_index": payload.get("student_index", 0) if "payload" in locals() else 0,
                "results": normalized_results,
                "raw_model_response": raw_content,
            }

        # Save to database
        file_hash = self.db.calculate_hash(b64_content)
        if file_hash:
            final_path = s3_link if s3_link else filename
            self.db.save_record(
                file_hash=file_hash,
                student_name=student_specific_name,
                filename=filename,
                s3_url=final_path,
                grade_data=result_data
            )

        return {
            "success": True,
            "student": result_data,
        }

    @staticmethod
    def _build_batch_user_prompt(student_name: str, question_papers_text: List[str], expert_texts: List[str]) -> str:
        experts_text = []
        for i, text in enumerate(expert_texts, start=1):
            experts_text.append(f"EXPERT ANSWER DOC {i}:\n{text.strip() or '[EMPTY]'}")

        parts: List[str] = []
        parts.append(
            "STUDENT NAME:\n"
            f"{student_name if student_name else '[NOT PROVIDED - please extract from the first page if present]'}"
        )
        
        # Handle multiple question papers
        if len(question_papers_text) == 1:
            parts.append("QUESTION PAPER (TYPED TEXT):\n" + (question_papers_text[0].strip() or "[EMPTY]"))
        else:
            questions_text = []
            for i, text in enumerate(question_papers_text, start=1):
                questions_text.append(f"QUESTION PAPER {i}:\n{text.strip() or '[EMPTY]'}")
            parts.append("QUESTION PAPERS (TYPED TEXT):\n" + "\n\n".join(questions_text))
        
        parts.append("EXPERT ANSWERS (TYPED TEXT):\n" + "\n\n".join(experts_text))
        parts.append(
            "STUDENT ANSWER SHEET:\n"
            "You will receive a sequence of page inputs labeled PAGE 1, PAGE 2, etc.\n"
            "Some pages may be provided as extracted TEXT, others as page IMAGES (for scanned/handwritten pages).\n"
            "Use those page numbers in page_numbers for each question."
        )
        return "\n\n".join(parts).strip()

    @staticmethod
    def _infer_question_count_from_texts(question_papers_text: List[str]) -> int:
        merged = "\n".join((t or "") for t in question_papers_text)
        if not merged.strip():
            return 0
        numbered = re.findall(r'(?m)^\s*(\d{1,2})[\.)]\s+', merged)
        if not numbered:
            return 0
        nums = sorted({int(n) for n in numbered if n.isdigit()})
        if not nums:
            return 0
        if nums[0] == 1:
            return nums[-1]
        return len(nums)

    @staticmethod
    def _infer_marks_per_question_from_texts(question_papers_text: List[str], question_count: int) -> float:
        merged = " ".join(" ".join((t or "").split()) for t in question_papers_text)
        if not merged:
            return 0.0
        header = merged[:3000]

        # Pattern like 6X10=60 or 5x20=100; second number is marks/question
        mult = re.search(r'(\d{1,2})\s*[x×*]\s*(\d{1,3})(?:\s*=\s*(\d{1,4}))?', header, re.IGNORECASE)
        if mult:
            lhs = float(mult.group(1))
            rhs = float(mult.group(2))
            total = float(mult.group(3)) if mult.group(3) else None
            if total is None or abs(lhs * rhs - total) < 0.001:
                return rhs

        # Explicit instruction: each question carries X marks
        explicit = re.search(
            r'(?:each|every)\s+question(?:\s+\w+){0,5}\s+(?:carries|carry|is|=)\s*(\d+(?:\.\d+)?)\s*marks?',
            header,
            re.IGNORECASE,
        )
        if explicit:
            return float(explicit.group(1))

        # Fallback: total marks / question count
        total_marks = Backend._infer_total_marks_from_texts(question_papers_text)
        if total_marks > 0 and question_count > 0:
            if total_marks > 0:
                return total_marks / float(question_count)
        return 0.0

    @staticmethod
    def _infer_total_marks_from_texts(question_papers_text: List[str]) -> float:
        merged = " ".join(" ".join((t or "").split()) for t in question_papers_text)
        if not merged:
            return 0.0
        header = merged[:3000]
        total_match = re.search(
            r'(?:max(?:imum)?\s*marks?|total\s*marks?)\s*[:=\-]?\s*(\d{1,3}(?:\.\d+)?)',
            header,
            re.IGNORECASE,
        )
        if total_match:
            try:
                return float(total_match.group(1))
            except Exception:
                return 0.0
        return 0.0

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _extract_marks_from_question_text(question_text: Any) -> float:
        text = str(question_text or "")
        if not text:
            return 0.0

        # e.g. "(10 Marks)" or "(7.5 marks)"
        p1 = re.search(r'\((\d+(?:\.\d+)?)\s*marks?\)', text, re.IGNORECASE)
        if p1:
            return float(p1.group(1))

        # e.g. "5 × 1 = 5 marks" or "5x1=5 marks"
        p2 = re.search(r'=\s*(\d+(?:\.\d+)?)\s*marks?', text, re.IGNORECASE)
        if p2:
            return float(p2.group(1))

        # e.g. trailing "7.5 Marks"
        p3 = re.search(r'(\d+(?:\.\d+)?)\s*marks?', text, re.IGNORECASE)
        if p3:
            return float(p3.group(1))

        return 0.0

    @classmethod
    def _normalize_results_marking(
        cls,
        results: Any,
        inferred_marks_per_question: float,
        inferred_total_marks: float = 0.0,
    ) -> List[Dict[str, Any]]:
        if not isinstance(results, list):
            return []

        normalized: List[Dict[str, Any]] = []
        inferred = cls._to_float(inferred_marks_per_question, 0.0)
        total_marks = cls._to_float(inferred_total_marks, 0.0)
        equal_split_fallback = (total_marks / float(len(results))) if total_marks > 0 and len(results) > 0 else 0.0

        for item in results:
            if not isinstance(item, dict):
                continue
            row = dict(item)

            model_max = cls._to_float(row.get("max_marks", 0), 0.0)
            per_question_explicit = cls._extract_marks_from_question_text(row.get("question_text", ""))
            max_marks = (
                per_question_explicit if per_question_explicit > 0 else
                inferred if inferred > 0 else
                equal_split_fallback if equal_split_fallback > 0 else
                model_max if model_max > 0 else
                10.0
            )

            score = cls._to_float(row.get("score", 0), 0.0)
            score = max(0.0, min(score, max_marks))

            row["max_marks"] = max_marks
            row["score"] = score
            normalized.append(row)

        return normalized

    @staticmethod
    def _extract_typed_doc_text(filename: str, b64: str) -> str:
        if not b64:
            raise ValueError("missing base64")

        data = base64.b64decode(b64)
        suffix = pathlib.Path(filename).suffix.lower()

        if suffix == ".pdf":
            return Backend._extract_text_from_pdf_bytes(data)
        if suffix == ".docx":
            return Backend._extract_text_from_docx_bytes(data)
        if suffix in {".txt", ".md"}:
            return data.decode("utf-8", errors="replace")

        raise ValueError(f"Unsupported typed document type: {suffix or '(no extension)'}")

    @staticmethod
    def _extract_text_from_pdf_bytes(data: bytes) -> str:
        doc = fitz.open(stream=data, filetype="pdf")
        parts: List[str] = []
        for page in doc:
            text = page.get_text("text")
            if text and text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)

    @staticmethod
    def _extract_text_from_docx_bytes(data: bytes) -> str:
        doc = Document(io.BytesIO(data))
        parts: List[str] = []
        for p in doc.paragraphs:
            if p.text and p.text.strip():
                parts.append(p.text.strip())
        return "\n".join(parts)

    @staticmethod
    def _extract_answer_sheet_pages(answer_sheet_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        pages: List[Dict[str, Any]] = []
        page_number = 1
        MAX_PAGES = 12  # ✅ Phase 1: safety cap — never send more than 12 pages to API

        for doc in answer_sheet_docs:
            if page_number > MAX_PAGES:
                break  # ✅ Stop processing once we hit the page cap
            filename = str(doc.get("filename", "") or "")
            b64 = str(doc.get("base64", "") or "")
            if not b64:
                continue

            data = base64.b64decode(b64)
            suffix = pathlib.Path(filename).suffix.lower()

            if suffix == ".pdf":
                doc = fitz.open(stream=data, filetype="pdf")
                # ✅ Phase 1: Zoom reduced from 2.0 → 1.4 (saves 30-50% image token cost)
                # Text-first: try extracting text before rendering as image
                mat = fitz.Matrix(1.4, 1.4)
                for p in doc:
                    if page_number > MAX_PAGES:
                        break
                    # ✅ Phase 2 text-first: use text if meaningful, image only for scanned/handwritten
                    page_text = p.get_text("text").strip()
                    compact = "".join(ch for ch in page_text if ch.isalnum())
                    if len(compact) >= 40:
                        # Enough real text — send as text, no image needed (saves tokens)
                        pages.append({
                            "page_number": page_number,
                            "type": "text",
                            "text": page_text,
                        })
                    else:
                        # Low text (scanned/handwritten) — render image at reduced zoom
                        pix = p.get_pixmap(matrix=mat)
                        png_bytes = pix.tobytes("png")
                        pages.append({
                            "page_number": page_number,
                            "type": "image",
                            "mime": "image/png",
                            "base64": base64.b64encode(png_bytes).decode("ascii"),
                        })
                    page_number += 1
                continue

            if suffix == ".docx":
                text = Backend._extract_text_from_docx_bytes(data).strip()
                if text:
                    pages.append({"page_number": page_number, "type": "text", "text": text})
                    page_number += 1
                continue

            mime = "image/png"
            if suffix in {".jpg", ".jpeg"}:
                mime = "image/jpeg"
            elif suffix == ".webp":
                mime = "image/webp"

            pages.append({"page_number": page_number, "type": "image", "mime": mime, "base64": b64})
            page_number += 1

        return pages
    @staticmethod
    def _is_meaningful_text(text: str) -> bool:
        if not text:
            return False
        compact = "".join(ch for ch in text if ch.isalnum())
        return len(compact) >= 40

    @staticmethod
    def _render_pdf_to_png_base64_pages(data: bytes, zoom: float = 1.4) -> List[str]:  # ✅ Phase 1: default zoom 2.0→1.4
        doc = fitz.open(stream=data, filetype="pdf")
        out: List[str] = []
        mat = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            png_bytes = pix.tobytes("png")
            out.append(base64.b64encode(png_bytes).decode("ascii"))
        return out

    @staticmethod
    def _parse_model_json(raw_content: str) -> Dict[str, Any]:
        """
        Try to parse the model output as JSON. If it fails, wrap it as explanation.
        """
        try:
            # Some models might wrap JSON in text – try to extract the first {...} block.
            raw_content_stripped = raw_content.strip()
            # Quick & dirty brace matching:
            if raw_content_stripped.startswith("{") and raw_content_stripped.endswith("}"):
                return json.loads(raw_content_stripped)

            # Fallback: find first '{' and last '}'.
            first = raw_content_stripped.find("{")
            last = raw_content_stripped.rfind("}")
            if first != -1 and last != -1 and last > first:
                json_candidate = raw_content_stripped[first : last + 1]
                return json.loads(json_candidate)

        except Exception:
            pass

        # If all else fails, treat entire response as explanation
        return {
            "score": None,
            "explanation": raw_content,
            "coverage_summary": "",
            "suggestions": "",
        }

    def save_zip_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        zip_b64 = (payload.get("zipBase64", "") or "").strip()
        suggested_filename = (payload.get("suggestedFilename", "") or "").strip()

        if not zip_b64:
            return {"error": "Missing zipBase64."}

        if not suggested_filename:
            suggested_filename = "examination-reports.zip"
        if not suggested_filename.lower().endswith(".zip"):
            suggested_filename += ".zip"

        try:
            zip_bytes = base64.b64decode(zip_b64)
        except Exception as e:
            return {"error": f"Failed to decode zipBase64: {e}"}

        if not webview.windows:
            return {"error": "No active app window available to show a save dialog."}

        window = webview.windows[0]
        try:
            save_path = window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=suggested_filename,
                file_types=("ZIP Files (*.zip)",),
            )
        except Exception as e:
            return {"error": f"Failed to open save dialog: {e}"}

        if not save_path:
            return {"error": "Save cancelled."}

        if isinstance(save_path, (list, tuple)):
            save_path = save_path[0] if save_path else ""

        try:
            with open(save_path, "wb") as f:
                f.write(zip_bytes)
        except Exception as e:
            return {"error": f"Failed to write ZIP file: {e}"}

        return {"ok": True, "path": save_path}
    def get_text_from_textract(self, s3_bucket, s3_filename):
        # 🚀 Everything below MUST be shifted 4 spaces to the right!
        """
        R&D Implementation: Using AWS Textract for High-Speed Handwriting OCR
        """
        client = boto3.client('textract')
        
        # Call Textract (This is a paid service)
        response = client.detect_document_text(
            Document={'S3Object': {'Bucket': s3_bucket, 'Name': s3_filename}}
        )
        
        # Extract only the lines of text
        lines = [item['Text'] for item in response['Blocks'] if item['BlockType'] == 'LINE']
        return " ".join(lines)