// ==========================================
// 1. UI VARIABLES & SETUP
// ==========================================
const stepperTitle = document.getElementById("stepperTitle");
const stepOne = document.getElementById("stepOne");
const stepTwo = document.getElementById("stepTwo");
const stepThree = document.getElementById("stepThree");
const gradeBtn = document.getElementById("gradeBtn");
const gradingStatus = document.getElementById("gradingStatus");
const resultBadge = document.getElementById("resultBadge");
const resultContent = document.getElementById("resultContent");
const statusQuestion = document.getElementById("statusQuestion");
const statusExperts = document.getElementById("statusExperts");
const statusStudents = document.getElementById("statusStudents");
const resultSummaryCard = document.getElementById("resultSummaryCard");
const studentFilesList = document.getElementById("studentFilesList");
const studentFilesName = document.getElementById("studentFilesName");
const studentsRow = document.getElementById("studentsRow");
const batchForm = document.getElementById("batch-form");

const downloadPdfBtn = document.getElementById("download-pdf-btn");
const downloadCsvBtn = document.getElementById("download-csv-btn");

let selectedStudentFiles = [];
let lastReportData = null;
let isBatchSubmitInFlight = false;
let sessionExpiredLock = false;

// ==========================================
// 2. HELPER FUNCTIONS
// ==========================================

function base64ToBytes(base64) {
    const binString = window.atob(base64);
    const bytes = new Uint8Array(binString.length);
    for (let i = 0; i < binString.length; i++) {
        bytes[i] = binString.charCodeAt(i);
    }
    return bytes;
}

