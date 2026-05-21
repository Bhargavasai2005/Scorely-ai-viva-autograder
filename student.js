// --- Anti-Cheat Variables ---
let cheatLog = {
    tabSwitches: 0,
    totalTimeAwaySeconds: 0,
    details: []
};
let awayStartTime = 0;

function enableAntiCheatEngine() {
    // 1. Tab Switch Detection
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            awayStartTime = Date.now();
            cheatLog.tabSwitches++;
            triggerSecurityAlert('Tab switch detected. You left the exam screen.');
        } else {
            if (awayStartTime > 0) {
                let timeAway = Math.round((Date.now() - awayStartTime) / 1000);
                cheatLog.totalTimeAwaySeconds += timeAway;
                cheatLog.details.push(`Left exam for ${timeAway} seconds.`);
                awayStartTime = 0;
            }
        }
    });

    // 2. Screenshot & Keyboard Cheat Detection
    document.addEventListener("keyup", (e) => {
        if (e.key === "PrintScreen") {
            cheatLog.details.push("Attempted to take a screenshot (PrintScreen key).");
            triggerSecurityAlert('Screenshot detected! This action has been logged.');
        }
    });

    document.addEventListener("keydown", (e) => {
        // Detect Ctrl+C, Ctrl+P (Print), or Cmd+Shift+4 (Mac Screenshot)
        if ((e.ctrlKey || e.metaKey) && (e.key === 'c' || e.key === 'p' || e.key === 's')) {
            e.preventDefault(); // Try to block it
            cheatLog.details.push(`Attempted keyboard shortcut: Ctrl+${e.key.toUpperCase()}`);
            triggerSecurityAlert(`Keyboard shortcut disabled during exam.`);
        }
    });
}



// Helper for the popup
function triggerSecurityAlert(message) {
    Swal.fire({ 
        icon: 'error', 
        title: '🚨 SECURITY VIOLATION', 
        text: message, 
        allowOutsideClick: false 
    });
}
// ---- VIVA TIMER ----
let vivaTimerInterval = null;
let vivaTimeRemaining = 0;

function startVivaTimer(minutes) {
    vivaTimeRemaining = minutes * 60;
    updateTimerDisplay();

    const timerEl = document.getElementById('vivaTimer');
    if (timerEl) timerEl.style.display = 'flex';

    clearInterval(vivaTimerInterval);
    vivaTimerInterval = setInterval(async () => {
        vivaTimeRemaining--;
        updateTimerDisplay();

        // Warning at 60 seconds
        if (vivaTimeRemaining === 60) {
            Swal.fire({
                icon: 'warning',
                title: '⏰ 1 Minute Left!',
                text: 'You have only 1 minute remaining. Please submit your current answer quickly.',
                timer: 4000,
                timerProgressBar: true,
                showConfirmButton: false,
                toast: true,
                position: 'top-end'
            });
        }

        // Time's up — auto-submit
        if (vivaTimeRemaining <= 0) {
            clearInterval(vivaTimerInterval);
            document.getElementById('timerDisplay').textContent = '00:00';
            document.getElementById('vivaTimer').classList.add('timer-expired');

            // Submit whatever answer is typed (even blank)
            const answer = document.getElementById('answerText').value.trim() || '[Time expired - no answer provided]';
            document.getElementById('answerText').value = answer;

            await Swal.fire({
                icon: 'error',
                title: '⏰ Time\'s Up!',
                text: 'Your viva time has expired. Your answers are being submitted now.',
                confirmButtonText: 'Submit Now',
                allowOutsideClick: false
            });

            // Submit current answer then finalize viva
            if (currentQuestion && studentData) {
                try {
                    await fetch('/api/submit-answer', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            student_id: studentData.student_id,
                            session_id: studentData.session_id,
                            question: currentQuestion.question,
                            answer: answer
                        })
                    });
                } catch (e) { /* ignore */ }
            }
            await submitViva();
        }
    }, 1000);
}

function stopVivaTimer() {
    clearInterval(vivaTimerInterval);
}

function updateTimerDisplay() {
    const mins = Math.floor(vivaTimeRemaining / 60);
    const secs = vivaTimeRemaining % 60;
    const display = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    const el = document.getElementById('timerDisplay');
    if (el) el.textContent = display;

    // Turn red in last 60 seconds
    const timerEl = document.getElementById('vivaTimer');
    if (timerEl) {
        if (vivaTimeRemaining <= 60) {
            timerEl.classList.add('timer-warning');
        } else {
            timerEl.classList.remove('timer-warning');
        }
    }
}
// ---- END VIVA TIMER ----


let studentData = null;
let currentQuestion = null;
let recognition = null;
let isRecording = false;
let proctoringVerified = false;
let baselineText = "";
let timerInterval = null;
let timeRemaining = 0;

