// Admin Panel JavaScript

let currentSessionId = null;
let existingResult = null;

function isSessionExpiredError(message) {
    const text = String(message || '').toLowerCase();
    return [
        'invalid_api_key',
        'incorrect api key',
        'missing openai_api_key',
        'insufficient_quota',
        'quota',
        'error code: 401',
        'error code: 429',
        'session expired'
    ].some((token) => text.includes(token));
}

async function showSessionExpiredPopup() {
    if (typeof Swal === 'undefined' || !Swal || typeof Swal.fire !== 'function') {
        alert('Session Expired: Your AI session has expired or API access failed. Please update/recheck API key or quota, then try again.');
        return;
    }
    await Swal.fire({
        icon: 'error',
        title: 'Session Expired',
        text: 'Your AI session has expired or API access failed. Please update/recheck API key or quota, then try again.',
        confirmButtonText: 'OK'
    });
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    loadSessions();
    loadResults();
});

// Navigation
function showSection(sectionId) {
    document.querySelectorAll('.section').forEach(section => {
        section.classList.remove('active');
    });
    const next = document.getElementById(sectionId);
    if (next) next.classList.add('active');

    document.querySelectorAll('[data-admin-section]').forEach(el => {
        const match = el.getAttribute('data-admin-section') === sectionId;
        el.classList.toggle('is-active', match);
        if (el.tagName === 'BUTTON') {
            el.setAttribute('aria-current', match ? 'page' : 'false');
        }
    });

    if (sectionId === 'sessions') {
        loadSessions();
    } else if (sectionId === 'results') {
        loadResults();
    }
}

function openAdminModal(modalId) {
    const m = document.getElementById(modalId);
    if (m) {
        m.hidden = false;
        document.body.classList.add('admin-modal-open');
    }
}

function closeAdminModal(modalId) {
    const m = document.getElementById(modalId);
    if (m) {
        m.hidden = true;
        document.body.classList.remove('admin-modal-open');
    }
}

// Session Management
document.getElementById('sessionForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    
    const form = e.target;
    const submitBtn = form.querySelector('button[type="submit"]');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoading = submitBtn.querySelector('.btn-loading');
    const message = document.getElementById('sessionMessage');
    
    // Show loading state
    btnText.style.display = 'none';
    btnLoading.style.display = 'inline';
    submitBtn.disabled = true;
    message.className = 'message';
    message.textContent = '';
    
    try {
        const formData = new FormData(form);
        
        const response = await fetch('/api/create-session', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            message.textContent = `Session created successfully! ${data.questions_generated} questions generated.`;
            message.className = 'message success';
            form.reset();
            loadSessions();
        } else {
            message.textContent = data.error || 'Failed to create session';
            message.className = 'message error';
            if (isSessionExpiredError(data.error || '')) {
                await showSessionExpiredPopup();
            }
        }
    } catch (error) {
        message.textContent = 'Error: ' + error.message;
        message.className = 'message error';
        if (isSessionExpiredError(error.message)) {
            await showSessionExpiredPopup();
        }
    } finally {
        btnText.style.display = 'inline';
        btnLoading.style.display = 'none';
        submitBtn.disabled = false;
    }
});

