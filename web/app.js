const API_URL = "http://127.0.0.1:8000";
const btnSpeak = document.getElementById('btn-speak');
const voiceStatus = document.getElementById('voice-status');
const brainScan = document.getElementById('brain-scan');
const brainBars = document.getElementById('brain-bars');
const vault = document.getElementById('vault');

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition;
let listening = false;
if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.lang = 'hi-IN';
    recognition.interimResults = false;
}

document.getElementById('btn-submit-intake').addEventListener('click', () => {
    submitIntake(document.getElementById('intake-text').value);
});

document.querySelector('.pictogram-menu').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-intake-text]');
    if (button) submitIntake(button.dataset.intakeText);
});

btnSpeak.addEventListener('click', async () => {
    if (!recognition) {
        voiceStatus.textContent = 'Browser speech is not available here. Use Chrome or Edge.';
        return;
    }
    if (listening) return;

    btnSpeak.disabled = true;
    try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('mic-unavailable');
        }
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => track.stop());
        voiceStatus.textContent = 'Listening...';
        listening = true;
        recognition.start();
    } catch (error) {
        listening = false;
        btnSpeak.disabled = false;
        voiceStatus.textContent = microphoneErrorMessage(error.name || error.message);
    }
});

if (recognition) {
    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        document.getElementById('intake-text').value = transcript;
        submitIntake(transcript);
    };
    recognition.onerror = (event) => {
        voiceStatus.textContent = microphoneErrorMessage(event.error);
    };
    recognition.onend = () => {
        listening = false;
        btnSpeak.disabled = false;
    };
}

function microphoneErrorMessage(errorCode) {
    const messages = {
        'not-allowed': 'Microphone access blocked. Allow microphone access in your browser settings.',
        'service-not-allowed': 'Microphone access blocked. Allow microphone access in your browser settings.',
        'NotAllowedError': 'Microphone access blocked. Allow microphone access in your browser settings.',
        'PermissionDeniedError': 'Microphone access blocked. Allow microphone access in your browser settings.',
        'security': 'Microphone access blocked. Open HaqSetu on localhost and allow microphone access.',
        'SecurityError': 'Microphone access blocked. Open HaqSetu on localhost and allow microphone access.',
        'network': 'No internet connection. Use typed input or the pictogram menu.',
        'no-speech': 'Nothing heard. Try again or use typed input.',
        'audio-capture': 'No microphone found. Connect a microphone or use typed input.',
        'NotFoundError': 'No microphone found. Connect a microphone or use typed input.',
        'DevicesNotFoundError': 'No microphone found. Connect a microphone or use typed input.',
        'language-not-supported': 'This speech language is not supported. Use typed input or the pictogram menu.',
        'mic-unavailable': 'Microphone access is not available here. Use typed input or the pictogram menu.'
    };
    return (messages[errorCode] || 'Speech input failed (' + errorCode + '). Use typed input or the pictogram menu.') + ' Error: ' + errorCode;
}

async function submitIntake(text) {
    const trimmedText = text.trim();
    if (!trimmedText) {
        voiceStatus.textContent = 'Enter or choose a concern first.';
        return;
    }
    if (trimmedText.length > 2000) {
        voiceStatus.textContent = 'Your concern is too long. Please keep it under 2,000 characters.';
        return;
    }
    voiceStatus.textContent = 'Sending your concern...';
    try {
        const data = await fetchJson(API_URL + '/triage', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: trimmedText })
        });
        renderBrainScan(data.brain_scan || {});
        if (data.action === 'ABSTAIN') {
            voiceStatus.textContent = 'AI abstained safely. A facilitator will choose the next step.';
        } else {
            const labels = Array.isArray(data.labels) ? data.labels.join(', ') : '';
            voiceStatus.textContent = 'Intent: ' + labels + '. Replying...';
            if (data.spoken_script) speakOutLoud(data.spoken_script);
            vault.style.display = 'block';
        }
    } catch (error) {
        voiceStatus.textContent = 'Could not send your concern. Check that the HaqSetu service is running on port 8000.';
    }
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
}