function setVivaInputLock(locked, reason = "") {
    const answerBox = document.getElementById('answerText');
    const submitBtn = document.getElementById('submitAnswer');
    const voiceStatus = document.getElementById('voiceStatus');

    if (answerBox) {
        answerBox.readOnly = locked;
        answerBox.classList.toggle('input-locked', locked);
        if (locked) {
            answerBox.placeholder = 'Camera + microphone permission is required before answering.';
        } else {
            answerBox.placeholder = 'Your voice transcript will appear here.';
        }
    }

    if (submitBtn) {
        submitBtn.disabled = locked;
        submitBtn.title = locked ? 'Enable camera and microphone first' : '';
    }

    if (voiceStatus && reason) {
        voiceStatus.textContent = reason;
        voiceStatus.className = 'voice-status';
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    loadSessions();
    initSpeechRecognition();
});

// Load available sessions
async function loadSessions() {
    try {
        const response = await fetch('/api/sessions');
        const sessions = await response.json();
        
        const select = document.getElementById('session_code');
        select.innerHTML = '<option value="">Select a viva session</option>';
        
        sessions.forEach(session => {
            select.innerHTML += `
                <option value="${session.id}" data-class="${session.class}" data-branch="${session.branch}" data-subject="${session.subject}" data-questions="${session.num_questions}">
                    ${session.name} - ${session.subject} | ${session.class} - ${session.branch} (${session.num_questions} Qs)
                </option>
            `;
        });
    } catch (error) {
        console.error('Error loading sessions:', error);
    }
}

// Student Login
document.getElementById('studentLoginForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    
    const sessionId = document.getElementById('session_code').value;
    const rollNumber = document.getElementById('roll_number').value;
    const studentName = document.getElementById('student_name').value;
    const message = document.getElementById('loginMessage');
    
    // Get session info (class and branch will come from session)
    const sessionSelect = document.getElementById('session_code');
    const sessionText = sessionSelect.options[sessionSelect.selectedIndex].text;
    
    // Check if already taken
    try {
        const checkResponse = await fetch('/api/student-status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ roll_number: rollNumber, session_id: sessionId })
        });
        
        const checkData = await checkResponse.json();
        
        if (checkData.already_taken) {
            existingResult = checkData.result;
            document.getElementById('alreadyTaken').style.display = 'block';
            document.getElementById('loginMessage').style.display = 'none';
            return;
        }
    } catch (error) {
        console.error('Error checking status:', error);
    }
    
    // Start viva
    try {
        const response = await fetch('/api/start-viva', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId,
                roll_number: rollNumber,
                student_name: studentName
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentSession = data;
            studentData = {
                student_id: data.student_id,
                session_id: sessionId,
                roll_number: rollNumber,
                student_name: studentName
            };
            
            // Update UI
            document.getElementById('displayRollNumber').textContent = `${studentName} (${rollNumber})`;
            document.getElementById('studentInfo').style.display = 'flex';
            
            // Show session info if available
              if (data.session_info) {
                  let infoText = ` | ${data.session_info.subject} | ${data.session_info.class} - ${data.session_info.branch}`;
                  document.getElementById('displayRollNumber').textContent += infoText;
              }
            enableAntiCheatEngine();

            proctoringVerified = false;
            setVivaInputLock(true, 'Camera + microphone permission is mandatory. Click "Click to Speak" to continue.');
            
// Show first question
             showSection('viva-section');
             displayQuestion(data.current_question, data.question_number, data.total_questions, data.difficulty);
             
             // Start the countdown timer
             startVivaTimer(data.time_limit || 10);

             // 🎥 Camera proctoring starts when student clicks "Click to Speak"
             // (camera + mic requested together for better UX)

        } else {
            message.textContent = data.error || 'Failed to start viva';
            message.className = 'message error';
        }
    } catch (error) {
        message.textContent = 'Error: ' + error.message;
        message.className = 'message error';
    }
});
// Function to switch between screens
function showSection(sectionId) {
    document.querySelectorAll('.section').forEach(section => {
        section.classList.remove('active');
    });
    document.getElementById(sectionId).classList.add('active');
}

function displayQuestion(question, questionNum, total, difficulty) {
    currentQuestion = question;
    
    document.getElementById('questionCounter').textContent = `Question ${questionNum} of ${total}`;
    document.getElementById('progressFill').style.width = `${(questionNum / total) * 100}%`;
    
    // Hide difficulty badge if you aren't using adaptive levels
    if (difficulty) {
        document.getElementById('currentDifficulty').textContent = `Difficulty: ${difficulty.charAt(0).toUpperCase() + difficulty.slice(1)}`;
    }
    
    document.getElementById('questionText').textContent = question.question;
    document.getElementById('questionHint').textContent = '';
    document.getElementById('answerText').value = '';
    
    // Clear voice status
    document.getElementById('voiceStatus').textContent = 'Click 🎤 to speak your answer';
    document.getElementById('voiceStatus').className = 'voice-status';

    // THE UX FIX: Change button text if it is the very last question!
    const submitBtn = document.getElementById('submitAnswer');
    if (questionNum === total) {
        submitBtn.textContent = 'Submit Viva';
        submitBtn.classList.add('submit-final-pulse'); 
    } else {
        submitBtn.textContent = 'Next Question';
        submitBtn.classList.remove('submit-final-pulse');
    }
    
    submitBtn.disabled = !proctoringVerified;
}

