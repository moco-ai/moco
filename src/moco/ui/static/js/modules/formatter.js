/**
 * Content formatting and transformation module
 */

// Language icons and display names
const LANGUAGE_CONFIG = {
    python: { icon: '🐍', name: 'Python' },
    py: { icon: '🐍', name: 'Python' },
    javascript: { icon: '📜', name: 'JavaScript' },
    js: { icon: '📜', name: 'JavaScript' },
    typescript: { icon: '📘', name: 'TypeScript' },
    ts: { icon: '📘', name: 'TypeScript' },
    json: { icon: '📋', name: 'JSON' },
    bash: { icon: '💻', name: 'Bash' },
    shell: { icon: '💻', name: 'Shell' },
    sh: { icon: '💻', name: 'Shell' },
    sql: { icon: '🗃️', name: 'SQL' },
    html: { icon: '🌐', name: 'HTML' },
    css: { icon: '🎨', name: 'CSS' },
    yaml: { icon: '⚙️', name: 'YAML' },
    yml: { icon: '⚙️', name: 'YAML' },
    markdown: { icon: '📝', name: 'Markdown' },
    md: { icon: '📝', name: 'Markdown' },
    go: { icon: '🐹', name: 'Go' },
    rust: { icon: '🦀', name: 'Rust' },
    rs: { icon: '🦀', name: 'Rust' },
    java: { icon: '☕', name: 'Java' },
    c: { icon: '⚡', name: 'C' },
    cpp: { icon: '⚡', name: 'C++' },
    xml: { icon: '📄', name: 'XML' },
    plaintext: { icon: '📄', name: 'Text' },
    text: { icon: '📄', name: 'Text' },
};

/**
 * Get language configuration
 */
function getLangConfig(lang) {
    if (!lang) return { icon: '📄', name: 'Code' };
    const normalizedLang = lang.toLowerCase();
    return LANGUAGE_CONFIG[normalizedLang] || { icon: '📄', name: lang.toUpperCase() };
}

/**
 * Apply syntax highlighting using highlight.js
 */
export function highlightCode(code, lang) {
    if (typeof hljs === 'undefined') {
        return escapeHtml(code);
    }
    
    try {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        } else {
            // Auto-detect language
            return hljs.highlightAuto(code).value;
        }
    } catch (e) {
        console.warn('Highlight.js error:', e);
        return escapeHtml(code);
    }
}

export function escapeHtml(text) {
    if (typeof text !== 'string') return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

export function formatJSON(jsonStr) {
    try {
        const unescaped = jsonStr
            .replace(/&quot;/g, '"')
            .replace(/&lt;/g, '<')
            .replace(/&gt;/g, '>')
            .replace(/&amp;/g, '&');
        const obj = JSON.parse(unescaped);
        const formatted = JSON.stringify(obj, null, 2);

        return escapeHtml(formatted)
            .replace(/"([^"]+)":/g, '<span class="json-key">"$1"</span>:')
            .replace(/: "([^"]+)"/g, ': <span class="json-string">"$1"</span>')
            .replace(/: (\d+)/g, ': <span class="json-number">$1</span>')
            .replace(/: (true|false)/g, ': <span class="json-bool">$1</span>')
            .replace(/: (null)/g, ': <span class="json-null">$1</span>');
    } catch {
        return jsonStr;
    }
}

