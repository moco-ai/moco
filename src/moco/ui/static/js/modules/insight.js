/**
 * Insight Panel module
 */
import { escapeHtml } from './formatter.js';
import { getCurrentSessionId } from '../../main.js';

const INSIGHT_STORAGE_PREFIX = 'moco_insights_v1:';
const MAX_STORED_INSIGHTS_PER_SESSION = 200;

function _storageKey(sessionId) {
    return `${INSIGHT_STORAGE_PREFIX}${sessionId}`;
}

function _loadStoredInsights(sessionId) {
    try {
        const raw = localStorage.getItem(_storageKey(sessionId));
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        console.warn('Failed to load stored insights:', e);
        return [];
    }
}

function _saveStoredInsights(sessionId, items) {
    try {
        localStorage.setItem(_storageKey(sessionId), JSON.stringify(items.slice(0, MAX_STORED_INSIGHTS_PER_SESSION)));
    } catch (e) {
        // localStorage が満杯でも動作は継続する
        console.warn('Failed to save stored insights:', e);
    }
}

function _appendStoredInsight(sessionId, item) {
    if (!sessionId) return;
    const items = _loadStoredInsights(sessionId);
    // 新しいものを先頭に
    items.unshift(item);
    _saveStoredInsights(sessionId, items);
}

