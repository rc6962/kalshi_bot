import * as vscode from 'vscode';
import { TrainingManager } from './training_manager';
import { DataDiscovery } from './data_discovery';

export function activate(context: vscode.ExtensionContext) {
  const trainingManager = new TrainingManager();
  const dataDiscovery = new DataDiscovery();

  const discoverCmd = vscode.commands.registerCommand('kalshiBot.discoverDatasets', async () => {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders) {
      vscode.window.showWarningMessage('No workspace folder opened.');
      return;
    }
    const root = workspaceFolders[0].uri.fsPath;
    const datasets = await dataDiscovery.findTradesCsv(root);
    vscode.window.showQuickPick(datasets.map(d => d.path), { placeHolder: 'Select dataset' })
      .then(selected => {
        if (selected) {
          vscode.window.showInformationMessage(`Selected dataset: ${selected}`);
        }
      });
  });

  const runCmd = vscode.commands.registerCommand('kalshiBot.runTraining', async () => {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders) {
      vscode.window.showWarningMessage('No workspace folder opened.');
      return;
    }
    // Simple example: run training on the first trades.csv found
    const root = workspaceFolders[0].uri.fsPath;
    const datasets = await dataDiscovery.findTradesCsv(root);
    if (datasets.length === 0) {
      vscode.window.showErrorMessage('No trades.csv found in workspace.');
      return;
    }
    const datasetPath = datasets[0].path;
    await trainingManager.runTraining(datasetPath);
  });

  context.subscriptions.push(discoverCmd, runCmd);
  vscode.window.showInformationMessage('Perplexicode Training extension activated');
}

export function deactivate() {}