// Submit Answer
document.getElementById('submitAnswer').addEventListener('click', async function() {
    if (!proctoringVerified || !cameraStream) {
        Swal.fire({
            icon: 'error',
            title: 'Camera & Microphone Required',
            text: 'You cannot submit answers until camera and microphone permissions are granted.',
            confirmButtonText: 'OK'
        });
        setVivaInputLock(true, 'Permission missing. Click "Click to Speak" and allow camera + microphone.');
        return;
    }
    
    // THE FIX: Instantly kill the microphone before doing anything else!
    if (isRecording) {
        stopRecording();
    }
    
    const answer = document.getElementById('answerText').value.trim();
    
    if (!answer) {
        Swal.fire({ icon: 'warning', title: 'Oops...', text: 'Please provide an answer before moving on.'});
        return;
    }
    
    if (!currentQuestion || !studentData) {
        alert('Session expired. Please refresh and try again.');
        return;
    }
    
    const originalText = this.textContent; // Remember if it said "Next" or "Submit"
    this.disabled = true;
    this.textContent = 'Evaluating...';
    
    try {
        const response = await fetch('/api/submit-answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                student_id: studentData.student_id,
                session_id: studentData.session_id,
                question: currentQuestion.question,
                answer: answer
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            if (data.is_complete) {
                // All questions answered, submit viva
                await submitViva();
            } else {
                // Go to next question
                await fetchNextQuestion();
            }
        } else {
            alert(data.error || 'Failed to submit answer');
            this.textContent = originalText; // Reset text only if there is an error
        }
    } catch (error) {
        alert('Error: ' + error.message);
        this.disabled = false; // 👈 Only re-enable the button if it FAILED
        this.textContent = originalText; 
    } 
});

// Function to fetch and display next question
async function fetchNextQuestion() {
    // 🚀 THE LOCK: Strictly block if mic is on
    if (typeof isRecording !== 'undefined' && isRecording === true) {
        Swal.fire({
            title: 'Mic is still ON!',
            text: 'You must click STOP to finish your recording before moving to the next question.',
            icon: 'warning',
            confirmButtonColor: '#f59e0b'
        });
        return; // EXIT function - this prevents the code from reaching the API
    }

    try {
        const response = await fetch('/api/next-question', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                student_id: studentData.student_id,
                session_id: studentData.session_id
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // 🚀 THE CLEAR: Ensures the next question starts with a blank box
            const answerBox = document.getElementById('answer-text');
            if (answerBox) answerBox.value = ""; 
            
            // Clear transcription buffers
            if (window.transcriptionBuffer) window.transcriptionBuffer = ""; 
            if (typeof transcribedText !== 'undefined') transcribedText = "";

            showSection('viva-section');
            displayQuestion(data.question, data.question_number, data.total_questions, data.difficulty);
        } else {
            // No more questions left in this session
            await submitViva();
        }
    } catch (error) {
        console.error('Error getting next question:', error);
    }
}
function showFeedback(data) {
    const grading = data.grading;
    
    document.getElementById('feedbackScore').textContent = (grading.score / 10).toFixed(1);
    document.getElementById('feedbackStudentAnswer').textContent = grading.correct_points ? grading.correct_points.join(', ') : 'See details';
    document.getElementById('feedbackIdealAnswer').textContent = grading.missing_points ? 'Points covered: ' + (grading.correct_points || []).join(', ') : '';
    document.getElementById('feedbackText').textContent = grading.feedback;
    document.getElementById('feedbackImprovement').textContent = grading.improvement;
    document.getElementById('feedbackExplanation').textContent = grading.explanation;
    
    const qualityBadge = document.getElementById('qualityBadge');
    qualityBadge.textContent = grading.quality.charAt(0).toUpperCase() + grading.quality.slice(1);
    qualityBadge.className = 'quality-badge ' + grading.quality;
    
    showSection('feedback-section');
}

// Next Question button (in feedback section)
document.getElementById('nextQuestionBtn').addEventListener('click', async function() {
    try {
        const response = await fetch('/api/next-question', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                student_id: studentData.student_id,
                session_id: studentData.session_id
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSection('viva-section');
            displayQuestion(data.question, data.question_number, data.total_questions, data.difficulty);
        } else {
            // No more questions, submit viva
            await submitViva();
        }
    } catch (error) {
        console.error('Error getting next question:', error);
    }
});

