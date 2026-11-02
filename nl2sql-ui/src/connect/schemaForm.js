// The Connect form, generated from an adapter's connection JSON Schema (`DatasourceType.connection_schema`, which is
// pydantic's `model_json_schema()`). Pure: the component draws what `fieldsOf` returns and keeps what the person typed
// in one flat map, `values`, keyed by the field's path ("pool.size").
//
// Field kinds: text, password (`format: password`, drawn hidden), number, boolean, select (an `enum`), list (an array
// of strings, typed comma-separated) and group (an object with its own fields). An optional field is `anyOf` with a
// null; `$ref` resolves into `$defs`; `group: "advanced"` (the adapters' own marker) puts a field behind a disclosure.

const refTarget = (root, ref) =>
  String(ref).replace(/^#\//, "").split("/").reduce((node, part) => (node ? node[part] : undefined), root);

// The property as the form sees it: its `$ref` followed and its `anyOf: [T, null]` reduced to T, with the property's
// own title, description, default and group winning over the definition's.
function resolve(root, prop) {
  let node = prop || {};
  for (let depth = 0; depth < 8; depth += 1) {
    if (node.$ref) {
      const { $ref, ...rest } = node;
      node = { ...(refTarget(root, $ref) || {}), ...rest };
    } else if (Array.isArray(node.anyOf)) {
      const real = node.anyOf.filter((alt) => alt && alt.type !== "null");
      if (real.length !== 1) break;
      const { anyOf, ...rest } = node;
      node = { ...real[0], ...rest };
    } else {
      break;
    }
  }
  return node;
}

function kindOf(node) {
  if (Array.isArray(node.enum)) return "select";
  if (node.type === "boolean") return "boolean";
  if (node.type === "integer" || node.type === "number") return "number";
  if (node.type === "array") return "list";
  if (node.type === "object" && node.properties) return "group";
  if (node.format === "password") return "password";
  return "text";
}

function build(root, schema, parent) {
  const required = new Set((schema && schema.required) || []);
  return Object.entries((schema && schema.properties) || {}).map(([name, prop]) => {
    const node = resolve(root, prop);
    const key = parent ? `${parent}.${name}` : name;
    const kind = kindOf(node);
    return {
      key,
      name,
      kind,
      label: node.title || name,
      help: node.description || "",
      example: Array.isArray(node.examples) && node.examples.length ? String(node.examples[0]) : "",
      required: required.has(name),
      default: node.default === undefined ? null : node.default,
      secret: node.format === "password",
      advanced: node.group === "advanced",
      options: Array.isArray(node.enum) ? node.enum : [],
      integer: node.type === "integer",
      fields: kind === "group" ? build(root, node, key) : [],
    };
  });
}

export function fieldsOf(schema) {
  return schema ? build(schema, schema, "") : [];
}

const leaves = (fields) => fields.flatMap((f) => (f.kind === "group" ? leaves(f.fields) : [f]));

// What the form starts with: each default as the form shows it (a number as text), a boolean as a boolean.
export function initialValues(fields) {
  const values = {};
  for (const f of leaves(fields)) {
    if (f.kind === "boolean") values[f.key] = f.default === true;
    else values[f.key] = f.default === null ? "" : String(f.default);
  }
  return values;
}

const text = (v) => (typeof v === "string" ? v.trim() : "");

// The problems the person can fix, by field key. A required field is empty when it holds only spaces.
export function validate(fields, values) {
  const problems = {};
  for (const f of leaves(fields)) {
    if (f.kind === "boolean") continue;
    const raw = text(values[f.key]);
    if (!raw) {
      if (f.required) problems[f.key] = "Required";
      continue;
    }
    if (f.kind === "number") {
      const n = Number(raw);
      if (!Number.isFinite(n)) problems[f.key] = "Enter a number";
      else if (f.integer && !Number.isInteger(n)) problems[f.key] = "Enter a whole number";
    }
  }
  return problems;
}

// The `connection` block to send. A field left empty, or still at its default and not required, is left out so the
// config stays as short as the person made it; a group is sent only when something in it is.
export function buildConnection(fields, values) {
  const out = {};
  for (const f of fields) {
    if (f.kind === "group") {
      const inner = buildConnection(f.fields, values);
      if (Object.keys(inner).length) out[f.name] = inner;
      continue;
    }
    const raw = values[f.key];
    if (f.kind === "boolean") {
      if (f.required || raw !== (f.default === true)) out[f.name] = Boolean(raw);
      continue;
    }
    const t = text(raw);
    if (!t) continue;
    if (f.kind === "list") {
      const items = t.split(",").map((s) => s.trim()).filter(Boolean);
      if (items.length) out[f.name] = items;
    } else if (f.kind === "number") {
      const n = Number(t);
      if (f.required || n !== f.default) out[f.name] = n;
    } else if (f.required || t !== (f.default === null ? "" : String(f.default))) {
      out[f.name] = t;
    }
  }
  return out;
}
