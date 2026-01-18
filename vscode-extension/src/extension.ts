import * as vscode from 'vscode';

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

    /**
     * Escape arguments for shell execution to prevent command injection.
     */
    function escapeShellArg(arg: string): string {
        return `'${arg.replace(/'/g, "'\\''")}'`;
    }

    const runMocoCommand = async (args: string[]) => {
        const config = vscode.workspace.getConfiguration('moco');
        const provider = config.get<string>('provider');
        const profile = config.get<string>('profile');

        let cmdArgs = [...args];
        if (provider && provider.trim() !== '') {
            cmdArgs.push('--provider', provider);
        }
        if (profile && profile.trim() !== '') {
            cmdArgs.push('--profile', profile);
        }

        // Use shell-escaped arguments
        const escapedCommand = `moco ${cmdArgs.map(escapeShellArg).join(' ')}`;
        
        outputChannel.appendLine(`Executing: ${escapedCommand}`);
        outputChannel.show();

        // Reuse terminal if it exists
        let terminal = vscode.window.terminals.find(t => t.name === 'moco run');
        if (!terminal) {
            terminal = vscode.window.createTerminal('moco run');
        }
        
        terminal.show();
        terminal.sendText(escapedCommand);
    };

    let runCmd = vscode.commands.registerCommand('moco.run', async () => {
        const query = await vscode.window.showInputBox({ 
            prompt: 'Enter task for moco',
            placeHolder: 'e.g. Explain this code'
        });
        if (query) {
            runMocoCommand([query]);
        }
    });

    let tasksRunCmd = vscode.commands.registerCommand('moco.tasksRun', async (uri: vscode.Uri) => {
        // Fallback to active editor if context menu wasn't used
        if (!uri && vscode.window.activeTextEditor) {
            uri = vscode.window.activeTextEditor.document.uri;
        }
        
        if (uri && uri.scheme === 'file') {
            runMocoCommand(['task', 'run', uri.fsPath]);
        } else {
            vscode.window.showErrorMessage('moco tasks run: Only local files are supported.');
        }
    });

    context.subscriptions.push(runCmd, tasksRunCmd, statusBarItem, outputChannel);
}

export function deactivate() {}
