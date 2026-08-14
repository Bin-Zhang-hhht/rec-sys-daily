const MIN_REPEATED_HALF_LENGTH = 80;

function dedupeRepeatedHalves(value: string): string {
  const text = value.trim();
  const midpoint = Math.floor(text.length / 2);

  if (text.length % 2 === 0 && midpoint >= MIN_REPEATED_HALF_LENGTH) {
    const left = text.slice(0, midpoint);
    if (left === text.slice(midpoint)) return left;
  }

  let dividerStart = midpoint;
  let dividerEnd = midpoint;
  while (dividerStart > 0 && /\s/.test(text[dividerStart - 1]!)) dividerStart -= 1;
  while (dividerEnd < text.length && /\s/.test(text[dividerEnd]!)) dividerEnd += 1;

  if (
    dividerStart >= MIN_REPEATED_HALF_LENGTH
    && dividerEnd > dividerStart
    && dividerStart === text.length - dividerEnd
    && text.slice(0, dividerStart) === text.slice(dividerEnd)
  ) {
    return text.slice(0, dividerStart);
  }

  return text;
}

export function formatAcademicText(value: string): string {
  return dedupeRepeatedHalves(value).replace(/\\([%&_#])/g, "$1");
}
