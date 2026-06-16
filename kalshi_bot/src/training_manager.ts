import * as path from 'path';
import * as fs from 'fs';
import * as child_process from 'child_process';
import * as vscode from 'vscode';

type TrainParams = {
  datasetPath: string;
  model: 'lightgbm' | 'xgboost';
  outputDir?: string;
  targetCol?: string;
  features?: string[];
};

export class TrainingManager {
  private defaultOutputDir(root: string) {
    return path.join(root, 'kalshi_bot_training');
  }

  public async runTraining(datasetPath: string, model: TrainParams['model'] = 'lightgbm') {
    const root = path.dirname(datasetPath);
    const outputDir = this.defaultOutputDir(root);
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    const params: TrainParams = {
      datasetPath,
      model,
      outputDir,
      targetCol: 'target',
      features: undefined
    };

    vscode.window.showInformationMessage(`Starting ${model} training on ${datasetPath}`);
    await this.executeTraining(params);
  }

  private async executeTraining(params: TrainParams) {
    // Lightweight example: call a Python script in the workspace to train
    // This requires a small helper script named train_model.py in the root (or adapt as needed)
    const pyScript = path.join(path.dirname(params.datasetPath), 'train_model.py');
    if (!fs.existsSync(pyScript)) {
      vscode.window.showErrorMessage(`Training script not found: ${pyScript}`);
      return;
    }

    const cmd = [
      'python', pyScript,
      '--dataset', params.datasetPath,
      '--model', params.model,
      '--output', params.outputDir || this.defaultOutputDir(path.dirname(params.datasetPath))
    ];

    const proc = child_process.spawn(cmd[0], cmd.slice(1), { stdio: 'inherit' });
    await new Promise<void>((resolve, reject) => {
      proc.on('close', (code) => {
        if (code === 0) {
          vscode.window.showInformationMessage('Training completed');
          resolve();
        } else {
          vscode.window.showErrorMessage(`Training failed with code ${code}`);
          reject();
        }
      });
    });
  }
}