function downloadFileFromBrowser(arg1, arg2) {
    // 1. Smart Detection: Find out which argument is the filename (it will have a .pdf, .zip, or .csv extension)
    let filename = (typeof arg1 === "string" && arg1.length < 150 && arg1.includes('.')) ? arg1 : arg2;
    let content = (filename === arg1) ? arg2 : arg1;

    let blob;
    if (content instanceof Blob) {
        blob = content;
    } else {
        // 2. If it's a PDF or ZIP, it is Base64 data from Python. We MUST decode it into real file bytes!
        if (typeof content === "string" && (filename.endsWith('.pdf') || filename.endsWith('.zip'))) {
            content = base64ToBytes(content);
        }

        // 3. Set the correct file type
        let mimeType = "application/octet-stream";
        if (filename.endsWith('.csv')) mimeType = "text/csv;charset=utf-8;";
        else if (filename.endsWith('.pdf')) mimeType = "application/pdf";
        else if (filename.endsWith('.zip')) mimeType = "application/zip";
        
        blob = new Blob([content], { type: mimeType });
    }
    
    // 4. Force the browser to download it
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(link.href), 100);
}
async function fileToBase64Raw(file) {
    if (!file) return "";
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(typeof reader.result === "string" ? reader.result.split(",")[1] : "");
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

function escapeHtml(value) {
    return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function isSessionExpiredError(message) {
    const text = String(message || "").toLowerCase();
    return [
        "invalid_api_key",
        "incorrect api key",
        "missing openai_api_key",
        "insufficient_quota",
        "quota",
        "error code: 401",
        "error code: 429",
        "session expired"
    ].some((token) => text.includes(token));
}

async function showSessionExpiredPopup() {
    await Swal.fire({
        icon: 'error',
        title: 'Session Expired',
        text: 'Your AI session has expired or API access failed. Please update/recheck API key or quota, then try again.',
        confirmButtonText: 'OK'
    });
}

function getFileIdentity(file) {
    if (!file) return "";
    return `${file.name}::${file.size}::${file.lastModified}`;
}

function setStage(stageNumber) {
    [stepOne, stepTwo, stepThree].forEach((el) => el?.classList.remove("active"));
    if (stageNumber === 1) { stepOne?.classList.add("active"); stepperTitle.textContent = "Step 1 of 3: Upload documents"; }
    if (stageNumber === 2) { stepTwo?.classList.add("active"); stepperTitle.textContent = "Step 2 of 3: Processing documents"; }
    if (stageNumber === 3) { stepThree?.classList.add("active"); stepperTitle.textContent = "Step 3 of 3: Grading completed"; }
}

function setStatusChip(element, success) {
    if (element) {
        element.className = `field-status ${success ? "uploaded" : "pending"}`;
        element.textContent = success ? "Uploaded" : "Pending";
    }
}

function syncDemoState() {
    const questionFileInput = document.getElementById("questionFileInput");
    const expertInputs = [document.getElementById("expertFileInput1"), document.getElementById("expertFileInput2"), document.getElementById("expertFileInput3")];

    if (sessionExpiredLock) {
        gradeBtn.disabled = true;
        gradeBtn.classList.add("disabled");
        gradingStatus.textContent = "Session Expired. Update API key/quota and refresh page to retry.";
        return;
    }
    
    const ready = questionFileInput?.files?.length === 1 && expertInputs.every(input => input?.files?.length === 1) && selectedStudentFiles.length > 0;

    if (ready) {
        gradeBtn.disabled = false;
        gradeBtn.classList.remove("disabled");
        gradingStatus.textContent = "All required files uploaded. Click to start grading.";
    } else {
        gradeBtn.disabled = true;
        gradeBtn.classList.add("disabled");
        gradingStatus.textContent = "Waiting for required files...";
    }
}

// ==========================================
// 3. UI EVENT LISTENERS (File Choosers)
// ==========================================
const questionFileInput = document.getElementById("questionFileInput");
const questionFileName = document.getElementById("questionFileName");
const questionRow = document.getElementById("questionRow");

if (questionFileInput) {
    document.querySelector('[data-target-input="questionFileInput"]')?.addEventListener("click", () => questionFileInput.click());
    questionFileInput.addEventListener("change", () => {
        const hasFile = questionFileInput.files?.length === 1;
        questionFileName.textContent = hasFile ? questionFileInput.files[0].name : "No file chosen";
        if(questionRow) {
            questionRow.classList.toggle("has-file", hasFile);
            questionRow.classList.toggle("is-success", hasFile);
        }
        setStatusChip(statusQuestion, hasFile);
        syncDemoState();
    });
}

for (let i = 1; i <= 3; i++) {
    const input = document.getElementById(`expertFileInput${i}`);
    const label = document.getElementById(`expertFileName${i}`);
    const row = document.getElementById(`expertRow${i}`);
    if (input) {
        document.querySelector(`[data-target-input="expertFileInput${i}"]`)?.addEventListener("click", () => input.click());
        input.addEventListener("change", () => {
            const hasFile = input.files?.length === 1;
            label.textContent = hasFile ? input.files[0].name : "No file";
            if(row) {
                row.classList.toggle("has-file", hasFile);
                row.classList.toggle("is-success", hasFile);
            }
            const e1 = document.getElementById("expertFileInput1").files.length;
            const e2 = document.getElementById("expertFileInput2").files.length;
            const e3 = document.getElementById("expertFileInput3").files.length;
            setStatusChip(statusExperts, (e1 && e2 && e3));
            syncDemoState();
        });
    }
}

const studentFilesInput = document.getElementById("studentFilesInput");
if (studentFilesInput) {
    document.querySelector('[data-target-input="studentFilesInput"]')?.addEventListener("click", () => studentFilesInput.click());
    studentFilesInput.addEventListener("change", () => {
        const existingIds = new Set(selectedStudentFiles.map(getFileIdentity));
        for (const file of Array.from(studentFilesInput.files || [])) {
            const fileId = getFileIdentity(file);
            if (!existingIds.has(fileId)) {
                selectedStudentFiles.push(file);
                existingIds.add(fileId);
            }
        }
        const count = selectedStudentFiles.length;
        document.getElementById("studentFilesName").textContent = count > 0 ? `${count} file(s) selected` : "No file chosen";
        
        if(studentsRow) {
            studentsRow.classList.toggle("has-file", count > 0);
            studentsRow.classList.toggle("is-success", count > 0);
        }
        setStatusChip(statusStudents, count > 0);
        
        studentFilesList.innerHTML = selectedStudentFiles.map((file, index) => `
            <div class="selected-file-item" style="display:flex; justify-content:space-between; align-items: center; padding: 12px 16px; border: 1px solid #e2e8f0; border-radius: 8px; margin-top: 8px; background-color: #f8fafc;">
                <span style="font-size: 0.875rem; color: #475569;">${index + 1}. ${escapeHtml(file.name)}</span>
                <button type="button" style="color:#ef4444; border:none; background:none; cursor:pointer; font-weight:bold;" onclick="removeStudentFile(${index})">✕</button>
            </div>
        `).join("");
        
        studentFilesInput.value = ""; 
        syncDemoState();
    });
}

window.removeStudentFile = function(index) {
    selectedStudentFiles.splice(index, 1);
    const count = selectedStudentFiles.length;
    document.getElementById("studentFilesName").textContent = count > 0 ? `${count} file(s) selected` : "No file chosen";
    if(studentsRow) {
        studentsRow.classList.toggle("has-file", count > 0);
        studentsRow.classList.toggle("is-success", count > 0);
    }
    setStatusChip(statusStudents, count > 0);
    
    studentFilesList.innerHTML = selectedStudentFiles.map((file, i) => `
        <div class="selected-file-item" style="display:flex; justify-content:space-between; align-items: center; padding: 12px 16px; border: 1px solid #e2e8f0; border-radius: 8px; margin-top: 8px; background-color: #f8fafc;">
            <span style="font-size: 0.875rem; color: #475569;">${i + 1}. ${escapeHtml(file.name)}</span>
            <button type="button" style="color:#ef4444; border:none; background:none; cursor:pointer; font-weight:bold;" onclick="removeStudentFile(${i})">✕</button>
        </div>
    `).join("");
    syncDemoState();
};

// ==========================================
// 4. THE PYTHON CONNECTION & DUPLICATE CHECK
// ==========================================
if (batchForm) {
    batchForm.addEventListener("submit", async (event) => {
        event.preventDefault();

        if (isBatchSubmitInFlight) {
            return;
        }
        isBatchSubmitInFlight = true;

        setStage(2);
        gradeBtn.disabled = true;
        resultBadge.className = "result-badge processing";
        resultBadge.textContent = "Processing";
        resultSummaryCard.classList.add("hidden");
        gradingStatus.textContent = "Checking for duplicates...";
        
        resultContent.className = "file-list";
        resultContent.innerHTML = `
            AI grading is running. Please wait while reports are generated.
            <div class="progress-wrap">
              <p>50% completed</p>
              <div class="progress-bar"><div class="progress-fill" style="width: 50%;"></div></div>
            </div>
        `;

        try {
            const questionFile = document.getElementById("questionFileInput").files[0];
            const e1 = document.getElementById("expertFileInput1").files[0];
            const e2 = document.getElementById("expertFileInput2").files[0];
            const e3 = document.getElementById("expertFileInput3").files[0];

            const questionDocs = [{ filename: questionFile.name, base64: await fileToBase64Raw(questionFile) }];
            const expertDocs = [
                { filename: e1.name, base64: await fileToBase64Raw(e1) },
                { filename: e2.name, base64: await fileToBase64Raw(e2) },
                { filename: e3.name, base64: await fileToBase64Raw(e3) },
            ];

            const answerSheetDocsList = [];
            for (const f of selectedStudentFiles) {
                answerSheetDocsList.push([{ filename: f.name, base64: await fileToBase64Raw(f) }]);
            }

            const duplicateResponse = await fetch('/api/check-duplicates', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ answerSheetDocsList })
            });
            const duplicateResult = await duplicateResponse.json();
            
            const filesToSkip = new Set();
            const overwriteFiles = new Set();
            
            if (duplicateResult.duplicates_found) {
                gradingStatus.textContent = "Duplicate found! Awaiting your input...";
                for (const dup of duplicateResult.duplicates) {
                    const result = await Swal.fire({
                        title: 'File Already Exists',
                        html: `<p><strong>File:</strong> ${escapeHtml(dup.filename)}</p>
                               <p><strong>Existing Student:</strong> ${escapeHtml(dup.existing_student)}</p>
                               <p><strong>Upload Date:</strong> ${escapeHtml(dup.existing_date)}</p>
                               <p>This file already exists. What would you like to do?</p>`,
                        icon: 'warning',
                        showCancelButton: true,
                        showDenyButton: true,
                        confirmButtonText: 'Overwrite',
                        denyButtonText: 'Skip This File',
                        cancelButtonText: 'Cancel All'
                    });
                    
                    if (result.isDismissed) {
                        gradingStatus.textContent = "Cancelled by user";
                        setStage(1);
                        gradeBtn.disabled = false;
                        return;
                    } else if (result.isDenied) {
                        filesToSkip.add(dup.filename);
                    } else if (result.isConfirmed) {
                        overwriteFiles.add(dup.filename);
                    }
                }
            }

            let filesToGrade = [];
            for (let i = 0; i < answerSheetDocsList.length; i++) {
                if (!filesToSkip.has(answerSheetDocsList[i][0].filename)) {
                    filesToGrade.push(answerSheetDocsList[i]);
                }
            }

            if (filesToGrade.length === 0) {
                gradingStatus.textContent = "No new files to grade. Skipped.";
                setStage(3);
                resultBadge.className = "result-badge idle";
                resultBadge.textContent = "Skipped";
                return;
            }

            gradingStatus.textContent = `🚀 Processing ${filesToGrade.length} files simultaneously...`;

            const response = await fetch('/api/grade', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    studentName: "", rollNumber: "", questionDocs, expertDocs, 
                    answerSheetDocsList: filesToGrade, overwriteDuplicate: true, skipDuplicate: false
                })
            });

            if (!response.ok) throw new Error(`Server returned ${response.status}`);
            const allResults = await response.json();

            let accordionsHTML = '';
            let totalEarned = 0;
            let totalPossible = 0;
            const allStudentsData = []; 
            const successfulResults = [];
            const failedResults = [];

            for (let j = 0; j < allResults.length; j++) {
                const result = allResults[j];
                if (result.success) {
                    successfulResults.push(result);
                    const stu = result.student;
                    allStudentsData.push(stu);
                    
                    let stuScore = 0;
                    let stuMax = 0;
                    let attemptedCount = 0;
                    let skippedCount = 0;
                    let totalQs = stu.results.length;
                    
                    const cards = stu.results.map((r, i) => {
                        const qid = r.question_id || `Q${i+1}`;
                        const score = typeof r.score === "number" ? r.score : 0;
                        const maxMarks = r.max_marks || 10; 
                        stuScore += score;
                        stuMax += maxMarks;

                        // Smart check to see if the question was skipped
                        const exp = (r.explanation || "").toLowerCase();
                        if (r.is_skipped === true || (score === 0 && (exp.includes("not attempt") || exp.includes("blank") || exp.includes("skipped") || exp.includes("not found")))) {
                            skippedCount++;
                        } else {
                            attemptedCount++;
                        }

                        return `
                        <div class="score-pill"><span>${escapeHtml(qid)}</span><strong>${score} / ${maxMarks}</strong></div>
                        <div class="detail-block"><h4>Question</h4><p>${escapeHtml(r.question_text || "")}</p></div>
                        <div class="detail-block"><h4>Pages</h4><p>${escapeHtml(Array.isArray(r.page_numbers) ? r.page_numbers.join(", ") : "")}</p></div>
                        <div class="detail-block"><h4>Explanation</h4><p>${escapeHtml(r.explanation || "")}</p></div>
                        <div class="detail-block"><h4>Coverage vs Expert</h4><p>${escapeHtml(r.coverage_summary || "")}</p></div>
                        <div class="detail-block"><h4>Suggestions</h4><p>${escapeHtml(r.suggestions || "")}</p></div>
                        <hr style="border:0; border-top:1px solid #eee; margin:15px 0;">
                        `;
                    }).join("");

                    totalEarned += stuScore;
                    totalPossible += stuMax;

                    accordionsHTML += `
                        <details class="student-accordion" ${j === 0 ? 'open' : ''}>
                        <summary style="display: flex; align-items: center; justify-content: space-between; padding: 15px; width: 100%; gap: 10px;">
    
                    <!-- 1. STUDENT INFO SECTION (Chevron Removed to fix the Dot) -->
                        <span class="student-meta" style="flex: 1; min-width: 150px;">
                        <small style="display: block; color: #64748b;">S.No: ${j + 1}</small>
                        <strong style="font-size: 1.1rem; display: block; margin: 2px 0;">${escapeHtml(stu.student_name)}</strong>
                        <small style="color: #64748b;">(${escapeHtml(stu.enrollment_number || "N/A")})</small>
                    </span>

                    <!-- 2. QUESTIONS GRADED SECTION -->
                        <span class="question-count" style="flex: 1; text-align: center; font-size: 1.2rem;">
                        <strong>${attemptedCount}</strong>
                        </span>

                    <! --- 3. REPORT DOWNLOAD SECTION -->
                        <div style="flex: 0 0 100px; text-align: right; padding-right: 15px;">
                        <div class="doc" title="Download Individual Report" 
                        style="cursor:pointer; display: inline-block; width: 40px; height: 40px; line-height: 38px; text-align: center; border: 1px solid #cbd5e1; border-radius: 10px; background: #fff; transition: all 0.2s;" 
                        onclick="event.preventDefault(); event.stopPropagation(); downloadStudentReport(${j});">
                        <img src="https://cdn-icons-png.flaticon.com/512/337/337946.png" width="22" height="22" style="vertical-align: middle;" alt="PDF">
                        </div>
                        </div>
                        </summary> <!-- ⚠️ CRITICAL: Ensure this tag is here to close the header section -->
                        <div class="student-body" style="padding: 15px;">${cards}</div>
                        </details>`;
                } else {
                    failedResults.push(result);
                }
            }

            const failedMessages = failedResults.map(r => r?.error || '').join(' | ');
            if (isSessionExpiredError(failedMessages)) {
                throw new Error('Session Expired');
            }

            if (successfulResults.length === 0) {
                const firstErr = failedResults[0]?.error || 'All grading tasks failed on server.';
                throw new Error(firstErr);
            }

            setStage(3);
            gradingStatus.textContent = failedResults.length > 0
                ? `Completed ${successfulResults.length} answer sheet(s), failed ${failedResults.length}.`
                : `Completed ${successfulResults.length} answer sheet(s).`;
            resultBadge.className = failedResults.length > 0 ? "result-badge idle" : "result-badge done";
            resultBadge.textContent = failedResults.length > 0 ? "Partial" : "Completed";
            gradeBtn.disabled = false;

            const failedHtml = failedResults.length > 0
                ? `<div class="report-section" style="margin: 12px 0; border-left: 4px solid #ef4444; padding-left: 12px;">
                        <p><strong>Some files failed:</strong></p>
                        <ul style="margin: 8px 0 0 18px;">
                            ${failedResults.map((r, idx) => `<li>File ${idx + 1}: ${escapeHtml(r.error || 'Unknown grading error')}</li>`).join('')}
                        </ul>
                   </div>`
                : "";
            
            resultContent.className = "";
            resultContent.innerHTML = `
                <div class="result-table-head">
                    <p>Student</p>
                    <p>Questions graded</p>
                    <p>Report</p>
                </div>
                ${failedHtml}
                <section class="students-accordion">${accordionsHTML}</section>
            `;

            const totalQuestionsGraded = successfulResults.reduce((sum, r) => {
                const rows = (r.student && Array.isArray(r.student.results)) ? r.student.results.length : 0;
                return sum + rows;
            }, 0);

            if (totalQuestionsGraded === 0) {
                throw new Error('Session Expired');
            }

            document.getElementById('summaryStudents').textContent = successfulResults.length;
            document.getElementById('summaryAverage').textContent = totalPossible > 0 ? `${((totalEarned/totalPossible)*100).toFixed(1)}%` : "0%";
            document.getElementById('summaryAccuracy').textContent = totalQuestionsGraded;
            resultSummaryCard.classList.remove("hidden");
            
            lastReportData = { students: allStudentsData };
            togglePdfButton(true);
            toggleCsvButton(true);

        } catch (err) {
            console.error(err);
            if (isSessionExpiredError(err?.message)) {
                await showSessionExpiredPopup();
                sessionExpiredLock = true;
                gradingStatus.textContent = "Session Expired";
                resultBadge.className = "result-badge idle";
                resultBadge.textContent = "Failed";
                resultSummaryCard.classList.add("hidden");
                resetReportData();
                resultContent.className = "file-list";
                resultContent.innerHTML = `
                    <div class="report-section" style="margin: 12px 0; border-left: 4px solid #ef4444; padding-left: 12px;">
                        <p><strong>Session Expired</strong></p>
                        <p>Evaluation stopped because API key/session is invalid or quota is exhausted.</p>
                    </div>
                `;
                gradeBtn.disabled = true;
                gradeBtn.classList.add("disabled");
                return;
            }
            gradingStatus.textContent = `Error: ${err.message}`;
            resultBadge.className = "result-badge idle";
            resultBadge.textContent = "Failed";
            gradeBtn.disabled = false;
        } finally {
            isBatchSubmitInFlight = false;
        }
    });
}