async function loadSessions() {
    try {
        const response = await fetch('/api/sessions');
        const sessions = await response.json();
        
        const sessionsList = document.getElementById('sessionsList');
        
        if (sessions.length === 0) {
            sessionsList.innerHTML = '<p class="empty-state">No sessions created yet.</p>';
            return;
        }
        
        sessionsList.innerHTML = sessions.map(session => `
            <div class="admin-session-row">
                <div class="admin-session-row-main">
                    <h3 class="admin-session-title">${session.name}</h3>
                    <p class="admin-session-sub">${session.programme ? session.programme + ' | ' : ''}${session.subject} | ${session.class} - ${session.branch}</p>
                    <p class="admin-session-meta admin-session-meta-time">Exam: ${session.num_questions} questions (bank: ${session.questions.length}) · Time limit: ${session.time_limit || 10} min · Created ${new Date(session.created_at).toLocaleDateString()}</p>
                </div>
                <button type="button" class="admin-session-view" onclick="viewSession('${session.id}')">View</button>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading sessions:', error);
    }
}

function viewSession(sessionId) {
    fetch(`/api/sessions/${sessionId}`)
        .then(res => res.json())
        .then(session => {
            if (session.error) {
                alert(session.error);
                return;
            }
            
            const details = document.getElementById('sessionDetails');
            details.innerHTML = `
                <h4>${session.name}</h4>
                <p><strong>Programme:</strong> ${session.programme || 'N/A'}</p>
                <p><strong>Subject:</strong> ${session.subject}</p>
                <p><strong>Class:</strong> ${session.class}</p>
                <p><strong>Branch:</strong> ${session.branch}</p>
                <p><strong>Questions:</strong> ${session.num_questions}</p>
                <p><strong>Time Limit:</strong> ${session.time_limit || 10} minutes</p>
                <p><strong>Status:</strong> ${session.status}</p>
                <hr>
                <h5>Questions:</h5>
                <div class="questions-list">
                    ${session.questions.map((q, i) => `
                        <div class="question-item">
                            <p><strong>Q${i+1}:</strong> ${q.question}</p>
                            <p><em>Difficulty: ${q.difficulty}</em></p>
                        </div>
                    `).join('')}
                </div>
            `;
            
            openAdminModal('sessionModal');
        });
}

function closeModal() {
    closeAdminModal('sessionModal');
}

// Results Management
async function loadResults() {
    try {
        const response = await fetch('/api/admin/results');
        const payload = await response.json();
        const results = Array.isArray(payload) ? payload : (payload.results || []);

        if (!response.ok) {
            throw new Error(payload.error || 'Failed to load results');
        }

        const resultsList = document.getElementById('resultsList');
        
        if (!results || results.length === 0) {
            resultsList.innerHTML = '<p class="empty-state">No results available yet. Complete a viva session first.</p>';
            return;
        }
        
        resultsList.innerHTML = results.map(result => `
            <div class="admin-result-row">
                <div class="admin-result-row-main">
                    <p class="admin-result-name">${result.student_name || 'N/A'} <span style="font-weight:600;color:#64748b">(${result.roll_number})</span></p>
                    <p class="admin-result-subject">${result.subject || 'N/A'}</p>
                    <p class="admin-result-score">Score: <strong>${result.total_score ? result.total_score.toFixed(1) : 0}</strong></p>
                </div>
                <button type="button" class="admin-result-view" onclick="viewResult('${result.roll_number}')">View Report</button>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading results:', error);
        document.getElementById('resultsList').innerHTML = '<p class="empty-state error">Failed to load results. Check console/server.</p>';
    }
}

function viewResult(studentKey) {
    // studentKey should be the enrollment_number
    fetch(`/api/results/${studentKey}`)
        .then(res => res.json())
        .then(result => {
            if (result.error) {
                alert("Error: " + result.error);
                return;
            }
            
            const details = document.getElementById('resultDetails');
            
            // 1. 👇 NEW: Use the exact percentage calculated by Python! No more guessing!
            const finalPercentage = result.percentage !== undefined ? result.percentage : 0;
            
            // 2. 👇 NEW: Dynamically grab the correct max marks for this specific exam
            const maxMarksPerQ = result.marks_per_q || 10;

            // 3. 👇 NEW: Correctly extract the Proctoring Status from the database rows
            const proctorStatus = (result.responses && result.responses.length > 0 && result.responses[0].proctoring_status) 
                                  ? result.responses[0].proctoring_status 
                                  : 'Clean';
            const hasCheated = proctorStatus !== 'Clean';

            details.innerHTML = `
                <div class="result-header">
                    <h2>${result.student_name || 'Student Report'}</h2>
                    <p><strong>Roll Number:</strong> ${result.roll_number}</p>
                    <div class="final-score">${finalPercentage.toFixed(1)}%</div>
                </div>
                
                <div class="report-content">
                    <div class="report-section" style="border-left: 4px solid ${hasCheated ? '#e74c3c' : '#2ecc71'};">
                        <h3>🚨 Proctoring Status</h3>
                        <p style="font-weight: bold; color: ${hasCheated ? '#e74c3c' : '#2ecc71'};">
                            ${hasCheated ? proctorStatus : 'No security violations detected. ✓'}
                        </p>
                    </div>

                    <div class="report-section">
                        <h3>Detailed Question-by-Question Analysis</h3>
                        ${result.responses.map((resp, i) => `
                            <div class="question-analysis" style="border-bottom: 1px solid #eee; padding-bottom: 15px; margin-bottom: 15px;">
                                <p><span class="question-number" style="font-weight:bold; color:#007bff;">Question ${i + 1}</span></p>
                                <p><strong>Q:</strong> ${resp.question_text}</p>
                                <p><strong>Student Answer:</strong> <span style="color: #444;">${resp.student_answer || "<em>No answer</em>"}</span></p>
                                
                                <p><strong>Score:</strong> ${resp.marks_awarded}/${maxMarksPerQ} (${resp.difficulty_level})</p>
                                
                                <p style="background: #f0f7ff; padding: 10px; border-radius: 5px;">
                                    <strong>💡 AI Suggestion:</strong> ${resp.actionable_suggestion || "Review the technical core concepts for this topic."}
                                </p>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
            
            openAdminModal('resultModal');
        })
        .catch(err => {
            console.error("Fetch Error:", err);
            alert("Could not fetch report. Check if the server is running.");
        });
}

function closeResultModal() {
    closeAdminModal('resultModal');
}

function getVerdictClass(verdict) {
    if (!verdict) return '';
    verdict = verdict.toLowerCase();
    if (verdict.includes('excellent')) return 'excellent';
    if (verdict.includes('good')) return 'good';
    if (verdict.includes('needs') || verdict.includes('improvement')) return 'needs-improvement';
    return 'poor';
}

document.addEventListener('click', function (event) {
    if (event.target.matches('[data-modal-close]')) {
        const modal = event.target.closest('.admin-modal');
        if (modal) {
            modal.hidden = true;
            document.body.classList.remove('admin-modal-open');
        }
    }
});
