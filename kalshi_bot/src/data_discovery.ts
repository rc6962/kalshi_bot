import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';

export interface DatasetEntry {
  path: string;
  mtime: number;
}

export class DataDiscovery {
  async findTradesCsv(root: string): Promise<DatasetEntry[]> {
    const results: DatasetEntry[] = [];
    const stack: string[] = [root];

    while (stack.length) {
      const current = stack.pop()!;
      let stat;
      try {
        stat = fs.statSync(current);
      } catch {
        continue;
      }

      if (stat.isDirectory()) {
        const entries = fs.readdirSync(current);
        for (const e of entries) {
          stack.push(path.join(current, e));
        }
      } else if (stat.isFile() && path.basename(current).toLowerCase() === 'trades.csv') {
        results.push({ path: current, mtime: stat.mtimeMs });
      }
    }

    // sort by path for deterministic order
    results.sort((a, b) => a.path.localeCompare(b.path));
    return results;
  }
}