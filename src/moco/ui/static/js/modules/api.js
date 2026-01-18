/**
 * API communication module for Moco
 */

/**
 * Connect to WebSocket endpoint for real-time approval requests
 * @param {string} sessionId - Session identifier
 * @param {Function} onMessage - Callback for incoming messages
 * @param {Function} onError - Callback for errors
 * @param {Function} onOpen - Callback when connection opens
 * @param {Function} onClose - Callback when connection closes
 * @returns {Promise<WebSocket>} - WebSocket connection
 */
export function connectWebSocket(sessionId, onMessage, onError, onOpen, onClose) {
    return new Promise((resolve, reject) => {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws?session_id=${sessionId}`);

        ws.onopen = () => {
            if (onOpen) onOpen();
            resolve(ws);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (onMessage) onMessage(data);
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
                if (onError) onError(e);
            }
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            if (onError) onError(error);
            reject(error);
        };

        ws.onclose = () => {
            if (onClose) onClose();
        };
    });
}

/**
 * Send approval response via WebSocket
 * @param {WebSocket} ws - WebSocket connection
 * @param {string} approvalId - Approval request ID
 * @param {boolean} approved - Approval decision
 * @returns {boolean} - True if message sent successfully
 */
export function sendApprovalResponse(ws, approvalId, approved) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'approval_response',
            approval_id: approvalId,
            approved: approved
        }));
        return true;
    }
    console.warn('WebSocket is not open. Cannot send approval response.');
    return false;
}

export async function fetchProfiles() {
    const res = await fetch('/api/profiles');
    if (!res.ok) throw new Error('Failed to load profiles');
    return await res.json();
}

export async function fetchSessions(profile) {
    const res = await fetch(`/api/sessions?limit=30&profile=${encodeURIComponent(profile)}`);
    if (!res.ok) throw new Error('Failed to load sessions');
    return await res.json();
}

export async function fetchSessionDetails(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}`);
    if (!res.ok) {
        const error = new Error('Failed to load session');
        error.status = res.status;
        throw error;
    }
    return await res.json();
}

export async function deleteSession(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to delete session');
    return await res.json();
}

export async function updateSession(sessionId, data) {
    const res = await fetch(`/api/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error('Failed to update session');
    return await res.json();
}

export async function approveTool(sessionId, approved) {
    const res = await fetch(`/api/sessions/${sessionId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved })
    });
    if (!res.ok) throw new Error('Failed to send approval');
    return await res.json();
}

export async function cancelSession(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
        console.error('Failed to cancel session', data || res.statusText);
        throw new Error('Failed to cancel session');
    }
    if (data && data.status === 'not_found') {
        console.warn('Cancel API responded with not_found for session', sessionId, data);
    } else {
        console.log('Cancel API response', data);
    }
    return data;
}

export async function* streamChat(params) {
    const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });

    if (!res.ok) throw new Error('Failed to start chat stream');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                try {
                    yield JSON.parse(line.slice(6));
                } catch (e) {
                    console.error('Error parsing SSE data:', e);
                }
            }
        }
    }
}

/**
 * Browse directories on the server
 * @param {string} path - Directory path to browse
 * @returns {Promise<Object>} - Directory listing
 */
export async function browseDirectories(path = "") {
    const url = path ? `/api/browse-directories?path=${encodeURIComponent(path)}` : '/api/browse-directories';
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to browse directories');
    return await res.json();
}
