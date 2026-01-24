/**
 * Main application controller for Moco
 */
import * as api from './js/modules/api.js';
import * as ui from './js/modules/ui.js';
import { AgentMonitor } from './js/modules/monitor.js';
import * as insight from './js/modules/insight.js';
import { escapeHtml } from './js/modules/formatter.js';

let currentSessionId = null;
let isLoading = false;

export function getCurrentSessionId() {
    return currentSessionId;
}
let isComposing = false;
let isStreaming = false;

// === File Attachments Management ===
let attachments = [];

// Supported file types
const SUPPORTED_TYPES = {
    // Documents
    'application/pdf': { icon: '📄', type: 'file' },
    'text/csv': { icon: '📊', type: 'file' },
    'application/json': { icon: '📋', type: 'file' },
    'text/plain': { icon: '📝', type: 'file' },
    'text/markdown': { icon: '📝', type: 'file' },
    // Code
    'text/x-python': { icon: '🐍', type: 'file' },
    'application/x-python': { icon: '🐍', type: 'file' },
    'text/javascript': { icon: '📜', type: 'file' },
    'application/javascript': { icon: '📜', type: 'file' },
    'text/typescript': { icon: '📘', type: 'file' },
    'text/html': { icon: '🌐', type: 'file' },
    'text/css': { icon: '🎨', type: 'file' },
    'text/x-yaml': { icon: '⚙️', type: 'file' },
    'application/x-yaml': { icon: '⚙️', type: 'file' },
    'text/xml': { icon: '📄', type: 'file' },
    'application/xml': { icon: '📄', type: 'file' },
    'text/x-sql': { icon: '🗃️', type: 'file' },
    'text/x-sh': { icon: '💻', type: 'file' },
    // Images
    'image/png': { icon: '🖼️', type: 'image' },
    'image/jpeg': { icon: '🖼️', type: 'image' },
    'image/gif': { icon: '🖼️', type: 'image' },
    'image/webp': { icon: '🖼️', type: 'image' },
};

// File extension to MIME type mapping
const EXT_TO_MIME = {
    'pdf': 'application/pdf',
    'csv': 'text/csv',
    'json': 'application/json',
    'txt': 'text/plain',
    'md': 'text/markdown',
    'py': 'text/x-python',
    'js': 'text/javascript',
    'ts': 'text/typescript',
    'jsx': 'text/javascript',
    'tsx': 'text/typescript',
    'html': 'text/html',
    'css': 'text/css',
    'yaml': 'text/x-yaml',
    'yml': 'text/x-yaml',
    'xml': 'application/xml',
    'sql': 'text/x-sql',
    'sh': 'text/x-sh',
    'bash': 'text/x-sh',
    'go': 'text/plain',
    'rs': 'text/plain',
    'java': 'text/plain',
    'c': 'text/plain',
    'cpp': 'text/plain',
    'h': 'text/plain',
    'hpp': 'text/plain',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'webp': 'image/webp',
};

// WebSocket connection management
let wsConnection = null;
let wsPromise = null;
const ENABLE_APPROVAL_WS = false;

// === Initialization ===
document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

