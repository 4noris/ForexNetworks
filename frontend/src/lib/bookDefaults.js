// Apply the configured A Book on page entry; explicit client links keep their scope.
export function initialBookGroups(groups, books, explicitGroup = "", fallbackGroup = "") {
  const available = new Set(groups);
  if (explicitGroup && available.has(explicitGroup)) return [explicitGroup];
  const aBook = [...new Set(books?.a_book || [])].filter((group) => available.has(group));
  if (aBook.length) return aBook;
  if (fallbackGroup && available.has(fallbackGroup)) return [fallbackGroup];
  return groups.length ? [groups[0]] : [];
}