async function submitViva() {
    // 🚀 NEW UPDATE: Block submission if Mic is still ON
    // Ensure 'isRecording' matches the variable name you use to track the mic status
    // 🚀 THE LOCK: Prevent submission if mic is active
    if (typeof isRecording !== 'undefined' && isRecording === true) {
        Swal.fire({
            title: 'Microphone Active',
            text: 'Please STOP the microphone to finalize your last answer before submitting.',
            icon: 'error'
        });
        return;
    }

    stopVivaTimer();
    // --- Everything below only runs if the mic is OFF ---
    stopCameraProctoring(); // 🎥 Stop camera proctoring
    
    // 1. Show the "Thanks for Participating" Popup
    Swal.fire({
        title: '🎉 Thanks for Participating!',
        text: 'You have successfully completed all Viva questions.',
        icon: 'success',
        confirmButtonText: 'Get Results',
        confirmButtonColor: '#3b82f6',
        allowOutsideClick: false
    }).then(async (sweetResult) => {
        
        // 2. When they click "Get Results", show the heavy loading overlay
        if (sweetResult.isConfirmed) {
            document.getElementById('evaluating-overlay').style.display = 'flex';
            document.querySelector('.overlay-subtext').textContent = 'Our AI is now evaluating all your answers. This may take 15-20 seconds...';
            
            try {
                const response = await fetch('/api/submit-viva', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        student_id: studentData.student_id,
                        session_id: studentData.session_id,
                        cheat_log: cheatLog 
                    })
                });
                
                const data = await response.json();
                
                document.getElementById('evaluating-overlay').style.display = 'none';
                
                if (data.success) {
                    renderSuperbResults(data.result);
                } else {
                    alert(data.error || 'Failed to submit viva');
                }
            } catch (error) {
                document.getElementById('evaluating-overlay').style.display = 'none';
                alert('Error: ' + error.message);
            }
        }
    });
}

