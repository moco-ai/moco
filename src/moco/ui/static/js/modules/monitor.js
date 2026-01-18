/**
 * Agent collaboration monitor module
 */
import { scrollToBottom } from './ui.js';

const COMPLETION_DELAY = 3000;

export const AgentMonitor = {
    container: null,
    logList: null,
    containerId: null,
    isExpanded: false,
    lastAgentName: null,

    init() {
        if (this.container) {
            this.container.remove();
        }
        this.container = null;
        this.logList = null;
        this.containerId = null;
        this.isExpanded = false;
        this.lastAgentName = null;
    },

    create() {
        const chatContainer = document.getElementById('chat-container');
        this.containerId = `agent-monitor-${Date.now()}`;

        const monitorHtml = `
            <div id="${this.containerId}" class="agent-monitor">
                <div class="agent-monitor-header">
                    <div class="agent-monitor-title">Agent Activity</div>
                    <button class="agent-expand-btn" title="Toggle Log">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </button>
                </div>
                <div class="agent-log-container">
                    <div class="agent-log-list"></div>
                </div>
            </div>
        `;
        chatContainer.insertAdjacentHTML('beforeend', monitorHtml);

        this.container = document.getElementById(this.containerId);
        this.logList = this.container.querySelector('.agent-log-list');

        const expandBtn = this.container.querySelector('.agent-expand-btn');
        expandBtn.addEventListener('click', () => {
            this.isExpanded = !this.isExpanded;
            this.container.classList.toggle('expanded', this.isExpanded);
        });
    },

    show() {
        if (!this.container) {
            this.create();
        }
        if (this.container && this.container.style.display !== 'flex') {
            this.container.style.display = 'flex';
            setTimeout(() => this.container.classList.add('active'), 10);
            scrollToBottom();
        }
    },

    hide() {
        if (!this.container) return;
        
        this._markAllAsCompleted();
        this.container.classList.remove('active');
        this.container.classList.add('completed');
        
        setTimeout(() => {
            if (this.container) {
                this.container.style.opacity = '0';
                this.container.style.transform = 'translateY(-10px)';
                setTimeout(() => {
                    if (this.container) {
                        this.container.style.display = 'none';
                    }
                }, 300);
            }
        }, COMPLETION_DELAY);
    },

    /**
     * Adds an agent delegation log entry
     */
    addAgent(name, parentName = null) {
        if (this.lastAgentName === name) return;
        this.lastAgentName = name;
        
        this.show();

        const entry = document.createElement('div');
        entry.className = 'agent-log-entry delegate fade-in';
        
        const icon = this._getAgentIcon(name, !parentName);
        
        entry.innerHTML = `
            <div class="log-icon">${icon}</div>
            <div class="log-agent">${name}</div>
            <div class="log-tool">is processing...</div>
        `;

        this.logList.appendChild(entry);
        this._scrollToBottomIfExpanded();
    },

    /**
     * Adds a tool execution log entry
     */
    setActive(name, toolName = null) {
        if (!toolName) {
            this._markAllAsCompleted();
            return;
        }
        
        this.show();

        const entry = document.createElement('div');
        entry.className = 'agent-log-entry tool fade-in';
        
        entry.innerHTML = `
            <div class="log-indent"></div>
            <div class="log-icon">🛠️</div>
            <div class="log-tool">Using tool: <span style="color: var(--text-primary)">${toolName}</span></div>
            <div class="log-status">⏳</div>
        `;

        this.logList.appendChild(entry);
        
        // Update previous "is processing" text if any
        const lastDelegate = Array.from(this.logList.querySelectorAll('.delegate')).pop();
        if (lastDelegate && lastDelegate.querySelector('.log-agent').textContent === name) {
            lastDelegate.querySelector('.log-tool').textContent = 'working';
        }

        this._scrollToBottomIfExpanded();
    },

    _markAllAsCompleted() {
        if (!this.logList) return;
        
        // Update tool statuses
        const statuses = this.logList.querySelectorAll('.log-status');
        statuses.forEach(status => {
            if (status.textContent === '⏳') {
                status.textContent = '✅';
                status.classList.add('status-done');
            }
        });
        
        // Update delegate texts
        const delegates = this.logList.querySelectorAll('.delegate .log-tool');
        delegates.forEach(tool => {
            if (tool.textContent === 'is processing...' || tool.textContent === 'working') {
                tool.textContent = 'completed task';
            }
        });
    },

    _getAgentIcon(name, isOrchestrator) {
        const lowerName = name.toLowerCase();
        if (isOrchestrator || lowerName.includes('orchestrator') || lowerName.includes('boss')) {
            return '👑';
        }
        if (lowerName.includes('coder') || lowerName.includes('developer')) return '💻';
        if (lowerName.includes('researcher')) return '🔍';
        if (lowerName.includes('reviewer')) return '⚖️';
        return '👤';
    },

    _scrollToBottomIfExpanded() {
        if (this.isExpanded) {
            const container = this.container.querySelector('.agent-log-container');
            container.scrollTop = container.scrollHeight;
        }
        scrollToBottom();
    }
};