async function initApp() {
    // テーマの初期化
    initTheme();

    const input = document.getElementById('message-input');
    input.addEventListener('compositionstart', () => { isComposing = true; });
    input.addEventListener('compositionend', () => { isComposing = false; });

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !isComposing) {
            e.preventDefault();
            sendMessage();
        }
    });

    input.addEventListener('input', () => {
        autoResize(input);
    });

    // === File Attachment Event Listeners ===
    initFileAttachment();
    initFolderBrowser();

    // New Chatボタンのイベントリスナー (初期化処理の前に登録)
    const newChatBtn = document.getElementById('new-chat-btn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', createNewSession);
    }

    const stopBtn = document.getElementById('stop-btn');
    if (stopBtn) {
        stopBtn.addEventListener('click', onStopClick);
    }

    // 初期化時のエラーで画面が真っ白にならないように保護
    try {
        await loadProfiles();
    } catch (e) {
        console.error('Failed to load profiles:', e);
    }

    try {
        await loadSessions();
    } catch (e) {
        console.error('Failed to load sessions:', e);
    }

    // Event delegation for session items
    document.getElementById('sessions-list').addEventListener('click', (e) => {
        const item = e.target.closest('.session-item');
        const deleteBtn = e.target.closest('.delete-session-btn');
        const renameBtn = e.target.closest('.rename-session-btn');

        if (deleteBtn) {
            e.stopPropagation();
            deleteSession(deleteBtn.dataset.sessionId);
        } else if (renameBtn) {
            e.stopPropagation();
            renameSession(renameBtn.dataset.sessionId);
        } else if (item) {
            loadSession(item.dataset.sessionId);
            if (window.innerWidth <= 768) {
                document.querySelector('.sidebar').classList.remove('open');
            }
        }
    });

    AgentMonitor.init();

    // プロファイル変更時にセッション一覧を更新 & 保存
    document.getElementById('profile-select').addEventListener('change', (e) => {
        localStorage.setItem('mocoProfile', e.target.value);
        loadSessions();
        createNewSession();
    });

    // Provider変更時にモデル選択を表示/非表示 & 保存
    const providerSelect = document.getElementById('provider-select');
    const openrouterModelGroup = document.getElementById('openrouter-model-group');
    const zaiModelGroup = document.getElementById('zai-model-group');
    
    // Restore saved provider
    const savedProvider = localStorage.getItem('mocoProvider');
    if (savedProvider) {
        providerSelect.value = savedProvider;
        // Trigger display update
        if (savedProvider === 'openrouter') {
            openrouterModelGroup.style.display = 'block';
        } else if (savedProvider === 'zai' && zaiModelGroup) {
            zaiModelGroup.style.display = 'block';
        }
    }
    
    providerSelect.addEventListener('change', () => {
        localStorage.setItem('mocoProvider', providerSelect.value);
        openrouterModelGroup.style.display = 'none';
        if (zaiModelGroup) zaiModelGroup.style.display = 'none';
        
        if (providerSelect.value === 'openrouter') {
            openrouterModelGroup.style.display = 'block';
        } else if (providerSelect.value === 'zai' && zaiModelGroup) {
            zaiModelGroup.style.display = 'block';
        }
    });

    // Insight Panel のトグルボタン（二重登録防止）
    const insightToggleBtn = document.getElementById('insight-toggle-btn');
    if (insightToggleBtn && !insightToggleBtn.dataset.insightBound) {
        insightToggleBtn.dataset.insightBound = 'true';
        insightToggleBtn.addEventListener('click', () => {
            insight.toggleInsight();
        });
    }

    // Insight Panel の閉じるボタン
    const insightCloseBtn = document.getElementById('insight-close-btn');
    if (insightCloseBtn) {
        insightCloseBtn.addEventListener('click', () => {
            insight.toggleInsight();
        });
    }

    // Insight Panel の拡大ボタン
    const expandInsightBtn = document.getElementById('expand-insight-btn');
    if (expandInsightBtn) {
        expandInsightBtn.addEventListener('click', () => {
            insight.toggleExpand();
        });
    }

    // Insight Panel のタブ切り替え
    document.querySelectorAll('.insight-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const tabName = tab.dataset.tab;
            
            // タブのアクティブ状態を切り替え
            document.querySelectorAll('.insight-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            // コンテンツの表示を切り替え
            const insightContent = document.getElementById('insight-content');
            const statsContent = document.getElementById('stats-content');
            
            if (tabName === 'insight') {
                insightContent?.classList.remove('hidden');
                statsContent?.classList.add('hidden');
            } else if (tabName === 'stats') {
                insightContent?.classList.add('hidden');
                statsContent?.classList.remove('hidden');
                insight.loadStats();
            }
        });
    });

    // サイドバートグルボタンのイベントリスナー
    const menuBtn = document.getElementById('menu-btn');
    if (menuBtn) {
        menuBtn.addEventListener('click', toggleSidebar);
    }
}