// 3. GENERATE THE BEAUTIFUL UI
function renderSuperbResults(result) {
    // Show score (e.g. 2.0 / 10) and percentage separately below
    document.getElementById('finalScore').textContent = `${result.total_score.toFixed(1)} / ${result.max_possible}`;
    document.getElementById('finalPercentage').textContent = `${result.percentage.toFixed(1)}%`;
    
    const verdict = result.report?.verdict || 'Completed';
    document.getElementById('finalVerdict').textContent = verdict;
    document.getElementById('finalVerdict').className = 'verdict-badge ' + getVerdictClass(verdict);
    
    let html = `<h3 style="text-align:center; margin-bottom: 25px; color: #1e293b;">Detailed Question Analysis</h3>`;
    
    if (result.responses && result.responses.length > 0) {
        result.responses.forEach((resp, index) => {
            let qQuality = resp.quality ? resp.quality.toLowerCase() : 'poor';
            
            // 🚀 NEW: Dynamically display the student's score out of the Admin's custom marks
            let customMax = result.marks_per_q || 10;
            let earnedScore = resp.scaled_score !== undefined ? resp.scaled_score : ((resp.score/100)*customMax);
            
            html += `
            <div class="viva-result-card ${qQuality}">
                <div class="q-header">
                    <h3 class="q-title">Q${index + 1}: ${resp.question}</h3>
                    <span class="q-score-badge">${earnedScore.toFixed(1)}/${customMax}</span>
                </div>
                
                <div class="qa-box">
                    <div class="qa-label">Your Answer:</div>
                    <div class="student-ans">${resp.student_answer || "<em style='color:#ef4444;'>Did not attempt</em>"}</div>
                </div>
                
                <div class="qa-box">
                    <div class="qa-label">Ideal Answer:</div>
                    <div class="ideal-ans">${resp.ideal_answer}</div>
                </div>
                
                <div class="feedback-box">
                    <div class="mistakes-panel">
                        <div class="panel-title">⚠️ Mistakes Made</div>
                        <div>${resp.mistakes_made || "No specific mistakes recorded."}</div>
                    </div>
                    <div class="suggestions-panel">
                        <div class="panel-title">💡 Actionable Suggestion</div>
                        <div>${resp.actionable_suggestion || "Review the ideal answer."}</div>
                    </div>
                </div>
            </div>
            `;
        });
    } else {
        html += `<p class="empty-state">No detailed response data available.</p>`;
    }
    
    document.getElementById('reportContent').innerHTML = html;
    
    // ... [The Chart drawing code below this remains exactly the same!] ...
    
    // Switch to the Result Section
    showSection('result-section');
    // Switch to the Result Section
    showSection('result-section');

    // --- NEW: RENDER HIGH-END PIE CHART ---
    let correctCount = 0;
    let wrongCount = 0;
    let unattemptedCount = 0;

    // Tally up the results
    if (result.responses) {
        result.responses.forEach(r => {
            let qual = r.quality ? r.quality.toLowerCase() : 'poor';
            if (qual === 'unattempted') {
                unattemptedCount++;
            } else if (qual === 'excellent' || qual === 'good') {
                correctCount++;
            } else {
                wrongCount++; // "poor"
            }
        });
    }

    const ctx = document.getElementById('performanceChart').getContext('2d');
    
    // Destroy previous chart if user takes another viva
    if (window.myPieChart) { window.myPieChart.destroy(); }

    window.myPieChart = new Chart(ctx, {
        type: 'doughnut', // Doughnut charts look much more modern than solid pie charts!
        data: {
            labels: ['Correct / Good', 'Needs Improvement', 'Unattempted'],
            datasets: [{
                data: [correctCount, wrongCount, unattemptedCount],
                backgroundColor: [
                    '#10b981', // Emerald Green
                    '#f59e0b', // Amber/Orange
                    '#ef4444'  // Red
                ],
                borderWidth: 0,
                hoverOffset: 10
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom', labels: { font: { size: 14 } } }
            },
            cutout: '65%' // Makes it a sleek ring shape
        }
    });
}
function showResult(result) {
    document.getElementById('finalScore').textContent = `${result.total_score.toFixed(1)} / ${result.max_possible}`;
    document.getElementById('finalPercentage').textContent = `${result.percentage.toFixed(1)}%`;
    
    const verdict = result.report?.verdict || 'Completed';
    document.getElementById('finalVerdict').textContent = verdict;
    document.getElementById('finalVerdict').className = 'verdict-badge ' + getVerdictClass(verdict);
    
    // Build report content
    const report = result.report || {};
    let reportHTML = '';
    
    if (report.executive_summary) {
        reportHTML += `
            <div class="report-section">
                <h3>Executive Summary</h3>
                <p>${report.executive_summary}</p>
            </div>
        `;
    }
    
    if (report.strengths && report.strengths.length > 0) {
        reportHTML += `
            <div class="report-section">
                <h3>Strengths</h3>
                <ul>${report.strengths.map(s => `<li>${s}</li>`).join('')}</ul>
            </div>
        `;
    }
    
    if (report.weaknesses && report.weaknesses.length > 0) {
        reportHTML += `
            <div class="report-section">
                <h3>Areas for Improvement</h3>
                <ul>${report.weaknesses.map(w => `<li>${w}</li>`).join('')}</ul>
            </div>
        `;
    }
    
    if (report.detailed_feedback && report.detailed_feedback.length > 0) {
        reportHTML += `
            <div class="report-section">
                <h3>Question Analysis</h3>
                ${report.detailed_feedback.map((fb, i) => `
                    <div class="question-analysis">
                        <p><span class="question-number">Q${i+1}:</span> ${fb.question}</p>
                        <p><strong>Score:</strong> ${fb.score}/10 (${fb.quality})</p>
                        <p><strong>Feedback:</strong> ${fb.feedback}</p>
                    </div>
                `).join('')}
            </div>
        `;
    }
    
    if (report.future_improvements && report.future_improvements.length > 0) {
        reportHTML += `
            <div class="report-section">
                <h3>Future Improvements</h3>
                <ul>${report.future_improvements.map(imp => `<li>${imp}</li>`).join('')}</ul>
            </div>
        `;
    }
    
    if (report.recommendations) {
        reportHTML += `
            <div class="report-section">
                <h3>Recommendations</h3>
                <p>${report.recommendations}</p>
            </div>
        `;
    }
    
    document.getElementById('reportContent').innerHTML = reportHTML || '<p>No detailed report available.</p>';
    
    showSection('result-section');
}

function getVerdictClass(verdict) {
    if (!verdict) return '';
    verdict = verdict.toLowerCase();
    if (verdict.includes('excellent')) return 'excellent';
    if (verdict.includes('good')) return 'good';
    if (verdict.includes('needs') || verdict.includes('improvement')) return 'needs-improvement';
    return 'poor';
}

function showExistingResult() {
    if (existingResult) {
        renderSuperbResults(existingResult);
    }
}

function printResult() {
    window.print();
}

// Speech Recognition
// ✅ FIX 3: userStoppedRecording flag prevents onend from restarting when user clicks Stop.
// Without this, recognition.onend fires after every short pause and calls stopRecording(),
// resetting the UI and losing the "recording" state even though the user didn't stop.
let userStoppedRecording = false;

function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    
    if (!SpeechRecognition) {
        document.getElementById('startVoice').style.display = 'none';
        document.getElementById('voiceStatus').textContent = 'Voice input not supported. Viva requires camera + microphone on a supported browser.';
        setVivaInputLock(true, 'This browser is unsupported for secured viva. Use Chrome/Edge and allow camera + microphone.');
        console.log('Speech recognition not supported in this browser.');
        return;
    }
    
    function createRecognition() {
        const r = new SpeechRecognition();
        r.continuous = true;
        r.interimResults = true;
        r.lang = 'en-IN'; // Indian English for better technical word accuracy
        r.maxAlternatives = 1;
        
        r.onstart = function() {
            isRecording = true;
            userStoppedRecording = false;
            document.getElementById('startVoice').style.display = 'none';
            document.getElementById('stopVoice').style.display = 'inline-flex';
            document.getElementById('voiceStatus').textContent = '🔴 Listening... Speak your answer';
            document.getElementById('voiceStatus').className = 'voice-status listening';
            
            // Remember what was already in the box before clicking record
            baselineText = document.getElementById('answerText').value;
            if (baselineText.length > 0 && !baselineText.endsWith(' ')) {
                baselineText += ' ';
            }
        };
        
        r.onresult = function(event) {
            let finalTranscript = '';
            let interimTranscript = '';

            for (let i = event.resultIndex; i < event.results.length; ++i) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript;
                } else {
                    interimTranscript += event.results[i][0].transcript;
                }
            }

            if (finalTranscript !== '') {
                baselineText += finalTranscript + ' ';
            }
            document.getElementById('answerText').value = baselineText + interimTranscript;
        };
        
        r.onerror = function(event) {
            // 'no-speech' is normal (silence timeout) — restart silently
            if (event.error === 'no-speech') {
                if (isRecording && !userStoppedRecording) {
                    try { recognition = createRecognition(); recognition.start(); } catch(e) {}
                }
                return;
            }
            // 'aborted' happens when we manually stop — ignore it
            if (event.error === 'aborted') return;
            
            console.error('Speech recognition error:', event.error);
            document.getElementById('voiceStatus').textContent = '⚠️ Mic error: ' + event.error + '. Click 🎤 to retry.';
            document.getElementById('voiceStatus').className = 'voice-status';
            isRecording = false;
            document.getElementById('startVoice').style.display = 'inline-flex';
            document.getElementById('stopVoice').style.display = 'none';
        };
        
        r.onend = function() {
            // ✅ KEY FIX: Only reset UI if the user deliberately stopped.
            // If isRecording is still true, the browser ended on its own (pause/timeout)
            // → restart automatically to keep continuous listening.
            if (isRecording && !userStoppedRecording) {
                try {
                    recognition = createRecognition();
                    recognition.start();
                } catch(e) {
                    console.warn('Auto-restart failed:', e);
                }
            } else {
                // User clicked Stop — reset UI cleanly
                isRecording = false;
                document.getElementById('startVoice').style.display = 'inline-flex';
                document.getElementById('stopVoice').style.display = 'none';
                document.getElementById('voiceStatus').textContent = 'Voice input ready. Click 🎤 to speak again.';
                document.getElementById('voiceStatus').className = 'voice-status';
            }
        };
        
        return r;
    }
    
    recognition = createRecognition();
    
    document.getElementById('startVoice').addEventListener('click', startRecording);
    document.getElementById('stopVoice').addEventListener('click', stopRecording);
}

