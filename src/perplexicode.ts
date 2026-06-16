// Perplexicode core entry for VS Code integration
// This module exposes a single API to format and emit Perplexicode blocks.
// It is intentionally minimal and focused on the editor integration path.

type Citation = {
  type: 'web' | 'code_file' | 'generated_image' | string;
  index: string;
  text?: string;
};

// Basic emitter that formats a block with inline citations after sentences
export function formatWithCitations(text: string, citations: Citation[]) {
  // naive approach: append [type:index] after the sentence containing the fact
  // In a real setup, you'd parse sentences; here we provide a simple placeholder.
  let result = text;
  for (const c of citations) {
    const marker = `[${c.type}:${c.index}]`;
    // Append marker if not already present
    if (!result.includes(marker)) {
      result += ` ${marker}`;
    }
  }
  return result;
}

// Example helper to create a citation reference string for a sentence
export function citeSentence(sentence: string, citation: Citation) {
  return `${sentence} [${citation.type}:${citation.index}]`;
}

// Default export for convenience
export default {
  formatWithCitations,
  citeSentence,
};