// === WebSocket Management ===

async function ensureWebSocket(sessionId) {
    if (wsPromise) return wsPromise;
    
    if (!ENABLE_APPROVAL_WS) return null;

    wsPromise = api.connectWebSocket(
        sessionId,
        handleWebSocketMessage,
        (error) => {
            console.error('WebSocket error:', error);
            wsConnection = null;
            wsPromise = null;
        },
        () => console.log('WebSocket connected'),
        () => {
            console.log('WebSocket disconnected');
            wsConnection = null;
            wsPromise = null;
        }
    );
    
    wsConnection = await wsPromise;
    return wsConnection;
}

function handleWebSocketMessage(data) {
    if (data.type === 'approval_request') {
        const approvalId = ui.addApprovalMessage(
            currentSessionId,
            data.tool,
            data.args,
            null
        );
        
        const approveBtn = document.getElementById(`${approvalId}-approve`);
        const rejectBtn = document.getElementById(`${approvalId}-reject`);
        
        if (approveBtn) {
            approveBtn.onclick = async () => {
                api.sendApprovalResponse(wsConnection, approvalId, true);
                ui.updateApprovalStatus(approvalId, true);
            };
        }
        
        if (rejectBtn) {
            rejectBtn.onclick = async () => {
                api.sendApprovalResponse(wsConnection, approvalId, false);
                ui.updateApprovalStatus(approvalId, false);
            };
        }
    }
}

// === Actions ===

async function loadProfiles() {
    try {
        const data = await api.fetchProfiles();
        const select = document.getElementById('profile-select');
        select.innerHTML = data.profiles.map(p =>
            `<option value="${p}">${p}</option>`
        ).join('');
        
        // Restore saved profile
        const savedProfile = localStorage.getItem('mocoProfile');
        if (savedProfile && data.profiles.includes(savedProfile)) {
            select.value = savedProfile;
        }
    } catch (e) {
        console.error('Failed to load profiles:', e);
    }
}

async function loadSessions() {
    try {
        const profile = document.getElementById('profile-select').value;
        const data = await api.fetchSessions(profile);
        ui.renderSessions(data.sessions, currentSessionId);

        if (!currentSessionId && data.sessions.length > 0) {
            const lastSessionId = localStorage.getItem('lastSessionId');
            if (lastSessionId && data.sessions.some(s => s.session_id === lastSessionId)) {
                loadSession(lastSessionId);
            }
        }
    } catch (e) {
        console.error('Failed to load sessions:', e);
    }
}

async function loadSession(sessionId) {
    try {
        const data = await api.fetchSessionDetails(sessionId);
        currentSessionId = sessionId;
        localStorage.setItem('lastSessionId', sessionId);

        ui.clearMessages();
        ui.showWelcome(false);
        insight.loadInsightsForSession(sessionId, data.insights);
        insight.loadStats();

        data.messages.forEach(msg => {
            if (msg.role === 'user' || msg.role === 'assistant' || msg.role === 'model') {
                const role = msg.role === 'model' ? 'assistant' : msg.role;
                ui.addMessage(role, msg.content);
            }
        });

        ui.scrollToBottom();
        highlightActiveSession();
        document.getElementById('current-session-title').textContent = data.session?.title || 'Untitled Session';

        if (data.session?.profile) {
            document.getElementById('profile-select').value = data.session.profile;
        }
    } catch (e) {
        console.error('Failed to load session:', e);
        if (e.status === 404) {
            localStorage.removeItem('lastSessionId');
            createNewSession();
        }
    }
}

async function deleteSession(sessionId) {
    if (!confirm('Are you sure you want to delete this session?')) return;

    try {
        await api.deleteSession(sessionId);
        if (localStorage.getItem('lastSessionId') === sessionId) localStorage.removeItem('lastSessionId');
        if (currentSessionId === sessionId) createNewSession();
        loadSessions();
    } catch (e) {
        console.error('Error deleting session:', e);
    }
}