async function startRecording() {
    if (!recognition) return;
    if (isRecording) return; // Already running

    // ✅ REQUEST CAMERA + MIC TOGETHER IN ONE CALL
    // One getUserMedia({audio:true, video:true}) = ONE popup for BOTH
    if (!cameraStream) {
        try {
            const studentName = document.getElementById('displayStudentName')
                ? document.getElementById('displayStudentName').textContent
                : (sessionStorage.getItem('vivaStudentSession')
                    ? JSON.parse(sessionStorage.getItem('vivaStudentSession')).name
                    : 'Student');

            // Request BOTH audio + video in single call → ONE permission popup
            const combinedStream = await navigator.mediaDevices.getUserMedia({
                audio: true,
                video: { width: { ideal: 320 }, height: { ideal: 240 }, facingMode: 'user' }
            });

            // Split: video tracks go to camera widget
            cameraStream = new MediaStream(combinedStream.getVideoTracks());

            // Set up camera widget with video
            const widget = document.getElementById('camera-widget');
            const dot = document.getElementById('cameraDot');
            const statusOverlay = document.getElementById('cameraStatus');
            const nameEl = document.getElementById('cameraStudentName');
            const videoEl = document.getElementById('cameraFeed');

            if (widget) widget.style.display = 'block';
            if (nameEl) nameEl.textContent = studentName || 'Student';
            if (videoEl) {
                videoEl.srcObject = cameraStream;
                videoEl.onloadedmetadata = () => {
                    videoEl.play();
                    if (statusOverlay) statusOverlay.classList.add('hidden');
                    if (dot) dot.classList.add('live');
                    startFaceMonitoring(videoEl);
                };
            }

            // Stop audio tracks from combinedStream (mic uses SpeechRecognition API separately)
            combinedStream.getAudioTracks().forEach(t => t.stop());

            const videoTracks = cameraStream.getVideoTracks();
            videoTracks.forEach((track) => {
                track.onended = () => {
                    proctoringVerified = false;
                    setVivaInputLock(true, 'Camera feed stopped. Re-enable camera to continue the viva.');
                };
            });

        } catch (err) {
            console.warn('Camera/Mic permission denied:', err);

            // ❌ BLOCK VIVA — Camera is MANDATORY (prevents phone cheating)
            alert('🚫 CAMERA IS REQUIRED TO TAKE THIS VIVA\n\nWithout camera you can use your phone to cheat — this is not allowed.\n\nPlease:\n1. Connect a camera to your device\n2. Click \'Allow\' when browser asks for permission\n3. Click \'Click to Speak\' again');

            // Show denial in camera widget
            const statusOverlay = document.getElementById('cameraStatus');
            const dot = document.getElementById('cameraDot');
            const widget = document.getElementById('camera-widget');
            if (widget) widget.style.display = 'block';
            if (statusOverlay) statusOverlay.innerHTML = '<div class="camera-blocked">📵 Camera access denied.<br>Please allow camera for proctoring.</div>';
            if (dot) dot.classList.add('error');
            cheatLog.details.push('Camera/Mic access denied by student.');
            proctoringVerified = false;
            setVivaInputLock(true, 'Permission denied. Viva is locked until camera + microphone are allowed.');
            return; // ✅ STOP — do not start recording
        }
    }

    proctoringVerified = true;
    setVivaInputLock(false, 'Permission granted. Speak your answer, then submit.');

    userStoppedRecording = false;
    baselineText = document.getElementById('answerText').value;
    if (baselineText.length > 0 && !baselineText.endsWith(' ')) {
        baselineText += ' ';
    }

    try {
        recognition.start();
    } catch(e) {
        console.warn('Could not start recognition:', e);
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition) {
            recognition = new SpeechRecognition();
            initSpeechRecognition();
        }
    }
}

