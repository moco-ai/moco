import * as vscode from 'vscode';
import * as cp from 'child_process';

export function activate(context: vscode.ExtensionContext) {
    const outputChannel = vscode.window.createOutputChannel('moco');
    let statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = 'moco.run';
    updateStatusBar();
    statusBarItem.show();

    function updateStatusBar() {
        const config = vscode.workspace.getConfiguration('moco');
        const profile = config.get<string>('profile') || 'default';
        statusBarItem.text = `$(hubot) moco: ${profile}`;
        statusBarItem.tooltip = `moco agent active (Profile: ${profile})`;
    }

    vscode.workspace.onDidChangeConfiguration(e => {
        if (e.affectsConfiguration('moco.profile')) {
            updateStatusBar();
        }
    });

    // Register Chat View Provider
    const provider = new MocoChatViewProvider(context.extensionUri);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(MocoChatViewProvider.viewType, provider)
    );

    function escapeShellArg(arg: string): string {
        return `'${arg.replace(/'/g, "'\\''")}'`;
    }

    const runMocoCommand = async (args: string[], onData?: (data: string) => void) => {
        const config = vscode.workspace.getConfiguration('moco');
        let providerName = config.get<string>('provider');
        let profile = config.get<string>('profile');

        let cmdArgs = [...args];
        if (providerName && providerName.trim() !== '' && providerName !== 'auto') {
            cmdArgs.push('--provider', providerName);
        }
        if (profile && profile.trim() !== '') {
            cmdArgs.push('--profile', profile);
        }

        const fullCommand = `moco ${cmdArgs.map(escapeShellArg).join(' ')}`;
        outputChannel.appendLine(`Executing: ${fullCommand}`);

        // If onData is provided, we use spawn to capture output
        if (onData) {
            // Note: chat mode is better for continuous interaction.
            // But VS Code extension usually triggers one-shot tasks via the UI.
            // If the user wants a persistent chat session, we should use 'chat' command.
            const isChat = args.includes('chat');
            const child = cp.spawn('moco', cmdArgs, {
                shell: true,
                cwd: vscode.workspace.workspaceFolders?.[0].uri.fsPath
            });

            child.stdout.on('data', (data) => {
                const text = data.toString();
                outputChannel.append(text);
                onData(text);
            });

            child.stderr.on('data', (data) => {
                const text = data.toString();
                outputChannel.append(text);
                onData(`Error: ${text}`);
            });

            child.on('close', (code) => {
                outputChannel.appendLine(`Process exited with code ${code}`);
                onData(`\n[Process finished with code ${code}]`);
            });
        } else {
            let terminal = vscode.window.terminals.find(t => t.name === 'moco run');
            if (!terminal) {
                terminal = vscode.window.createTerminal('moco run');
            }
            terminal.show();
            terminal.sendText(fullCommand);
        }
    };

    let runCmd = vscode.commands.registerCommand('moco.run', async () => {
        const query = await vscode.window.showInputBox({ 
            prompt: 'Enter task for moco',
            placeHolder: 'e.g. Explain this code'
        });
        if (query) {
            runMocoCommand(['run', query]);
        }
    });

    let runWithQueryCmd = vscode.commands.registerCommand('moco.runWithQuery', async (query: string) => {
        // provider.addMessage(`Running: ${query}`, 'moco'); // Remove redundant "Running" message
        runMocoCommand(['chat', '--new', query], (data) => {
            provider.addMessage(data, 'moco');
        });
    });

    let tasksRunCmd = vscode.commands.registerCommand('moco.tasksRun', async (uri: vscode.Uri) => {
        if (!uri && vscode.window.activeTextEditor) {
            uri = vscode.window.activeTextEditor.document.uri;
        }
        if (uri && uri.scheme === 'file') {
            runMocoCommand(['run', '--file', uri.fsPath]);
        } else {
            vscode.window.showErrorMessage('moco tasks run: Only local files are supported.');
        }
    });

    context.subscriptions.push(runCmd, runWithQueryCmd, tasksRunCmd, statusBarItem, outputChannel);
}

class MocoChatViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'moco.chatView';
    private _view?: vscode.WebviewView;

    constructor(private readonly _extensionUri: vscode.Uri) {}

    public resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken,
    ) {
        this._view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this._extensionUri]
        };

        webviewView.webview.html = this._getHtmlForWebview(webviewView.webview);

        webviewView.webview.onDidReceiveMessage(async data => {
            switch (data.type) {
                case 'sendMessage':
                    vscode.commands.executeCommand('moco.runWithQuery', data.value);
                    break;
                case 'updateConfig':
                    const config = vscode.workspace.getConfiguration('moco');
                    if (data.key === 'profile') {
                        await config.update('profile', data.value, vscode.ConfigurationTarget.Global);
                    } else if (data.key === 'provider') {
                        await config.update('provider', data.value, vscode.ConfigurationTarget.Global);
                    }
                    break;
                case 'requestProfiles':
                    cp.exec('moco list-profiles', (err, stdout) => {
                        if (!err) {
                            const profiles = stdout.split('\n')
                                .filter(line => line.trim().startsWith('-'))
                                .map(line => line.trim().replace('- ', ''));
                            webviewView.webview.postMessage({ type: 'setProfiles', value: profiles });
                        }
                    });
                    break;
            }
        });
    }

    public addMessage(text: string, sender: 'user' | 'moco') {
        if (this._view) {
            this._view.webview.postMessage({ type: 'addMessage', text, sender });
        }
    }

    private _getHtmlForWebview(webview: vscode.Webview) {
        const config = vscode.workspace.getConfiguration('moco');
        const currentProfile = config.get<string>('profile') || 'default';
        const currentProvider = config.get<string>('provider') || 'auto';

        return `<!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body { font-family: var(--vscode-font-family); padding: 0; display: flex; flex-direction: column; height: 100%; margin: 0; box-sizing: border-box; background-color: var(--vscode-sideBar-background); color: var(--vscode-sideBar-foreground); overflow: hidden; }
                    .config-panel { padding: 8px; border-bottom: 1px solid var(--vscode-panel-border); display: flex; flex-direction: column; gap: 6px; font-size: 11px; flex-shrink: 0; background: var(--vscode-sideBar-background); position: sticky; top: 0; z-index: 10; }
                    .config-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
                    .config-row label { font-weight: bold; opacity: 0.8; }
                    select { flex: 1; background: var(--vscode-select-background); color: var(--vscode-select-foreground); border: 1px solid var(--vscode-select-border); border-radius: 4px; padding: 2px 4px; outline: none; }
                    #chat-history { flex: 1; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
                    .message { padding: 8px 12px; border-radius: 6px; max-width: 92%; word-wrap: break-word; font-size: 13px; line-height: 1.4; }
                    .user { background: var(--vscode-button-background); color: var(--vscode-button-foreground); align-self: flex-end; }
                    .moco { background: var(--vscode-editor-inactiveSelectionBackground); color: var(--vscode-editor-foreground); align-self: flex-start; white-space: pre-wrap; font-family: var(--vscode-editor-font-family); }
                    .input-container { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--vscode-panel-border); background: var(--vscode-sideBar-background); }
                    input { flex: 1; background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border); padding: 6px 10px; border-radius: 4px; outline: none; }
                    input:focus { border-color: var(--vscode-focusBorder); }
                    button { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; padding: 6px 12px; cursor: pointer; border-radius: 4px; font-weight: bold; }
                    button:hover { background: var(--vscode-button-hoverBackground); }
                </style>
            </head>
            <body>
                <div class="config-panel">
                    <div class="config-row">
                        <label>Profile:</label>
                        <select id="profile-select">
                            <option value="${currentProfile}">${currentProfile}</option>
                        </select>
                    </div>
                    <div class="config-row">
                        <label>Provider:</label>
                        <select id="provider-select">
                            <option value="auto" ${currentProvider === 'auto' ? 'selected' : ''}>Auto</option>
                            <option value="gemini" ${currentProvider === 'gemini' ? 'selected' : ''}>Gemini</option>
                            <option value="openai" ${currentProvider === 'openai' ? 'selected' : ''}>OpenAI</option>
                            <option value="openrouter" ${currentProvider === 'openrouter' ? 'selected' : ''}>OpenRouter</option>
                            <option value="zai" ${currentProvider === 'zai' ? 'selected' : ''}>Zai (DMM)</option>
                        </select>
                    </div>
                </div>
                <div id="chat-history"></div>
                <div class="input-container">
                    <input type="text" id="chat-input" placeholder="Ask moco...">
                    <button id="send-button">Send</button>
                </div>
                <script>
                    const vscode = acquireVsCodeApi();
                    const chatHistory = document.getElementById('chat-history');
                    const chatInput = document.getElementById('chat-input');
                    const sendButton = document.getElementById('send-button');
                    const profileSelect = document.getElementById('profile-select');
                    const providerSelect = document.getElementById('provider-select');

                    // Request profiles on load
                    vscode.postMessage({ type: 'requestProfiles' });

                    function addMessage(text, sender) {
                        const div = document.createElement('div');
                        div.className = 'message ' + sender;
                        div.textContent = text;
                        chatHistory.appendChild(div);
                        chatHistory.scrollTop = chatHistory.scrollHeight;
                    }

                    window.addEventListener('message', event => {
                        const message = event.data;
                        switch (message.type) {
                            case 'addMessage':
                                addMessage(message.text, message.sender);
                                break;
                            case 'setProfiles':
                                const current = profileSelect.value;
                                profileSelect.innerHTML = '';
                                message.value.forEach(p => {
                                    const opt = document.createElement('option');
                                    opt.value = p;
                                    opt.textContent = p;
                                    if (p === current) opt.selected = true;
                                    profileSelect.appendChild(opt);
                                });
                                break;
                        }
                    });

                    profileSelect.addEventListener('change', () => {
                        vscode.postMessage({ type: 'updateConfig', key: 'profile', value: profileSelect.value });
                    });

                    providerSelect.addEventListener('change', () => {
                        vscode.postMessage({ type: 'updateConfig', key: 'provider', value: providerSelect.value });
                    });

                    sendButton.addEventListener('click', () => {
                        const value = chatInput.value;
                        if (value) {
                            addMessage(value, 'user');
                            vscode.postMessage({ type: 'sendMessage', value: value });
                            chatInput.value = '';
                        }
                    });

                    chatInput.addEventListener('keypress', (e) => {
                        if (e.key === 'Enter') {
                            sendButton.click();
                        }
                    });
                </script>
            </body>
            </html>`;
    }

}

export function deactivate() {}
