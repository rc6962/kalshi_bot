// Perplexicode example entrypoint
import perplexicode from './perplexicode';

const text = "The Eiffel Tower is in Paris.";
const citations = [
  { type: 'web', index: 'web:1', text: 'Paris location' }
];

// Demonstration of how to attach citations to a block
const output = perplexicode.formatWithCitations(text, citations);
console.log(output);