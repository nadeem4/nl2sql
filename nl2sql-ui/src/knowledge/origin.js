// Where a piece of knowledge came from. The engine keys `origins` per field (`tables.Invoice.description`,
// `columns.Invoice.Total.role`), never per item, so an item's origin is its description's, else its first field's.
export function originOf(origins, kind, name) {
  if (!origins) return null;
  const own = `${kind}.${name}.`;
  if (origins[`${own}description`]) return origins[`${own}description`];
  const key = Object.keys(origins).find((k) => k.startsWith(own));
  return key ? origins[key] : null;
}