function renderBrainScan(scores) {
    brainScan.style.display = 'block';
    brainBars.replaceChildren();
    Object.entries(scores).sort((a, b) => b[1] - a[1]).slice(0, 5).forEach(([intent, score]) => {
        const pct = Math.max(0, Math.min(100, Number(score) * 100));
        const row = document.createElement('div');
        row.className = 'bar-container';
        const label = document.createElement('div');
        label.className = 'bar-label';
        const intentLabel = document.createElement('span');
        intentLabel.textContent = intent.replace('_', ' ');
        const percentLabel = document.createElement('span');
        percentLabel.textContent = pct.toFixed(1) + '%';
        label.append(intentLabel, percentLabel);
        const background = document.createElement('div');
        background.className = 'bar-bg';
        const fill = document.createElement('div');
        fill.className = 'bar-fill';
        fill.style.width = pct + '%';
        background.appendChild(fill);
        row.append(label, background);
        brainBars.appendChild(row);
    });
}

document.getElementById('btn-upload').addEventListener('click', async () => {
    const fileInput = document.getElementById('file-input');
    if (!fileInput.files.length) { alert("Select an image first."); return; }
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('case_id', 'LIVE-DEMO-001');
    voiceStatus.textContent = "Hashing pixels and locking into ledger...";
    try {
        const data = await fetchJson(API_URL + "/upload-evidence", { method: 'POST', body: formData });
        voiceStatus.textContent = 'Evidence locked.';
        const hd = document.getElementById('vault-hash');
        hd.style.display = 'block';
        hd.textContent = 'SHA-256: ' + data.sha256;
    } catch (error) {
        voiceStatus.textContent = 'Could not upload evidence. Check that the HaqSetu service is running.';
    }
});

document.getElementById('btn-deny').addEventListener('click', async () => {
    const reason = document.getElementById('denial-reason').value || "No reason provided";
    const resultDiv = document.getElementById('denial-result');
    const proofTrace = document.getElementById('proof-trace');
    const btn = document.getElementById('btn-deny');
    btn.disabled = true;
    btn.textContent = "Processing Override...";
    try {
        const data = await fetchJson(API_URL + "/attempt-denial", {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ case_id: "WIDOW-PENSION-001", reason: reason })
        });
        let traceText = "";
        for (const [key, value] of Object.entries(data.proof_trace)) {
            traceText += key.toUpperCase() + ": " + value + "\n";
        }
        proofTrace.textContent = traceText;
        resultDiv.style.display = 'block';
        speakOutLoud("Denial blocked. Automated rejection is precluded by construction. Case auto-escalated to District Magistrate.");
    } catch (e) { alert("Could not connect to the HaqSetu service."); }
    btn.textContent = "ATTEMPT DENIAL (Sarpanch Override)";
    btn.disabled = false;
});

function ledColor(name) { return { red: '#dc2626', amber: '#f59e0b', green: '#10b981' }[name] || '#dc2626'; }

async function loadBoard() {
    const out = document.getElementById('board-out');
    try {
        const data = await fetchJson(API_URL + '/board');
        if (!data.cases.length) { setMessage(out, 'No open cases. Open a demo case above.'); return; }
        out.replaceChildren();
        data.cases.forEach((item) => {
            const row = document.createElement('div');
            row.className = 'row';
            const led = document.createElement('span');
            led.className = 'led';
            led.style.background = ledColor(item.led);
            led.style.boxShadow = '0 0 8px ' + ledColor(item.led);
            const caseId = document.createElement('b');
            caseId.textContent = item.case_id;
            row.append(led, caseId, document.createTextNode(' · ' + item.route + ' · desk: ' + item.desk));
            row.append(document.createElement('br'), document.createTextNode('Waiting at: '));
            const waitingAt = document.createElement('b');
            waitingAt.textContent = item.waiting_at;
            const status = document.createElement('b');
            status.textContent = item.status.toUpperCase();
            row.append(waitingAt, document.createTextNode(' · Clock: ' + item.hours_elapsed + 'h / ' + item.clock_hours + 'h · Status: '), status);
            if (item.status !== 'closed') {
                const advanceButton = document.createElement('button');
                advanceButton.className = 'btn-small';
                advanceButton.textContent = '⏩ +48h';
                advanceButton.addEventListener('click', () => fastForwardCase(item.case_id));
                row.appendChild(advanceButton);
            }
            out.appendChild(row);
        });
    } catch (e) { setMessage(out, 'API offline.'); }
}

