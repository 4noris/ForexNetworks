const DAY = 86400000;
export function utcDaysBefore(day, count) {
  return new Date(Date.parse(`${day}T00:00:00Z`) - count * DAY).toISOString().slice(0, 10);
}

export function boundedUtcRange(from, to, boundary, value, today) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value)))
    return boundary === "from" ? [value, to] : [from, value];
  const selected = value > today ? today : value;
  if (boundary === "from") {
    const end = !to || to < selected ? selected : to;
    return [selected, end > utcDaysBefore(selected, -6) ? utcDaysBefore(selected, -6) : end];
  }
  const start = !from || from > selected ? selected : from;
  return [start < utcDaysBefore(selected, 6) ? utcDaysBefore(selected, 6) : start, selected];
}