function stopRecording() {
    userStoppedRecording = true; // ✅ Signal onend NOT to auto-restart
    isRecording = false;
    
    if (recognition) {
        try { recognition.stop(); } catch(e) {}
    }
    
    document.getElementById('startVoice').style.display = 'inline-flex';
    document.getElementById('stopVoice').style.display = 'none';
    document.getElementById('voiceStatus').textContent = 'Voice input ready. Click 🎤 to speak again.';
    document.getElementById('voiceStatus').className = 'voice-status';
}

// ==========================================
// CAMERA PROCTORING MODULE
// ==========================================

let cameraStream = null;
let faceCheckInterval = null;
let faceAbsenceCount = 0;
const FACE_ABSENCE_THRESHOLD = 3; // consecutive misses before alert

async function startCameraProctoring(studentName) {
    const widget = document.getElementById('camera-widget');
    const dot = document.getElementById('cameraDot');
    const statusOverlay = document.getElementById('cameraStatus');
    const nameEl = document.getElementById('cameraStudentName');

    if (widget) widget.style.display = 'block';
    if (nameEl) nameEl.textContent = studentName || 'Student';

    try {
        cameraStream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 320 }, height: { ideal: 240 }, facingMode: 'user' },
            audio: false
        });

        const videoEl = document.getElementById('cameraFeed');
        if (videoEl) {
            videoEl.srcObject = cameraStream;
            videoEl.onloadedmetadata = () => {
                videoEl.play();
                if (statusOverlay) statusOverlay.classList.add('hidden');
                if (dot) dot.classList.add('live');
                startFaceMonitoring(videoEl);
            };
        }
        return true; // ✅ Camera granted — viva can continue

    } catch (err) {
        console.warn('Camera access denied or unavailable:', err);
        cameraStream = null; // Make sure it's null so caller knows it failed

        if (statusOverlay) {
            statusOverlay.innerHTML = `<div class="camera-blocked">📵 Camera access denied.<br>Please allow camera for proctoring.</div>`;
        }
        if (dot) dot.classList.add('error');
        cheatLog.details.push('Camera access was denied by student.');

        // ❌ BLOCK VIVA — Camera is MANDATORY for exam security
        // Student could use phone to cheat if no camera
        alert('🚫 CAMERA IS REQUIRED\n\nYou cannot take this viva examination without camera access.\n\nReasons:\n• Prevents cheating using phone\n• Ensures exam integrity\n\nPlease:\n1. Connect a camera to your device\n2. Allow camera permission\n3. Try again');

        // Disable the Click to Speak button so student cannot proceed
        const speakBtn = document.getElementById('startVoice');
        const nextBtn = document.querySelector('.next-btn') || document.getElementById('nextBtn');
        if (speakBtn) {
            speakBtn.disabled = true;
            speakBtn.style.opacity = '0.5';
            speakBtn.style.cursor = 'not-allowed';
            speakBtn.title = 'Camera required to continue';
        }
        if (nextBtn) {
            nextBtn.disabled = true;
            nextBtn.style.opacity = '0.5';
        }

        return false; // ❌ Camera denied — block viva
    }
}