function _renderRecallItem(data, { animate = false } = {}) {
    const content = document.getElementById('insight-content');
    const empty = content.querySelector('.empty-insight');
    if (empty) empty.remove();

    const id = `recall-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
    const timestamp = data._ts ? new Date(data._ts).toLocaleTimeString() : new Date().toLocaleTimeString();

    let detailHtml = '';
    if (data.details) {
        if (typeof data.details === 'object') {
            detailHtml = `<pre class="recall-details"><code>${escapeHtml(JSON.stringify(data.details, null, 2))}</code></pre>`;
        } else {
            detailHtml = `<p class="recall-details">${escapeHtml(String(data.details))}</p>`;
        }
    }

    const html = `
        <div class="recall-item" id="${id}">
            <div class="recall-header">
                <span class="recall-type">${escapeHtml(data.recall_type || 'Recall')}</span>
                <span class="recall-time">${timestamp}</span>
            </div>
            <div class="recall-query">${escapeHtml(data.query || '')}</div>
            ${detailHtml}
        </div>
    `;

    content.insertAdjacentHTML('afterbegin', html);
    const el = document.getElementById(id);
    if (animate && el) setTimeout(() => el.classList.add('appear'), 10);
}

// Debounce to prevent double-firing when multiple app.js loads exist
let _toggleLock = false;

export function toggleInsight() {
    if (_toggleLock) return;
    _toggleLock = true;
    setTimeout(() => { _toggleLock = false; }, 50);

    const panel = document.getElementById('insight-panel');
    if (!panel) return;

    if (panel.classList.contains('open')) {
        panel.classList.remove('open');
        panel.classList.remove('expanded');
    } else {
        panel.classList.add('open');
    }
}

export function toggleExpand() {
    const panel = document.getElementById('insight-panel');
    if (!panel) return;
    // Ensure visible when expanding
    panel.classList.add('open');
    panel.classList.toggle('expanded');

    const btn = document.getElementById('expand-insight-btn');
    if (panel.classList.contains('expanded')) {
        btn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7"/>
            </svg>
        `;
        btn.title = "Collapse";
    } else {
        btn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
            </svg>
        `;
        btn.title = "Expand";
    }
}

export const toggleExpandInsight = toggleExpand;

export function addRecall(data) {
    // セッションごとに保存（リロード/切替で消えないように）
    const sessionId = getCurrentSessionId();
    _appendStoredInsight(sessionId, { ...data, _ts: Date.now() });

    _renderRecallItem(data, { animate: true });
}

export function clearInsights() {
    const content = document.getElementById('insight-content');
    content.innerHTML = `
        <div class="empty-insight">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="16" x2="12" y2="12"></line>
                <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
            <p>No insights yet. Moco will show relevant information here during the conversation.</p>
        </div>
    `;
}

export function loadInsightsForSession(sessionId) {
    clearInsights();
    if (!sessionId) return;

    const items = _loadStoredInsights(sessionId);
    if (!items.length) return;

    // stored は新しい順（先頭が最新）
    for (const item of items) {
        _renderRecallItem(item, { animate: false });
    }
}

// Stats Dashboard
let statsInterval = null;

const CONTROLS_HTML = `
    <div class="stats-controls">
        <select id="stats-scope-select" class="stats-select">
            <option value="today">Today</option>
            <option value="session">Current Session</option>
            <option value="all">All Time</option>
        </select>
        <button class="stats-refresh-btn" id="stats-refresh-btn" title="Refresh">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
            </svg>
        </button>
    </div>
`;

export async function loadStats() {
    const display = document.getElementById('stats-data-display');
    const content = document.getElementById('stats-content');

    if (display) {
        display.style.opacity = '0.5';
    } else if (content) {
        content.innerHTML = '<div class="stats-loading">Loading stats...</div>';
    }

    try {
        const scope = document.getElementById('stats-scope-select')?.value || 'today';
        const sessionId = getCurrentSessionId();

        let url = `/api/stats?scope=${scope}`;
        if (sessionId) {
            url += `&session_id=${sessionId}`;
        }

        const res = await fetch(url);
        const data = await res.json();
        renderStats(data);
    } catch (e) {
        console.error('Failed to load stats:', e);
        if (display) display.style.opacity = '1';
    } finally {
        if (display) display.style.opacity = '1';
    }
}

function renderEmptyState(container, scope) {
    let message = '';
    const icon = `
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M21 12V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h7"></path>
            <path d="M16 19h6"></path>
            <path d="M19 16v6"></path>
            <circle cx="9" cy="12" r="1"></circle>
            <circle cx="13" cy="12" r="1"></circle>
            <circle cx="17" cy="12" r="1"></circle>
        </svg>
    `;

    switch (scope) {
        case 'session':
            message = 'No stats for this session yet. Start chatting to see performance metrics.';
            break;
        case 'today':
            message = 'No activity recorded today. Your data will appear here once tasks are completed.';
            break;
        case 'all':
            message = 'No historical data found. Complete some tasks to build your performance profile.';
            break;
        default:
            message = 'No data available for the selected period.';
    }

    container.innerHTML = `
        <div class="stats-empty-state">
            <div class="empty-icon">${icon}</div>
            <p class="empty-text">${message}</p>
            <button class="retry-btn" id="stats-retry-btn">Refresh Dashboard</button>
        </div>
    `;

    document.getElementById('stats-retry-btn')?.addEventListener('click', () => loadStats());
}

function renderStats(data) {
    const content = document.getElementById('stats-content');
    if (!content) return;

    // Initialize structure if not exists
    if (!content.querySelector('.stats-controls')) {
        content.innerHTML = `
            ${CONTROLS_HTML}
            <div id="stats-data-display"></div>
        `;
        document.getElementById('stats-scope-select').addEventListener('change', loadStats);
        document.getElementById('stats-refresh-btn').addEventListener('click', loadStats);
    }

    const display = document.getElementById('stats-data-display');
    const scopeSelect = document.getElementById('stats-scope-select');
    const scopeLabel = scopeSelect?.options[scopeSelect.selectedIndex]?.text || 'Today';

    // Check if data is empty using generic keys (avg_score, count)
    const isEmpty = !data || (data.count === 0 && (!data.recent_tasks || data.recent_tasks.length === 0));

    if (isEmpty) {
        renderEmptyState(display, data.scope || scopeSelect?.value || 'today');
        return;
    }

    const avgScore = data.avg_score ?? 0;
    const scoreColor = avgScore >= 0.8 ? '#4ade80' :
                       avgScore >= 0.6 ? '#facc15' : '#f87171';

    // スコア推移のミニグラフ
    const trendHtml = data.score_trend?.length > 0 ?
        `<div class="stats-trend">
            ${data.score_trend.map((s, i) => {
                const height = Math.max(10, s * 100);
                const color = s >= 0.8 ? '#4ade80' : s >= 0.6 ? '#facc15' : '#f87171';
                return `<div class="trend-bar" style="height:${height}%;background:${color}" title="${(s * 100).toFixed(0)}%"></div>`;
            }).join('')}
        </div>` : '';

    // 全体メトリクス
    const om = data.overall_metrics || {};
    const overallMetricsHtml = `
        <div class="stats-section">
            <div class="section-title">📊 Overall Metrics</div>
            <div class="metrics-grid">
                <div class="metric-item">
                    <span class="metric-value">${om.avg_complexity ?? 0}</span>
                    <span class="metric-label">Avg Complexity</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value">${om.avg_delegation ?? 0}</span>
                    <span class="metric-label">Avg Delegation</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value">${om.todo_usage_rate ?? 0}%</span>
                    <span class="metric-label">Todo Usage</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value">${om.avg_history_turns ?? 0}</span>
                    <span class="metric-label">Turns</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value">${om.summaries ?? 0}</span>
                    <span class="metric-label">Summaries</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value">${om.avg_prompt_specificity ?? 0}</span>
                    <span class="metric-label">Prompt Spec.</span>
                </div>
            </div>
        </div>
    `;

    // エージェント統計
    let agentStatsHtml = '';
    if (data.agent_stats && Object.keys(data.agent_stats).length > 0) {
        const cards = Object.entries(data.agent_stats).map(([name, stats]) => {
            const successRate = stats.total > 0 ? (stats.success / stats.total * 100).toFixed(0) : 0;
            const agentAvgScore = stats.avg_score ? (stats.avg_score * 100).toFixed(0) : 0;
            const agentScoreColor = (stats.avg_score ?? 0) >= 0.8 ? '#4ade80' : (stats.avg_score ?? 0) >= 0.6 ? '#facc15' : '#f87171';

            return `
                <div class="agent-stat-card">
                    <div class="agent-stat-header">
                        <span class="agent-stat-name">${escapeHtml(name)}</span>
                        <span class="agent-stat-count">${stats.total ?? 0} tasks</span>
                    </div>
                    <div class="agent-stat-metrics">
                        <div class="agent-metric">
                            <span class="agent-metric-label">Success</span>
                            <span class="agent-metric-value">${successRate}%</span>
                        </div>
                        <div class="agent-metric">
                            <span class="agent-metric-label">Avg Score</span>
                            <span class="agent-metric-value" style="color:${agentScoreColor}">${agentAvgScore}%</span>
                        </div>
                    </div>
                    <div class="agent-stat-detail-metrics">
                        <div class="agent-detail-metric">
                            <span class="detail-label">Tokens</span>
                            <span class="detail-value">${stats.avg_tokens ? (stats.avg_tokens / 1000).toFixed(1) + 'k' : '-'}</span>
                        </div>
                        <div class="agent-detail-metric">
                            <span class="detail-label">Time</span>
                            <span class="detail-value">${stats.avg_time_ms ? (stats.avg_time_ms / 1000).toFixed(1) + 's' : '-'}</span>
                        </div>
                        <div class="agent-detail-metric">
                            <span class="detail-label">Errors</span>
                            <span class="detail-value" style="color:${(stats.error_rate ?? 0) > 0 ? '#f87171' : '#4ade80'}">${stats.error_rate ?? 0}%</span>
                        </div>
                    </div>
                    <div class="agent-stat-detail-metrics" style="border-top:none; padding-top:0; margin-top:0;">
                        <div class="agent-detail-metric">
                            <span class="detail-label">Summaries</span>
                            <span class="detail-value">${stats.summaries ?? 0}</span>
                        </div>
                        <div class="agent-detail-metric">
                            <span class="detail-label">Turns</span>
                            <span class="detail-value">${stats.avg_history_turns ?? 0}</span>
                        </div>
                    </div>
                    <div class="agent-stat-bar-container">
                        <div class="agent-stat-bar" style="width:${agentAvgScore}%"></div>
                    </div>
                </div>
            `;
        }).join('');

        agentStatsHtml = `
            <div class="agent-stats-section">
                <div class="section-title">🤖 Agent Performance</div>
                <div class="agent-stats-grid">
                    ${cards}
                </div>
            </div>
        `;
    }

    // プロファイル別
    const profileHtml = data.profile_stats?.map(p => {
        const pAvgScore = p.avg_score ?? 0;
        const width = Math.max(5, pAvgScore * 100);
        const color = pAvgScore >= 0.8 ? '#4ade80' : pAvgScore >= 0.6 ? '#facc15' : '#f87171';
        return `
            <div class="profile-stat">
                <span class="profile-name">${escapeHtml(p.profile || 'Unknown')}</span>
                <div class="profile-bar-container">
                    <div class="profile-bar" style="width:${width}%;background:${color}"></div>
                </div>
                <span class="profile-score">${(pAvgScore * 100).toFixed(0)}%</span>
            </div>
        `;
    }).join('') || '';

    // 最新タスク
    const tasksHtml = data.recent_tasks?.map(t => `
        <div class="recent-task">
            <span class="task-time">${t.time}</span>
            <span class="task-name">${escapeHtml(t.task)}</span>
            <span class="task-score" style="color:${t.score >= 0.8 ? '#4ade80' : t.score >= 0.6 ? '#facc15' : '#f87171'}">${(t.score * 100).toFixed(0)}%</span>
        </div>
    `).join('') || '<div class="no-data">No tasks yet</div>';

    // Update display content without overwriting controls
    display.innerHTML = `
        <div class="stats-summary">
            <div class="stat-card">
                <div class="stat-value" style="color:${scoreColor}">${(avgScore * 100).toFixed(0)}%</div>
                <div class="stat-label">${scopeLabel} Avg</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${data.count ?? 0}</div>
                <div class="stat-label">Tasks</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${data.success_rate ?? 0}%</div>
                <div class="stat-label">Success Rate</div>
            </div>
        </div>

        <div class="stats-section">
            <div class="section-title">📈 Score Trend (Last 10)</div>
            ${trendHtml}
        </div>

        ${overallMetricsHtml}

        ${agentStatsHtml}

        <div class="stats-section">
            <div class="section-title">📊 By Profile</div>
            ${profileHtml}
        </div>

        <div class="stats-section">
            <div class="section-title">🕐 Recent Tasks</div>
            ${tasksHtml}
        </div>
    `;
}

export function startStatsPolling() {
    loadStats();
    if (statsInterval) clearInterval(statsInterval);
    statsInterval = setInterval(loadStats, 30000); // 30秒ごと
}

export function stopStatsPolling() {
    if (statsInterval) {
        clearInterval(statsInterval);
        statsInterval = null;
    }
}
