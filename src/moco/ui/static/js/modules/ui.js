/**
 * UI Rendering and DOM manipulation module
 */
import { formatContent, escapeHtml } from './formatter.js';

export function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const id = `toast-${Date.now()}`;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.id = id;
    
    let icon = 'ℹ️';
    if (type === 'error') icon = '❌';
    if (type === 'success') icon = '✅';
    if (type === 'warning') icon = '⚠️';

    toast.innerHTML = `
        <span class="toast-icon">${icon}</span>
        <span class="toast-message">${escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    // Auto remove
    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => {
            if (toast.parentNode) {
                container.removeChild(toast);
            }
        }, 500);
    }, 3000);
}

export function scrollToBottom() {
    const container = document.getElementById('chat-container');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

export function clearMessages() {
    const container = document.getElementById('chat-container');
    container.querySelectorAll('.message').forEach(el => el.remove());
    container.querySelectorAll('.progress-message').forEach(el => el.remove());
}

export function showWelcome(show = true) {
    const el = document.getElementById('welcome-message');
    if (el) el.style.display = show ? 'flex' : 'none';
}

export function addMessage(role, content, isStreaming = false) {
    const container = document.getElementById('chat-container');
    const id = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const avatar = role === 'user' ? '👤' : '🤖';
    const streamingContent = isStreaming
        ? `<div class="typing-indicator"><span></span><span></span><span></span></div>`
        : formatContent(content);

    const html = `
        <div class="message message-${role}" id="${id}">
            <div class="message-content">
                <div class="message-avatar">${avatar}</div>
                <div class="message-text">${streamingContent}</div>
            </div>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', html);
    const msgEl = document.getElementById(id);
    if (msgEl) {
        setTimeout(() => msgEl.classList.add('appear'), 10);
        if (!isStreaming) addCopyButtons(id);
    }
    // メッセージが追加されたら、特にAI応答中は常に最下部にスクロール
    if (role === 'assistant') {
        scrollToBottom();
    }
    return id;
}

export function updateMessage(id, content) {
    const el = document.querySelector(`#${id} .message-text`);
    if (el) {
        el.innerHTML = formatContent(content);
        // ストリーミング中(更新中)はスクロールを維持
        scrollToBottom();
    }
}