function stopCameraProctoring() {
    if (faceCheckInterval) clearInterval(faceCheckInterval);
    if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
        cameraStream = null;
    }
    const widget = document.getElementById('camera-widget');
    if (widget) widget.style.display = 'none';
}

function startFaceMonitoring(videoEl) {
    // Use a canvas to sample the center region of the video for brightness/motion
    // as a lightweight heuristic when FaceDetection API is unavailable.
    // If the browser supports the FaceDetector API, use that instead.
    if ('FaceDetector' in window) {
        startFaceDetectorAPI(videoEl);
    } else {
        startBrightnessHeuristic(videoEl);
    }
}

// --- Method 1: Modern FaceDetector API (Chrome 70+, Edge) ---
function startFaceDetectorAPI(videoEl) {
    const detector = new FaceDetector({ maxDetectedFaces: 1, fastMode: true });
    const faceAlertEl = document.getElementById('faceAlert');
    const faceStatusEl = document.getElementById('cameraFaceStatus');

    faceCheckInterval = setInterval(async () => {
        if (videoEl.readyState < 2) return;
        try {
            const faces = await detector.detect(videoEl);
            if (faces.length === 0) {
                faceAbsenceCount++;
                if (faceAbsenceCount >= FACE_ABSENCE_THRESHOLD) {
                    if (faceAlertEl) faceAlertEl.style.display = 'block';
                    if (faceStatusEl) {
                        faceStatusEl.textContent = '● Missing';
                        faceStatusEl.className = 'cam-face-status face-missing';
                    }
                    if (faceAbsenceCount === FACE_ABSENCE_THRESHOLD) {
                        // Log once per absence event
                        cheatLog.details.push(`Face not detected at ${new Date().toLocaleTimeString()}.`);
                        triggerSecurityAlert('⚠️ Your face is not visible on camera. Please stay in frame.');
                    }
                }
            } else {
                faceAbsenceCount = 0;
                if (faceAlertEl) faceAlertEl.style.display = 'none';
                if (faceStatusEl) {
                    faceStatusEl.textContent = '● Present';
                    faceStatusEl.className = 'cam-face-status face-ok';
                }
            }
        } catch (e) {
            // FaceDetector can fail on certain frames — silently skip
        }
    }, 2000);
}

// --- Method 2: Brightness heuristic fallback (all other browsers) ---
function startBrightnessHeuristic(videoEl) {
    const canvas = document.createElement('canvas');
    canvas.width = 80; canvas.height = 60;
    const ctx = canvas.getContext('2d');
    const faceAlertEl = document.getElementById('faceAlert');
    const faceStatusEl = document.getElementById('cameraFaceStatus');
    let lastBrightness = null;

    faceCheckInterval = setInterval(() => {
        if (videoEl.readyState < 2) return;
        ctx.drawImage(videoEl, 0, 0, 80, 60);
        const frame = ctx.getImageData(0, 0, 80, 60);
        let total = 0;
        for (let i = 0; i < frame.data.length; i += 4) {
            total += (frame.data[i] + frame.data[i+1] + frame.data[i+2]) / 3;
        }
        const brightness = total / (frame.data.length / 4);

        // If frame is nearly black (< 15 avg brightness), assume no face / covered camera
        if (brightness < 15) {
            faceAbsenceCount++;
            if (faceAbsenceCount >= FACE_ABSENCE_THRESHOLD) {
                if (faceAlertEl) faceAlertEl.style.display = 'block';
                if (faceStatusEl) {
                    faceStatusEl.textContent = '● Missing';
                    faceStatusEl.className = 'cam-face-status face-missing';
                }
                if (faceAbsenceCount === FACE_ABSENCE_THRESHOLD) {
                    cheatLog.details.push(`Camera feed went dark at ${new Date().toLocaleTimeString()}.`);
                }
            }
        } else {
            faceAbsenceCount = 0;
            if (faceAlertEl) faceAlertEl.style.display = 'none';
            if (faceStatusEl) {
                faceStatusEl.textContent = '● Present';
                faceStatusEl.className = 'cam-face-status face-ok';
            }
        }
        lastBrightness = brightness;
    }, 2500);
}

// --- Camera Widget Toggle (minimize/expand) ---
document.addEventListener('DOMContentLoaded', function () {
    const toggleBtn = document.getElementById('cameraToggleBtn');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            const widget = document.getElementById('camera-widget');
            if (widget) widget.classList.toggle('minimized');
            toggleBtn.textContent = widget.classList.contains('minimized') ? '⛶' : '⛶';
        });
    }
    // Hide camera widget until viva starts
    const widget = document.getElementById('camera-widget');
    if (widget) widget.style.display = 'none';
});