function togglePdfButton(enabled) { if (downloadPdfBtn) downloadPdfBtn.disabled = !enabled; }
function toggleCsvButton(enabled) { if (downloadCsvBtn) downloadCsvBtn.disabled = !enabled; }

async function downloadStudentReport(studentIdx) {
    if (!lastReportData || !lastReportData.students || !lastReportData.students[studentIdx]) {
        alert("No report data available for this student.");
        return;
    }

    let student = lastReportData.students[studentIdx];
    
    const findData = (obj, keys) => {
        let result = "Not Found";
        const search = (target) => {
            if (!target || typeof target !== 'object') return;
            for (let k of keys) {
                for (let targetKey in target) {
                    let cleanTargetKey = String(targetKey).toLowerCase().replace(/[^a-z0-9]/g, '');
                    let cleanK = k.toLowerCase().replace(/[^a-z0-9]/g, '');
                    if (cleanTargetKey === cleanK) {
                        let val = target[targetKey];
                        if (val && val !== "Not Found" && val !== "null" && String(val).trim() !== "") {
                            result = val;
                            return;
                        }
                    }
                }
            }
            for (let key in target) {
                if (typeof target[key] === 'object') {
                    search(target[key]);
                    if (result !== "Not Found") return;
                } else if (typeof target[key] === 'string' && target[key].trim().startsWith('{')) {
                    try {
                        search(JSON.parse(target[key]));
                        if (result !== "Not Found") return;
                    } catch(e) {}
                }
            }
        };
        search(student);
        return result;
    };

    let studentResults = [];
    if (student.results && Array.isArray(student.results)) {
        studentResults = student.results;
    } else if (student.grade_data) {
        try {
            let gd = typeof student.grade_data === 'string' ? JSON.parse(student.grade_data) : student.grade_data;
            if (gd.results) studentResults = gd.results;
        } catch(e) {}
    }

    const stuName = findData(student, ["studentname", "name"]) || student.filename || `Student_${studentIdx + 1}`;
    const copyNo = findData(student, ["copynumber", "copy"]);
    const enrollNo = findData(student, ["enrollmentnumber", "enrollment", "enrollno"]);
    const prog = findData(student, ["program", "programme"]);
    const branch = findData(student, ["branch"]);
    const batch = findData(student, ["batch"]);
    const subj = findData(student, ["subject"]);
    const subCode = findData(student, ["subjectcode", "subcode"]);
    const acadSess = findData(student, ["academicsession", "session", "academic"]);

    const studentReport = {
        mode: "Individual Student Report",
        student_name: stuName,
        copy_number: copyNo,
        enrollment_number: enrollNo,
        program: prog,
        branch: branch,
        batch: batch,
        subject: subj,
        subject_code: subCode,
        academic_session: acadSess,
        results: studentResults,
        raw: student.raw_model_response || student.grade_data || "",
        generatedAt: new Date().toLocaleString(),
    };

    try {
        const pdfBlob = createSimplePdf(studentReport);
        const pdfBase64 = await blobToBase64(pdfBlob);
        const suggestedFilename = `examination-report-${stuName.replace(/[^a-zA-Z0-9]/g, '_')}.pdf`;

        downloadFileFromBrowser(pdfBase64, suggestedFilename, "application/pdf");
        
    } catch (err) {
        console.error(err);
        alert(`Failed to generate student PDF: ${err.message || err}`);
    }
}