async function renameSession(sessionId) {
    const newTitle = prompt('Enter new session title:');
    if (!newTitle) return;

    try {
        await api.updateSession(sessionId, { title: newTitle });
        if (currentSessionId === sessionId) {
            document.getElementById('current-session-title').textContent = newTitle;
        }
        loadSessions();
    } catch (e) {
        console.error('Error renaming session:', e);
    }
}

function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    sidebar.classList.toggle('open');
}

function createNewSession() {
    currentSessionId = null;
    localStorage.removeItem('lastSessionId');
    ui.clearMessages();
    ui.showWelcome(true);
    insight.clearInsights();
    highlightActiveSession();
    document.getElementById('current-session-title').textContent = 'New Session';
    if (window.innerWidth <= 768) {
        document.querySelector('.sidebar').classList.remove('open');
    }
}

async function onStopClick() {
    if (!isStreaming || !currentSessionId) return;
    try {
        await api.cancelSession(currentSessionId);
        ui.showToast('Cancelling request...', 'info');
    } catch (e) {
        console.error('Failed to request cancel:', e);
        ui.showToast(`Failed to cancel: ${e.message}`, 'error');
    }
}

async function sendMessage() {
    const input = document.getElementById('message-input');
    const message = input.value.trim();

    if (!message && attachments.length === 0) return;
    if (isLoading) return;

    ui.showWelcome(false);
    isStreaming = true;
    
    // Build user message with attachment info
    let displayMessage = message;
    if (attachments.length > 0) {
        const attachmentNames = attachments.map(a => a.name).join(', ');
        displayMessage = message + (message ? '\n' : '') + `📎 Attached: ${attachmentNames}`;
    }
    ui.addMessage('user', displayMessage);
    
    input.value = '';
    autoResize(input);
    setLoading(true);

    const provider = document.getElementById('provider-select').value;
    const workingDirInput = document.getElementById('working-dir-input');
    const workingDir = workingDirInput?.value?.trim() || null;
    const params = {
        message,
        session_id: currentSessionId,
        profile: document.getElementById('profile-select').value,
        provider: provider,
        verbose: document.getElementById('verbose-toggle').checked,
        attachments: getAttachmentsForApi(),
        working_directory: workingDir,
    };
    // OpenRouter/Z.ai選択時はモデルも送信
    if (provider === 'openrouter') {
        params.model = document.getElementById('openrouter-model-select').value;
    } else if (provider === 'zai') {
        const zaiSelect = document.getElementById('zai-model-select');
        if (zaiSelect) params.model = zaiSelect.value;
    }
    
    // Clear attachments after sending
    clearAttachments();

    AgentMonitor.init();

    // WebSocket接続を確立
    try {
        // session_idが決まったら接続（startイベント後）
        // まずSSEストリームを開始してsession_idを取得
    } catch (e) {
        console.warn('WebSocket connection failed, continuing without real-time approval');
    }

    // 経過時間計測
    const startTime = Date.now();
    let elapsedTimerId = null;
    
    // 経過時間表示を開始
    const startElapsedTime = () => {
        const update = () => {
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            const timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
            ui.updateElapsedTime(timeStr);
        };
        elapsedTimerId = setInterval(update, 1000);
        update();  // 初回表示
    };
    startElapsedTime();
    
    try {
        const progressMsgId = ui.addProgressMessage();
        let progressItems = [];
        let assistantMsgId = null;
        let fullResponse = '';
        let thinkingMsgId = null;
        let fullThinking = '';

        for await (const data of api.streamChat(params)) {
            if (data.type === 'start') {
                currentSessionId = data.session_id;
                localStorage.setItem('lastSessionId', currentSessionId);
                // WebSocket接続を確立
                try {
                    await ensureWebSocket(currentSessionId);
                } catch (wsError) {
                    console.warn('WebSocket connection failed:', wsError);
                }
            } else if (data.type === 'thinking') {
                if (!thinkingMsgId) {
                    thinkingMsgId = ui.addThinkingMessage();
                }
                fullThinking += (data.content || '');
                ui.updateThinkingContent(thinkingMsgId, fullThinking);
            } else if (data.type === 'progress') {
                handleProgress(data, progressItems, progressMsgId);
            } else if (data.type === 'chunk') {
                if (!assistantMsgId) {
                    assistantMsgId = ui.addMessage('assistant', '', true);
                }
                fullResponse += data.content;
                ui.updateMessage(assistantMsgId, fullResponse);
            } else if (data.type === 'recall') {
                insight.addRecall(data);
            }
            // approval_request は WebSocket で処理されるため削除
            else if (data.type === 'cancelled') {
                ui.showCancelledSystemMessage();
                ui.showToast('Generation cancelled', 'info');
                isStreaming = false;
                setLoading(false);
                AgentMonitor.hide();
                clearInterval(elapsedTimerId);
                ui.hideElapsedTime();
            }
            else if (data.type === 'done') {
                // 最終経過時間を表示
                clearInterval(elapsedTimerId);
                const totalElapsed = ((Date.now() - startTime) / 1000).toFixed(1);
                ui.showFinalElapsedTime(totalElapsed);
                
                ui.finalizeProgressMessage(progressMsgId, progressItems);
                ui.addCopyButtons(assistantMsgId);
                AgentMonitor.hide();
                isStreaming = false;
                setLoading(false);
            } else if (data.type === 'error') {
                ui.updateMessage(assistantMsgId, `Error: ${data.message}`);
                ui.showToast(`Error: ${data.message}`, 'error');
                AgentMonitor.hide();
                clearInterval(elapsedTimerId);
                ui.hideElapsedTime();
            }
        }
        loadSessions();
    } catch (e) {
        console.error('Failed to send message:', e);
        ui.addMessage('assistant', `Error: ${e.message}`);
        ui.showToast(`Error: ${e.message}`, 'error');
    } finally {
        isStreaming = false;
        setLoading(false);
    }
}

