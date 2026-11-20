// Remove only exact, repeated single-line version notes across entry files.
// Keep role sections, lists, code and differently worded rules intact.
export function composeGuidance(documents) {
  const previous = new Set();
  return documents.map(document => {
    const added = new Set();
    let fenced = false;
    const paragraphs = document.replaceAll('\r\n', '\n').split('\n\n');
    const kept = paragraphs.filter(paragraph => {
      const eligible = !fenced && !paragraph.includes('\n') && paragraph.length >= 300
        && /^\d+\.\d+\.\d+ /.test(paragraph);
      for (const line of paragraph.split('\n')) if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
      if (!eligible) return true;
      added.add(paragraph);
      return !previous.has(paragraph);
    });
    for (const paragraph of added) previous.add(paragraph);
    return kept.join('\n\n');
  }).join('\n\n');
}