function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
    });
}

function togglePdfButton(enabled) {
    if (downloadPdfBtn) {
        downloadPdfBtn.disabled = !enabled;
        downloadPdfBtn.textContent = enabled ? "Download All Reports (ZIP)" : "Download All Reports (ZIP)";
    }
}

function toggleCsvButton(enabled) {
    if (downloadCsvBtn) {
        downloadCsvBtn.disabled = !enabled;
    }
}

function resetReportData() {
    lastReportData = null;
    togglePdfButton(false);
    toggleCsvButton(false);
}

function setReportData(data) {
    lastReportData = {
        ...data,
        generatedAt: new Date().toLocaleString(),
    };
    togglePdfButton(true);
    toggleCsvButton(true);
}

function csvEscape(value) {
    if (value === null || value === undefined) return "";
    const str = String(value);
    if (/[",\n\r]/.test(str)) {
        return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
}

function buildCsv(report) {
    if (!report) return "";
    
    const headers = [
        "Date Generated", "Input Mode", "Student Name", 
        "Copy Number", "Enrollment Number", "Program", "Branch", "Batch", 
        "Subject", "Subject Code", "Academic Session", 
        "Total Exam Score", 
        "Question ID", "Question Text", "Pages", "Score", "Max Marks", 
        "Explanation", "Coverage vs Expert", "Suggestions"
    ];
    
    const rows = [];
    
    const csvEscape = (val) => {
        if (val === null || val === undefined) return "";
        const str = String(val);
        if (str.includes(",") || str.includes('"') || str.includes("\n")) {
            return `"${str.replace(/"/g, '""')}"`;
        }
        return str;
    };

    const findData = (obj, keys) => {
        let result = "Not Found";
        const search = (target) => {
            if (!target || typeof target !== 'object') return;
            for (let k of keys) {
                for (let targetKey in target) {
                    let cleanTargetKey = String(targetKey).toLowerCase().replace(/[^a-z0-9]/g, '');
                    let cleanK = k.toLowerCase().replace(/[^a-z0-9]/g, '');
                    if (cleanTargetKey === cleanK) {
                        let val = target[targetKey];
                        if (val && val !== "Not Found" && val !== "null" && String(val).trim() !== "") {
                            result = val;
                            return;
                        }
                    }
                }
            }
            for (let key in target) {
                if (typeof target[key] === 'object') {
                    search(target[key]);
                    if (result !== "Not Found") return;
                } else if (typeof target[key] === 'string' && target[key].trim().startsWith('{')) {
                    try {
                        search(JSON.parse(target[key]));
                        if (result !== "Not Found") return;
                    } catch(e) {}
                }
            }
        };
        search(obj);
        return result;
    };

    const processStudent = (student, mode, date) => {
        let studentResults = [];
        if (student.results && Array.isArray(student.results)) {
            studentResults = student.results;
        } else if (student.grade_data) {
            try {
                let gd = typeof student.grade_data === 'string' ? JSON.parse(student.grade_data) : student.grade_data;
                if (gd.results) studentResults = gd.results;
            } catch(e) {}
        }

        let totalScore = 0;
        studentResults.forEach(r => {
            const s = parseFloat(r.score);
            if (!isNaN(s)) totalScore += s;
        });

        const copyNo = findData(student, ["copynumber", "copy"]);
        const enrollNo = findData(student, ["enrollmentnumber", "enrollment", "enrollno"]);
        const prog = findData(student, ["program", "programme"]);
        const branch = findData(student, ["branch"]);
        const batch = findData(student, ["batch"]);
        const subj = findData(student, ["subject"]);
        const subCode = findData(student, ["subjectcode", "subcode"]);
        const acadSess = findData(student, ["academicsession", "session", "academic"]);
        const stuName = findData(student, ["studentname", "name"]) || student.filename || "";

        if (studentResults.length === 0) {
            const values = [
                date || "", mode || "", stuName, copyNo, enrollNo, prog, branch, batch, subj, subCode, acadSess, totalScore,
                "", "", "", "", "", "", "", ""
            ];
            rows.push(values.map(csvEscape).join(","));
        } else {
            for (const r of studentResults) {
                const pages = Array.isArray(r.page_numbers) ? r.page_numbers.join("|") : "";
                const values = [
                    date || "", mode || "", stuName, copyNo, enrollNo, prog, branch, batch, subj, subCode, acadSess,
                    totalScore, r.question_id || "", r.question_text || "", pages,
                    r.score !== null && r.score !== undefined ? r.score : "",
                    r.max_marks || "", r.explanation || "", r.coverage_summary || "", r.suggestions || "",
                ];
                rows.push(values.map(csvEscape).join(","));
            }
        }
    };

    if (report.students && Array.isArray(report.students)) {
        for (const student of report.students) processStudent(student, report.mode, report.generatedAt);
    } else if (Array.isArray(report.results)) {
        processStudent(report, report.mode, report.generatedAt);
    } else if (Array.isArray(report)) {
        for (const student of report) processStudent(student, "History", new Date().toLocaleString());
    } else {
        processStudent(report, "Single", new Date().toLocaleString());
    }

    return `${headers.join(",")}\n${rows.join("\n")}\n`;
}

if (downloadCsvBtn) {
    downloadCsvBtn.addEventListener("click", async () => {
        if (!lastReportData || !lastReportData.students) {
            alert("Run a grading first to generate the CSV report.");
            return;
        }

        try {
            // 1. Build the CSV rows
            let csvText = "Student Name,Enrollment Number,Total Score,Max Marks,Percentage\n";
            
            lastReportData.students.forEach(stu => {
                let stuScore = 0;
                let stuMax = 0;
                // Use the results array directly
                const results = stu.results || [];
                results.forEach(r => {
                    stuScore += (parseFloat(r.score) || 0);
                    stuMax += (parseFloat(r.max_marks) || 0);
                });
                const percentage = stuMax > 0 ? ((stuScore / stuMax) * 100).toFixed(2) : 0;
                
                // Add the row
                csvText += `"${stu.student_name}","${stu.enrollment_number || 'N/A'}",${stuScore},${stuMax},${percentage}%\n`;
            });

            // 2. Create a proper Blob for the CSV
            const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8;' });
            
            // 3. Create a unique filename
            const filename = `Batch_Results_${Date.now()}.csv`;

            // 4. Manual trigger to ensure the browser saves the NAME correctly
            const link = document.createElement("a");
            const url = URL.createObjectURL(blob);
            link.setAttribute("href", url);
            link.setAttribute("download", filename);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);

        } catch (err) {
            console.error(err);
            alert(`Failed to generate CSV: ${err.message}`);
        }
    });
}

if (downloadPdfBtn) {
    downloadPdfBtn.addEventListener("click", async () => {
        if (!lastReportData || !lastReportData.students) {
            alert("Run a grading first to generate the reports.");
            return;
        }

        try {
            const zip = new JSZip();
            const students = lastReportData.students;
            
            // Define CSV content for the ZIP
            let csvContentForZip = "Student Name,Enrollment Number,Total Score,Max Marks\n";

            for (let i = 0; i < students.length; i++) {
                const student = students[i];
                const studentName = student.student_name || `Student_${i + 1}`;
                const safeFilename = studentName.replace(/[^a-zA-Z0-9_]/g, '_');
                
                // Calculate scores for CSV
                let totalScore = 0;
                let totalMax = 0;
                student.results.forEach(r => {
                    totalScore += (parseFloat(r.score) || 0);
                    totalMax += (parseFloat(r.max_marks) || 0);
                });
                csvContentForZip += `"${studentName}","${student.enrollment_number || 'N/A'}",${totalScore},${totalMax}\n`;

                // Add PDF to ZIP
                const studentReport = {
                    mode: "Student Report",
                    student_name: studentName,
                    enrollment_number: student.enrollment_number,
                    results: student.results || [],
                    raw: student.raw_model_response || "",
                    generatedAt: new Date().toLocaleString(),
                };

                const pdfBlob = createSimplePdf(studentReport);
                const pdfArrayBuffer = await pdfBlob.arrayBuffer();
                zip.file(`${safeFilename}_Report.pdf`, pdfArrayBuffer);
                
                // NOTE: We do NOT add the .json file here anymore.
            }

            // Add the CSV to the ZIP as raw text (No more gibberish)
            zip.file("summary_report.csv", csvContentForZip);

            const zipBlob = await zip.generateAsync({ type: 'blob' });
            
            // Download as raw Blob
            downloadFileFromBrowser(zipBlob, `Examination_Reports_${Date.now()}.zip`);

        } catch (err) {
            console.error(err);
            alert(`Failed to generate ZIP: ${err.message}`);
        }
    });
}

function createSimplePdf(report) {
    const lines = buildReportLines(report);
    const pageHeight = 792; 
    const pageWidth = 612; 
    const margin = 40;
    const lineHeight = 16;
    const maxLinesPerPage = Math.max(1, Math.floor((pageHeight - margin * 2) / lineHeight));
    const chunks = [];

    for (let i = 0; i < lines.length; i += maxLinesPerPage) {
        chunks.push(lines.slice(i, i + maxLinesPerPage));
    }

    if (chunks.length === 0) {
        chunks.push(["No report content available."]);
    }

    const encoder = new TextEncoder();
    const objects = [];

    const setObject = (index, content) => { objects[index] = content; };
    const addObject = (content) => { objects.push(content); return objects.length; };

    setObject(0, "");
    setObject(1, "");

    const fontObjNum = addObject("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
    const pageObjNums = [];

    chunks.forEach((chunkLines) => {
        const textOps = chunkLines.map((line, index) => {
            const y = pageHeight - margin - index * lineHeight;
            return `BT /F1 11 Tf 1 0 0 1 ${margin} ${y.toFixed(2)} Tm (${escapePdfText(line)}) Tj ET`;
        });

        const contentStream = textOps.join("\n");
        const streamLength = encoder.encode(contentStream).length;
        const contentObjNum = addObject(`<< /Length ${streamLength} >>\nstream\n${contentStream}\nendstream`);

        const pageObjNum = addObject(
            [
                "<< /Type /Page",
                " /Parent 2 0 R",
                ` /MediaBox [0 0 ${pageWidth} ${pageHeight}]`,
                ` /Contents ${contentObjNum} 0 R`,
                ` /Resources << /Font << /F1 ${fontObjNum} 0 R >> >>`,
                ">>",
            ].join("")
        );

        pageObjNums.push(pageObjNum);
    });

    if (pageObjNums.length === 0) {
        throw new Error("Unable to build PDF pages.");
    }

    setObject(1, `<< /Type /Pages /Kids [${pageObjNums.map((num) => `${num} 0 R`).join(" ")}] /Count ${pageObjNums.length} >>`);
    setObject(0, "<< /Type /Catalog /Pages 2 0 R >>");

    let pdf = "%PDF-1.4\n";
    const offsets = [];

    objects.forEach((obj, idx) => {
        if (!obj) throw new Error(`Missing PDF object at index ${idx}`);
        offsets[idx] = pdf.length;
        pdf += `${idx + 1} 0 obj\n${obj}\nendobj\n`;
    });

    const xrefPosition = pdf.length;
    pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    offsets.forEach((offset) => {
        pdf += `${String(offset).padStart(10, "0")} 00000 n \n`;
    });
    pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefPosition}\n%%EOF`;

    return new Blob([pdf], { type: "application/pdf" });
}

function buildReportLines(report) {
    const lines = [];
    const wrap = (text, max = 80) => wrapText(text, max);

    lines.push("Examination AI Grading Report");
    lines.push("----------------------------------------");
    lines.push(`Generated: ${report.generatedAt || "N/A"}`);
    lines.push("");

    if (report && Array.isArray(report.results)) {
        lines.push(`Student Name: ${report.student_name || "—"}`);
        lines.push(`Enrollment No: ${report.enrollment_number || "Not Found"}`);
        lines.push(`Program / Branch: ${report.program || "N/A"} / ${report.branch || "N/A"}`);
        lines.push(`Batch / Session: ${report.batch || "N/A"} / ${report.academic_session || "N/A"}`);
        lines.push(`Subject: ${report.subject || "N/A"} (${report.subject_code || "N/A"})`);
        lines.push("----------------------------------------");
        lines.push("");

        report.results.forEach((r) => {
            lines.push(`Question: ${r.question_id || "(unknown)"}`);
            const pages = Array.isArray(r.page_numbers) ? r.page_numbers.join(", ") : "";
            lines.push(`Pages: ${pages || "—"}`);
            const maxMarks = r.max_marks || 10;
            const scoreLabel = typeof r.score === "number" ? `${r.score} / ${maxMarks}` : "Not available";
            lines.push(`Score: ${scoreLabel}`);
            lines.push("");
            lines.push("Explanation:");
            lines.push(...wrap(r.explanation || "—"));
            lines.push("");
            lines.push("Coverage vs Expert Answers:");
            lines.push(...wrap(r.coverage_summary || "—"));
            lines.push("");
            lines.push("Suggestions:");
            lines.push(...wrap(r.suggestions || "—"));
            lines.push("----------------------------------------");
        });
    }

    const rawPreview = report.raw
        ? `${report.raw.slice(0, 1200)}${report.raw.length > 1200 ? " [...]" : ""}`
        : "—";
    lines.push("Raw Model Response (truncated):");
    lines.push(...wrap(rawPreview));

    return lines;
}

function wrapText(text, maxLength) {
    if (!text) return [""];
    const normalized = String(text).replace(/\r\n/g, "\n").split("\n");
    const result = [];

    normalized.forEach((paragraph) => {
        const trimmed = paragraph.trim();
        if (!trimmed) {
            result.push("");
            return;
        }

        let currentLine = "";
        trimmed.split(/\s+/).forEach((word) => {
            const candidate = currentLine ? `${currentLine} ${word}` : word;
            if (candidate.length > maxLength && currentLine) {
                result.push(currentLine);
                currentLine = word;
            } else {
                currentLine = candidate;
            }
        });

        if (currentLine) result.push(currentLine);
    });

    return result.length ? result : [""];
}

function escapePdfText(text) {
    return String(text).replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}