export function formatContent(content) {
    if (!content) return '';

    try {
        // Search Results Cardification
        content = content.replace(/\[Search Result: (.*?) \| (.*?) \| (.*?)\]/g, (match, title, snippet, url) => {
            return `
                <a href="${url}" target="_blank" class="search-card">
                    <div class="search-card-title">${title}</div>
                    <div class="search-card-snippet">${snippet}</div>
                    <div class="search-card-url">${url}</div>
                </a>
            `;
        });

        const codeBlocks = [];
        let html = escapeHtml(content);

        // Code blocks with syntax highlighting - replace with placeholder first
        html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (match, lang, code) => {
            const unescapedCode = code.replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').trim();
            const langConfig = getLangConfig(lang);
            const langClass = lang ? `lang-${lang.toLowerCase()}` : '';
            
            // Create code header with language label and copy button (no newlines to prevent <br> injection)
            const codeHeader = `<div class="code-header"><span class="code-lang-label ${langClass}"><span class="lang-icon">${langConfig.icon}</span>${langConfig.name}</span><button class="code-copy-btn" onclick="copyCodeBlock(this)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg><span>Copy</span></button></div>`;
            
            let codeBlock;
            try {
                if (lang === 'json' || (!lang && unescapedCode.startsWith('{'))) {
                    JSON.parse(unescapedCode);
                    codeBlock = `<pre class="json-block">${codeHeader}<code>${formatJSON(code)}</code></pre>`;
                } else {
                    throw new Error();
                }
            } catch {
                // Apply syntax highlighting
                const highlightedCode = highlightCode(unescapedCode, lang);
                codeBlock = `<pre data-lang="${lang || ''}">${codeHeader}<code class="hljs">${highlightedCode}</code></pre>`;
            }

            // Store code block and return placeholder
            const placeholder = `__CODE_BLOCK_${codeBlocks.length}__`;
            codeBlocks.push(codeBlock);
            return placeholder;
        });

        // Inline code and bold
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

        // Line-based processing
        const lines = html.split('\n');
        let resultLines = [];
        let currentList = [];
        let currentTable = [];

        const flush = () => {
            if (currentList.length > 0) {
                resultLines.push('<ul class="md-list">' + currentList.join('') + '</ul>');
                currentList = [];
            }
            if (currentTable.length > 0) {
                resultLines.push('<div class="md-table-wrapper"><table class="md-table">' + currentTable.join('') + '</tbody></table></div>');
                currentTable = [];
            }
        };

        for (let line of lines) {
            const trimmed = line.trim();

            if (line.startsWith('### ')) {
                flush();
                resultLines.push(`<h4 class="md-h4">${line.substring(4)}</h4>`);
                continue;
            }
            if (line.startsWith('## ')) {
                flush();
                resultLines.push(`<h3 class="md-h3">${line.substring(3)}</h3>`);
                continue;
            }
            if (line.startsWith('# ')) {
                flush();
                resultLines.push(`<h2 class="md-h2">${line.substring(2)}</h2>`);
                continue;
            }

            if (trimmed === '---') {
                flush();
                resultLines.push('<hr class="md-hr">');
                continue;
            }

            const ulMatch = line.match(/^- (.+)$/);
            const olMatch = line.match(/^(\d+)\. (.+)$/);
            if (ulMatch || olMatch) {
                if (currentTable.length > 0) flush();
                if (ulMatch) {
                    currentList.push(`<li class="md-li">${ulMatch[1]}</li>`);
                } else {
                    currentList.push(`<li class="md-li-num"><span class="num">${olMatch[1]}.</span> ${olMatch[2]}</li>`);
                }
                continue;
            }

            if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
                if (currentList.length > 0) flush();
                const content = trimmed.substring(1, trimmed.length - 1);
                const cells = content.split('|').map(c => c.trim());
                if (!cells.every(c => /^[-:]+$/.test(c))) {
                    if (currentTable.length === 0) {
                        currentTable.push('<thead><tr>' + cells.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>');
                    } else {
                        currentTable.push('<tr>' + cells.map(c => `<td>${c}</td>`).join('') + '</tr>');
                    }
                }
                continue;
            }

            flush();
            resultLines.push(line);
        }
        flush();

        html = resultLines.join('\n');
        html = html.replace(/\n/g, '<br>');

        // Restore code blocks
        codeBlocks.forEach((block, i) => {
            html = html.replace(`__CODE_BLOCK_${i}__`, block);
        });

        // Clean up extra <br> tags around block elements
        html = html.replace(/<\/(h[234]|ul|table|hr|pre)><br>/g, '</$1>');
        html = html.replace(/<br><(h[234]|ul|table|hr|pre)/g, '<$1');

        return html;
    } catch (e) {
        console.error('Error in formatContent:', e);
        return escapeHtml(content);
    }
}
