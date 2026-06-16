export declare function formatWithCitations(text: string, citations: { type: string; index: string; text?: string }[]): string;
export declare function citeSentence(sentence: string, citation: { type: string; index: string; text?: string }): string;
export default { formatWithCitations, citeSentence };