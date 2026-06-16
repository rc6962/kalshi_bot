import perplexicode from '../src/perplexicode';
const sentence = "The sky is blue.";
const cited = perplexicode.formatWithCitations(sentence, [{ type: 'web', index: 'web:42' }]);
console.log(cited);