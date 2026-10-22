// Render model-generated questions without interpreting their topic or intent.
function buildFollowUpSuggestions(questions) {
  if (!Array.isArray(questions)) return [];
  const seen = new Set();
  return questions.filter(question => {
    if (typeof question !== 'string' || !question.trim()) return false;
    const key = question.trim().toLocaleLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 4).map(question => ({ label: question.trim(), message: question.trim() }));
}

if (typeof module !== 'undefined') module.exports = { buildFollowUpSuggestions };