function setMessage(element, message) {
    const paragraph = document.createElement('p');
    paragraph.textContent = message;
    element.replaceChildren(paragraph);
}

async function fastForwardCase(id) {
    try {
        await fetchJson(API_URL + '/fast-forward', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ case_id: id, hours: 48 }) });
        await loadBoard();
    } catch (error) {
        voiceStatus.textContent = 'Could not update the case. Check that the HaqSetu service is running.';
    }
}

async function openDemoCase(route, desk, clockHours) {
    const id = 'CASE-' + Date.now().toString().slice(-4);
    try {
        await fetchJson(API_URL + '/open-case', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ case_id: id, route: route, desk: desk, waiting_at: 'Panchayat', clock_hours: clockHours }) });
        await loadBoard();
    } catch (error) {
        voiceStatus.textContent = 'Could not open a case. Check that the HaqSetu service is running.';
    }
}

document.getElementById('btn-open-case').addEventListener('click', () => openDemoCase('crop_loss', 'Patwari', 72));
document.getElementById('btn-open-case2').addEventListener('click', () => openDemoCase('pension', 'BDO', 48));

document.getElementById('btn-refresh-board').addEventListener('click', loadBoard);

document.getElementById('btn-flags').addEventListener('click', async () => {
    const out = document.getElementById('flags-out');
    setMessage(out, 'Scanning ledger...');
    try {
        const data = await fetchJson(API_URL + '/red-flags');
        if (!data.flags.length) { setMessage(out, 'No corruption patterns detected.'); return; }
        out.replaceChildren();
        const summary = document.createElement('p');
        summary.textContent = 'Desks monitored: ' + data.total_desks_monitored + ' · Alerts: ' + data.corruption_alerts;
        out.appendChild(summary);
        data.flags.forEach((flag) => {
            const row = document.createElement('div');
            row.className = 'row ' + (flag.risk_level === 'CRITICAL' ? 'flag-critical' : 'flag-high');
            const desk = document.createElement('b');
            desk.textContent = flag.desk;
            row.append(desk, document.createTextNode(' — ' + flag.risk_level + ' RISK'));
            row.append(document.createElement('br'), document.createTextNode('Stall rate: ' + flag.stall_rate + '% · Reject rate: ' + flag.reject_rate + '%'));
            flag.reasons.forEach((reason) => {
                row.append(document.createElement('br'), document.createTextNode('• ' + reason));
            });
            row.append(document.createElement('br'), document.createTextNode('Action: ' + flag.action_required));
            out.appendChild(row);
        });
    } catch (e) { setMessage(out, 'API offline.'); }
});

document.getElementById('btn-close-case').addEventListener('click', async () => {
    const id = document.getElementById('close-case-id').value.trim();
    if (!id) { alert('Enter a case ID from the board.'); return; }
    const force = document.getElementById('force-audit').checked;
    const out = document.getElementById('close-out');
    try {
        const data = await fetchJson(API_URL + '/close-case', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ case_id: id, force_audit: force }) });
        const row = document.createElement('div');
        row.className = data.status === 'GHOST_AUDIT_TRIGGERED' ? 'row flag-high' : 'row';
        const status = document.createElement('b');
        status.textContent = data.status;
        row.append(status, document.createElement('br'), document.createTextNode(data.message || ''));
        if (data.status === 'GHOST_AUDIT_TRIGGERED') {
            row.append(document.createElement('br'), document.createTextNode('Verifiers: ' + data.verifiers.join(' + ')));
            speakOutLoud("Closure intercepted. Independent callback audit assigned. Case locked until two verifiers confirm.");
        }
        out.replaceChildren(row);
        await loadBoard();
    } catch (error) {
        setMessage(out, 'Could not close the case. Check that the HaqSetu service is running.');
    }
});

function speakOutLoud(text) {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) return;
    try {
        const u = new SpeechSynthesisUtterance(text);
        u.lang = 'en-IN';
        u.rate = 0.95;
        window.speechSynthesis.speak(u);
    } catch (error) {
        return;
    }
}

loadBoard();