function handleProgress(data, progressItems, progressMsgId) {
    // delegate_to_agent は内部ツールなので非表示
    if (data.tool === 'delegate_to_agent') return;

    // ツール実行ログの追加
    if (data.event === 'tool' && data.tool) {
        if (data.status === 'running') {
            // 開始時のみ新規追加
            const item = `⏳ ${data.name}: ${data.detail || ''}`;
            progressItems.push(item);
            ui.appendProgressItem(progressMsgId, item);
        } else if (data.status === 'completed') {
            // 完了時は既存アイテムを更新（⏳ → ✅）
            const runningItem = `⏳ ${data.name}: ${data.detail || ''}`;
            const completedItem = `✅ ${data.name}: ${data.detail || ''}`;
            const idx = progressItems.indexOf(runningItem);
            if (idx !== -1) {
                progressItems[idx] = completedItem;
                ui.updateProgressItem(progressMsgId, idx, completedItem);
            }
        }
    }

    if (data.event === 'delegate') {
        // エージェント委譲
        AgentMonitor.addAgent(data.agent, data.parent || 'orchestrator');
        AgentMonitor.setActive(data.agent);
    } else if (data.event === 'tool' && data.tool) {
        // ツール使用
        AgentMonitor.addAgent(data.agent, data.parent || 'orchestrator');
        AgentMonitor.setActive(data.agent, data.tool);
    } else if (data.status === 'completed') {
        AgentMonitor.setActive(null);
    }
    // 不要なノード追加を防ぐため、それ以外のイベントは無視
}

function highlightActiveSession() {
    document.querySelectorAll('.session-item').forEach(el => {
        el.classList.toggle('active', el.dataset.sessionId === currentSessionId);
    });
}

function setLoading(loading) {
    isLoading = loading;
    ui.setLoading(loading);
}

