// עיצוב טקסט בסיסי לתיבות טקסט - עוטף את הטקסט המסומן בסימני מארקדאון קלים
// (**מודגש**, *נטוי*, __קו תחתון__) שהשרת ממיר בהמשך ל-HTML בבטחה.
function wrapSelection(textareaId, marker) {
  const ta = document.getElementById(textareaId);
  if (!ta) return;
  const start = ta.selectionStart;
  const end = ta.selectionEnd;
  const text = ta.value;
  const selected = text.slice(start, end) || 'טקסט';
  const before = text.slice(0, start);
  const after = text.slice(end);
  ta.value = before + marker + selected + marker + after;
  ta.focus();
  ta.selectionStart = start + marker.length;
  ta.selectionEnd = start + marker.length + selected.length;
}
