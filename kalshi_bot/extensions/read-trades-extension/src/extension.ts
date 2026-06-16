import * as vscode from 'vscode';
import { TextDecoder } from 'util';
import * as path from 'path';

export function activate(context: vscode.ExtensionContext) {
  const disposable = vscode.commands.registerCommand('read-trades.readTrades', async () => {
    try {
      const files = await vscode.workspace.findFiles('**/trades.csv', '**/node_modules/**', 100);
      if (files.length === 0) {
        vscode.window.showInformationMessage('No trades.csv files found in the current workspace.');
        return;
      }

      const summaries: string[] = [];
      for (const uri of files) {
        const bytes = await vscode.workspace.fs.readFile(uri);
        const text = new TextDecoder().decode(bytes);
        const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
        // Very lightweight summary: count rows and first line as header
        const header = lines[0] ?? '';
        const rowCount = lines.length - 1;
        summaries.push(`${path.relative(vscode.workspace.rootPath || '', uri.fsPath)}: ${rowCount} rows, header="${header}"`);
      }

      const output = summaries.join('\n');
      const channel = vscode.window.createOutputChannel('ReadTrades');
      channel.appendLine(output);
      channel.show(true);
    } catch (err) {
      vscode.window.showErrorMessage('Error reading trades.csv: ' + String(err));
    }
  });

  context.subscriptions.push(disposable);
}

export function deactivate() {}