// === Global Helpers (exposed to HTML) ===
window.createNewSession = createNewSession;
window.sendMessage = sendMessage;
window.toggleSidebar = toggleSidebar;
window.renameSession = renameSession;
window.onProfileChange = () => {
    loadSessions();
    createNewSession();
};

// === Theme Management ===
function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'midnight';
    document.documentElement.setAttribute('data-theme', savedTheme);
    const themeSelect = document.getElementById('theme-select');
    if (themeSelect) {
        themeSelect.value = savedTheme;
    }
}

window.onThemeChange = () => {
    const themeSelect = document.getElementById('theme-select');
    const theme = themeSelect.value;
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
};

function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
    const btn = document.getElementById('send-btn');
    if (btn) {
        btn.classList.toggle('active', textarea.value.trim().length > 0);
    }
}

window.autoResize = autoResize;
window.handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !isComposing) {
        e.preventDefault();
        sendMessage();
    }
};

// === File Attachment Functions ===

function initFileAttachment() {
    const attachBtn = document.getElementById('attach-btn');
    const fileInput = document.getElementById('file-input');
    const inputContainer = document.getElementById('input-container');
    const dropOverlay = document.getElementById('drop-overlay');
    const messageInput = document.getElementById('message-input');

    // Attach button click
    attachBtn.addEventListener('click', () => {
        fileInput.click();
    });

    // File input change
    fileInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
        fileInput.value = ''; // Reset for re-selection
    });

    // Drag & Drop events
    let dragCounter = 0;

    document.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCounter++;
        if (e.dataTransfer.types.includes('Files')) {
            dropOverlay.classList.add('active');
        }
    });

    document.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dragCounter--;
        if (dragCounter === 0) {
            dropOverlay.classList.remove('active');
        }
    });

    document.addEventListener('dragover', (e) => {
        e.preventDefault();
    });

    document.addEventListener('drop', (e) => {
        e.preventDefault();
        dragCounter = 0;
        dropOverlay.classList.remove('active');
        
        if (e.dataTransfer.files.length > 0) {
            handleFiles(e.dataTransfer.files);
        }
    });

    // Paste event for images
    messageInput.addEventListener('paste', handlePaste);
    document.addEventListener('paste', (e) => {
        // Only handle if not focused on input
        if (document.activeElement !== messageInput) {
            handlePaste(e);
        }
    });
}

/**
 * Handle pasted content (images from clipboard)
 */
function handlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;

    let hasImage = false;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            hasImage = true;
            const file = item.getAsFile();
            if (file) {
                handleFiles([file]);
            }
        }
    }

    // Prevent default only if we handled an image
    if (hasImage) {
        e.preventDefault();
    }
}

/**
 * Handle file selection/drop
 */
const MAX_ATTACHMENTS = 10;

async function handleFiles(files) {
    // 添付ファイル数の上限チェック
    const remainingSlots = MAX_ATTACHMENTS - attachments.length;
    if (remainingSlots <= 0) {
        alert(`Maximum ${MAX_ATTACHMENTS} attachments allowed.`);
        return;
    }

    const filesToProcess = Array.from(files).slice(0, remainingSlots);
    if (filesToProcess.length < files.length) {
        alert(`Only ${remainingSlots} more file(s) can be added. Maximum is ${MAX_ATTACHMENTS}.`);
    }

    for (const file of filesToProcess) {
        try {
            await addAttachment(file);
        } catch (error) {
            console.error('Error adding attachment:', error);
            alert(`Failed to add file: ${file.name}\n${error.message}`);
        }
    }
}

/**
 * Add a file as attachment
 */