export function addCopyButtons(messageId) {
    const msgEl = document.getElementById(messageId);
    if (!msgEl) return;
    
    msgEl.querySelectorAll('pre').forEach(pre => {
        // Skip if already has copy button (from formatter.js)
        if (pre.querySelector('.code-copy-btn') || pre.querySelector('.copy-btn')) return;
        
        pre.style.position = 'relative';
        const btn = document.createElement('button');
        btn.className = 'copy-btn';
        btn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
            <span>Copy</span>
            <span class="tooltip">Copy to clipboard</span>
        `;
        btn.onclick = () => {
            const code = pre.querySelector('code').innerText;
            navigator.clipboard.writeText(code).then(() => {
                const span = btn.querySelector('span:not(.tooltip)');
                const tooltip = btn.querySelector('.tooltip');
                const originalText = span.innerText;
                span.innerText = 'Copied!';
                if (tooltip) tooltip.innerText = 'Copied!';
                btn.classList.add('copied');
                setTimeout(() => {
                    span.innerText = originalText;
                    if (tooltip) tooltip.innerText = 'Copy to clipboard';
                    btn.classList.remove('copied');
                }, 2000);
            });
        };
        pre.appendChild(btn);
    });
}

export function addProgressMessage() {
    const container = document.getElementById('chat-container');
    const id = `progress-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const html = `
        <div class="progress-message" id="${id}">
            <details class="progress-details">
                <summary class="progress-summary">
                    <span class="progress-icon">▶</span>
                    <span class="progress-title">Tool execution logs</span>
                </summary>
                <div class="progress-items"></div>
            </details>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', html);
    return id;
}

export function appendProgressItem(id, item) {
    const el = document.querySelector(`#${id} .progress-items`);
    if (el) {
        el.insertAdjacentHTML('beforeend', `<div class="progress-item">${escapeHtml(item)}</div>`);
        scrollToBottom();
    }
}

export function updateProgressItem(id, index, newItem) {
    const el = document.querySelector(`#${id} .progress-items`);
    if (el) {
        const items = el.querySelectorAll('.progress-item');
        if (items[index]) {
            items[index].textContent = newItem;
        }
    }
}

export function addThinkingMessage() {
    const container = document.getElementById('chat-container');
    const id = `thinking-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const html = `
        <div class="thinking-message" id="${id}">
            <details class="thinking-details">
                <summary class="thinking-summary">
                    <span class="thinking-icon">💭</span>
                    <span class="thinking-title">Thinking process...</span>
                </summary>
                <div class="thinking-content"></div>
            </details>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', html);
    scrollToBottom();
    return id;
}

export function updateThinkingContent(id, content) {
    const el = document.querySelector(`#${id} .thinking-content`);
    if (el) {
        el.innerHTML = formatContent(content);
        
        // ユーザーが最下部にいる場合のみスクロール
        const container = document.getElementById('chat-container');
        if (container) {
            const isAtBottom = container.scrollHeight - container.scrollTop <= container.clientHeight + 100;
            if (isAtBottom) {
                scrollToBottom();
            }
        }
    }
}

export function updateProgressMessage(id, items) {
    const el = document.querySelector(`#${id} .progress-items`);
    if (el) {
        el.innerHTML = items.map(item => `<div class="progress-item">${escapeHtml(item)}</div>`).join('');
        scrollToBottom();
    }
}

export function finalizeProgressMessage(id, items) {
    const el = document.getElementById(id);
    if (el) {
        if (items.length === 0) {
            el.remove();
        } else {
            const details = el.querySelector('.progress-details');
            if (details) details.classList.add('completed');
        }
    }
}

export function setLoading(loading) {
    const btn = document.getElementById('send-btn');
    const stopBtn = document.getElementById('stop-btn');
    const input = document.getElementById('message-input');
    
    if (loading) {
        // Hide send button, show stop button
        if (btn) {
            btn.classList.add('hidden');
            btn.disabled = true;
        }
        if (stopBtn) {
            stopBtn.classList.remove('hidden');
            stopBtn.disabled = false;
        }
        input.disabled = true;
        input.placeholder = "Waiting for response...";
    } else {
        // Hide stop button, show send button
        if (stopBtn) {
            stopBtn.classList.add('hidden');
            stopBtn.disabled = true;
        }
        if (btn) {
            btn.classList.remove('hidden');
            btn.disabled = false;
            if (input.value.trim().length > 0) btn.classList.add('active');
        }
        input.disabled = false;
        input.placeholder = "Send a message...";
    }
}

export function showCancelledSystemMessage() {
    addMessage('assistant', 'ユーザーが処理を停止しました');
}

export function addApprovalMessage(sessionId, tool, args, onAction = null) {
    const container = document.getElementById('chat-container');
    const id = `approval-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const argsStr = JSON.stringify(args, null, 2);

    const html = `
        <div class="message message-assistant approval-request" id="${id}">
            <div class="message-content">
                <div class="message-avatar">🛡️</div>
                <div class="message-text">
                    <div class="approval-header">
                        <strong>Approval Required</strong>
                    </div>
                    <div class="approval-body">
                        <p>The agent wants to execute the following tool:</p>
                        <div class="approval-tool"><code>${escapeHtml(tool)}</code></div>
                        <pre class="approval-args"><code>${escapeHtml(argsStr)}</code></pre>
                    </div>
                    <div class="approval-actions">
                        <button class="approve-btn" id="${id}-approve" data-approval-id="${id}">Approve</button>
                        <button class="reject-btn" id="${id}-reject" data-approval-id="${id}">Reject</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', html);

    // onActionが提供される場合のみ設定（後方互換性）
    if (onAction) {
        document.getElementById(`${id}-approve`).onclick = () => onAction(true, id);
        document.getElementById(`${id}-reject`).onclick = () => onAction(false, id);
    }

    scrollToBottom();
    return id;
}

export function updateApprovalStatus(id, approved) {
    const el = document.getElementById(id);
    if (!el) return;

    const actions = el.querySelector('.approval-actions');
    if (actions) {
        actions.innerHTML = `
            <div class="approval-result ${approved ? 'approved' : 'rejected'}">
                ${approved ? '✅ Approved' : '❌ Rejected'}
            </div>
        `;
    }
    el.classList.add('completed');
}

export function renderSessions(sessions, currentSessionId) {
    const container = document.getElementById('sessions-list');
    container.innerHTML = sessions.map(s => {
        const date = s.updated_at ? new Date(s.updated_at).toLocaleDateString() : '';
        return `
            <div class="session-item ${s.session_id === currentSessionId ? 'active' : ''}"
                 data-session-id="${s.session_id}">
                <div class="session-info">
                    <span class="session-title" title="${escapeHtml(s.title || 'Untitled')}">${escapeHtml(s.title || 'Untitled')}</span>
                    <span class="session-date">${date}</span>
                </div>
                <div class="session-actions">
                    <button class="rename-session-btn" data-session-id="${s.session_id}" title="Rename session">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                        </svg>
                    </button>
                    <button class="delete-session-btn" data-session-id="${s.session_id}" title="Delete session">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

// === 経過時間表示 ===

let elapsedTimeEl = null;

export function updateElapsedTime(timeStr) {
    if (!elapsedTimeEl) {
        // 経過時間表示要素を作成
        elapsedTimeEl = document.createElement('div');
        elapsedTimeEl.id = 'elapsed-time';
        elapsedTimeEl.className = 'elapsed-time';
        elapsedTimeEl.innerHTML = `<span class="elapsed-icon">⏱️</span><span class="elapsed-value">${timeStr}</span>`;
        
        // 入力欄の上に配置
        const inputContainer = document.querySelector('.input-container');
        if (inputContainer) {
            inputContainer.parentNode.insertBefore(elapsedTimeEl, inputContainer);
        }
    } else {
        elapsedTimeEl.querySelector('.elapsed-value').textContent = timeStr;
        elapsedTimeEl.style.display = 'flex';
    }
}

export function showFinalElapsedTime(seconds) {
    if (elapsedTimeEl) {
        elapsedTimeEl.innerHTML = `<span class="elapsed-icon">✅</span><span class="elapsed-value">${seconds}s</span>`;
        elapsedTimeEl.classList.add('elapsed-done');
        // 3秒後にフェードアウト
        setTimeout(() => {
            if (elapsedTimeEl) {
                elapsedTimeEl.classList.add('elapsed-fadeout');
                setTimeout(() => hideElapsedTime(), 500);
            }
        }, 3000);
    }
}

export function hideElapsedTime() {
    if (elapsedTimeEl) {
        elapsedTimeEl.style.display = 'none';
        elapsedTimeEl.classList.remove('elapsed-done', 'elapsed-fadeout');
    }
}