async function addAttachment(file) {
    // Check file size (max 10MB)
    const MAX_SIZE = 10 * 1024 * 1024;
    if (file.size > MAX_SIZE) {
        throw new Error('File size exceeds 10MB limit');
    }

    // Determine MIME type
    let mimeType = file.type;
    if (!mimeType || mimeType === 'application/octet-stream') {
        const ext = file.name.split('.').pop()?.toLowerCase();
        mimeType = EXT_TO_MIME[ext] || 'application/octet-stream';
    }

    // Check if supported
    const typeConfig = SUPPORTED_TYPES[mimeType];
    if (!typeConfig && !mimeType.startsWith('text/')) {
        throw new Error(`Unsupported file type: ${mimeType}`);
    }

    // Read file as Base64
    const data = await fileToBase64(file);

    // Create attachment object
    const attachment = {
        id: `att-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
        type: typeConfig?.type || 'file',
        name: file.name,
        mime_type: mimeType,
        size: file.size,
        data: data,
    };

    // For images, create thumbnail
    if (attachment.type === 'image') {
        attachment.thumbnail = data;
    }

    attachments.push(attachment);
    renderAttachments();
}

/**
 * Convert file to Base64
 */
function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            // Remove data URL prefix (data:mime;base64,)
            const base64 = reader.result.split(',')[1];
            resolve(base64);
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

/**
 * Remove attachment
 */
function removeAttachment(id) {
    attachments = attachments.filter(a => a.id !== id);
    renderAttachments();
}

/**
 * Render attachments preview
 */
function renderAttachments() {
    const preview = document.getElementById('attachments-preview');
    
    if (attachments.length === 0) {
        preview.classList.remove('has-items');
        preview.innerHTML = '';
        return;
    }

    preview.classList.add('has-items');
    preview.innerHTML = attachments.map(att => {
        const sizeStr = formatFileSize(att.size);
        const icon = SUPPORTED_TYPES[att.mime_type]?.icon || '📄';

        const escapedName = escapeHtml(att.name);

        if (att.type === 'image') {
            return `
                <div class="attachment-item image-attachment" data-id="${att.id}">
                    <img src="data:${att.mime_type};base64,${att.thumbnail}" alt="${escapedName}" class="attachment-thumbnail">
                    <div class="attachment-info">
                        <span class="attachment-name" title="${escapedName}">${escapedName}</span>
                        <span class="attachment-size">${sizeStr}</span>
                    </div>
                    <button class="attachment-remove" onclick="removeAttachment('${att.id}')" title="Remove">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                </div>
            `;
        }

        return `
            <div class="attachment-item" data-id="${att.id}">
                <div class="attachment-icon">${icon}</div>
                <div class="attachment-info">
                    <span class="attachment-name" title="${escapedName}">${escapedName}</span>
                    <span class="attachment-size">${sizeStr}</span>
                </div>
                <button class="attachment-remove" onclick="removeAttachment('${att.id}')" title="Remove">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                    </svg>
                </button>
            </div>
        `;
    }).join('');
}

/**
 * Format file size
 */
function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * Clear all attachments
 */
function clearAttachments() {
    // メモリリーク防止: Base64データを明示的に解放
    attachments.forEach(a => {
        a.data = null;
        a.thumbnail = null;
    });
    attachments = [];
    renderAttachments();
}

/**
 * Get attachments for API
 */
function getAttachmentsForApi() {
    return attachments.map(att => ({
        type: att.type,
        name: att.name,
        mime_type: att.mime_type,
        data: att.data,
    }));
}

// Expose to global scope
window.removeAttachment = removeAttachment;

// === Copy Code Block Function ===
window.copyCodeBlock = function(btn) {
    const pre = btn.closest('pre');
    const code = pre.querySelector('code');
    const text = code.innerText;
    
    navigator.clipboard.writeText(text).then(() => {
        const span = btn.querySelector('span');
        const originalText = span.innerText;
        span.innerText = 'Copied!';
        btn.classList.add('copied');
        
        setTimeout(() => {
            span.innerText = originalText;
            btn.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy:', err);
    });
};

// === Folder Browser ===
let folderBrowserCurrentPath = null;
let folderBrowserParentPath = null;
let folderBrowserSelectedPath = null;

function initFolderBrowser() {
    const browseBtn = document.getElementById('browse-dir-btn');
    const modal = document.getElementById('folder-modal');
    const closeBtn = document.getElementById('folder-modal-close');
    const cancelBtn = document.getElementById('folder-cancel-btn');
    const selectBtn = document.getElementById('folder-select-btn');
    const upBtn = document.getElementById('folder-up-btn');
    const pathInput = document.getElementById('folder-path-input');
    const list = document.getElementById('folder-list');
    const workingDirInput = document.getElementById('working-dir-input');

    if (!browseBtn || !modal || !list || !workingDirInput) return;

    const savedWorkingDir = localStorage.getItem('mocoWorkingDirectory');
    if (savedWorkingDir) {
        workingDirInput.value = savedWorkingDir;
    }

    const browseDirectories = typeof api.browseDirectories === 'function'
        ? api.browseDirectories
        : async (path = '') => {
            const url = path ? `/api/browse-directories?path=${encodeURIComponent(path)}` : '/api/browse-directories';
            const res = await fetch(url);
            if (!res.ok) throw new Error('Failed to browse directories');
            return await res.json();
        };

    const openModal = async () => {
        modal.classList.add('show');
        await loadDirectories(null);
    };

    const closeModal = () => {
        modal.classList.remove('show');
        folderBrowserSelectedPath = null;
    };

    browseBtn.addEventListener('click', openModal);
    closeBtn?.addEventListener('click', closeModal);
    cancelBtn?.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal.classList.contains('show')) closeModal();
    });

    upBtn?.addEventListener('click', () => {
        if (folderBrowserParentPath) {
            loadDirectories(folderBrowserParentPath);
        } else if (folderBrowserCurrentPath) {
            const parent = folderBrowserCurrentPath.split('/').slice(0, -1).join('/') || '/';
            loadDirectories(parent);
        }
    });

    pathInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const nextPath = pathInput.value.trim();
            if (nextPath) loadDirectories(nextPath);
        }
    });

    selectBtn?.addEventListener('click', () => {
        const selected = folderBrowserSelectedPath || folderBrowserCurrentPath || pathInput?.value?.trim();
        if (selected) {
            workingDirInput.value = selected;
            localStorage.setItem('mocoWorkingDirectory', selected);
            closeModal();
        }
    });

    async function loadDirectories(path) {
        list.innerHTML = '<div class="folder-item">Loading...</div>';

        try {
            const data = await browseDirectories(path || '');
            if (data.error) {
                list.innerHTML = `<div class="folder-item">⚠️ ${escapeHtml(data.error)}</div>`;
                return;
            }

            folderBrowserCurrentPath = data.current || path || '';
            folderBrowserParentPath = data.parent || null;
            if (pathInput) pathInput.value = folderBrowserCurrentPath;

            list.innerHTML = '';
            if (!data.directories || data.directories.length === 0) {
                list.innerHTML = '<div class="folder-item">No subfolders</div>';
                return;
            }

            data.directories.forEach((dir) => {
                const item = document.createElement('div');
                item.className = 'folder-item';
                item.dataset.path = dir.path;

                const icon = document.createElement('span');
                icon.className = 'icon';
                icon.textContent = dir.icon || '📁';

                const name = document.createElement('span');
                name.className = 'name';
                name.textContent = dir.name || dir.path;

                item.appendChild(icon);
                item.appendChild(name);

                item.addEventListener('click', () => {
                    list.querySelectorAll('.folder-item').forEach(el => el.classList.remove('selected'));
                    item.classList.add('selected');
                    folderBrowserSelectedPath = item.dataset.path;
                });
                item.addEventListener('dblclick', () => {
                    if (item.dataset.path) loadDirectories(item.dataset.path);
                });

                list.appendChild(item);
            });
        } catch (err) {
            list.innerHTML = `<div class="folder-item">❌ ${escapeHtml(err.message || 'Failed to load')}</div>`;
        }
    }
}
