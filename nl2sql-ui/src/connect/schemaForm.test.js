// The Connect form is generated from an adapter's connection JSON Schema (pydantic's `model_json_schema()`), so the
// tests use that shape: `$ref` into `$defs`, `anyOf` with null for an optional field, `format: password`, `group`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildConnection, fieldsOf, initialValues, validate } from "./schemaForm.js";

const POOL = {
  type: "object",
  title: "Pool",
  properties: {
    size: { type: "integer", default: 5, title: "Pool size", description: "Connections kept open" },
    timeout_s: { type: "number", default: 30, title: "Wait (s)" },
  },
};

const SCHEMA = {
  $defs: { Pool: POOL },
  type: "object",
  required: ["host", "database", "password"],
  properties: {
    pool: { $ref: "#/$defs/Pool", group: "advanced", title: "Connection pool" },
    host: { type: "string", title: "Host", examples: ["db.internal"], description: "Where the server listens" },
    port: { type: "integer", default: 5432, title: "Port" },
    database: { type: "string", title: "Database" },
    user: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "User" },
    password: { type: "string", format: "password", writeOnly: true, title: "Password" },
    sslmode: { type: "string", enum: ["disable", "prefer", "require"], default: "prefer", group: "advanced", title: "SSL mode" },
    trusted: { type: "boolean", default: false, title: "Trusted connection" },
    schemas: { type: "array", items: { type: "string" }, title: "Schemas" },
  },
};

const byKey = (fields) => Object.fromEntries(fields.map((f) => [f.key, f]));
// A reference to an environment variable, written so no secret scanner takes it for a value.
const REF = "$" + "{env:SALES_DB_PASSWORD}";

test("each JSON Schema type becomes a field kind, in the schema's order", () => {
  const fields = fieldsOf(SCHEMA);
  assert.deepEqual(fields.map((f) => f.key),
    ["pool", "host", "port", "database", "user", "password", "sslmode", "trusted", "schemas"]);
  const f = byKey(fields);
  assert.equal(f.host.kind, "text");
  assert.equal(f.port.kind, "number");
  assert.equal(f.port.integer, true);
  assert.equal(f.trusted.kind, "boolean");
  assert.equal(f.sslmode.kind, "select");
  assert.equal(f.schemas.kind, "list");
  assert.equal(f.pool.kind, "group");
});

test("an optional field (anyOf with null) is its non-null type and not required", () => {
  const user = byKey(fieldsOf(SCHEMA)).user;
  assert.equal(user.kind, "text");
  assert.equal(user.required, false);
});

test("format password is a secret field, and only that", () => {
  const f = byKey(fieldsOf(SCHEMA));
  assert.equal(f.password.kind, "password");
  assert.equal(f.password.secret, true);
  assert.equal(f.host.secret, false);
});

test("an optional secret (anyOf with null) is still a secret", () => {
  const schema = { type: "object", properties: { token: { anyOf: [{ type: "string", format: "password" }, { type: "null" }] } } };
  const token = fieldsOf(schema)[0];
  assert.equal(token.kind, "password");
  assert.equal(token.secret, true);
});

test("required comes from the object's required list; description is help; examples are the placeholder", () => {
  const f = byKey(fieldsOf(SCHEMA));
  assert.equal(f.host.required, true);
  assert.equal(f.port.required, false);
  assert.equal(f.host.help, "Where the server listens");
  assert.equal(f.host.example, "db.internal");
  assert.equal(f.host.label, "Host");
});

test("an enum is a select with its options and default", () => {
  const ssl = byKey(fieldsOf(SCHEMA)).sslmode;
  assert.deepEqual(ssl.options, ["disable", "prefer", "require"]);
  assert.equal(ssl.default, "prefer");
  assert.equal(ssl.advanced, true);
});

test("a $ref group has its resolved children, keyed by their path, and is advanced when the schema says so", () => {
  const pool = byKey(fieldsOf(SCHEMA)).pool;
  assert.equal(pool.advanced, true);
  assert.deepEqual(pool.fields.map((c) => c.key), ["pool.size", "pool.timeout_s"]);
  assert.equal(pool.fields[0].integer, true);
  assert.equal(pool.fields[1].integer, false);
  assert.equal(pool.fields[0].help, "Connections kept open");
});

test("initial values hold each default as the form shows it, and nothing for a field with none", () => {
  const values = initialValues(fieldsOf(SCHEMA));
  assert.equal(values.port, "5432");
  assert.equal(values.sslmode, "prefer");
  assert.equal(values.trusted, false);
  assert.equal(values["pool.size"], "5");
  assert.equal(values.host, "");
  assert.equal(values.user, "");
});

test("required fields that are empty are the errors, by key", () => {
  const fields = fieldsOf(SCHEMA);
  const values = { ...initialValues(fields), host: "  " };
  assert.deepEqual(validate(fields, values), { host: "Required", database: "Required", password: "Required" });
});

test("a number must be a number, and a whole one where the schema says integer", () => {
  const fields = fieldsOf(SCHEMA);
  const base = { ...initialValues(fields), host: "h", database: "d", password: REF };
  assert.deepEqual(validate(fields, base), {});
  assert.deepEqual(validate(fields, { ...base, port: "abc" }), { port: "Enter a number" });
  assert.deepEqual(validate(fields, { ...base, port: "54.5" }), { port: "Enter a whole number" });
  assert.deepEqual(validate(fields, { ...base, "pool.timeout_s": "2.5" }), {});
  assert.deepEqual(validate(fields, { ...base, "pool.size": "x" }), { "pool.size": "Enter a number" });
});

test("the connection leaves out what is empty or still the default, and nests a group that changed", () => {
  const fields = fieldsOf(SCHEMA);
  const values = { ...initialValues(fields), host: " db.internal ", database: "sales", password: REF };
  assert.deepEqual(buildConnection(fields, values), { host: "db.internal", database: "sales", password: REF });
  const changed = {
    ...values, port: "6543", sslmode: "require", trusted: true, user: "ann", "pool.size": "9", schemas: "public, sales",
  };
  assert.deepEqual(buildConnection(fields, changed), {
    host: "db.internal", port: 6543, database: "sales", user: "ann", password: REF,
    sslmode: "require", trusted: true, schemas: ["public", "sales"], pool: { size: 9 },
  });
});

test("a required field is sent even when it equals its default", () => {
  const schema = { type: "object", required: ["port"], properties: { port: { type: "integer", default: 5432 } } };
  const fields = fieldsOf(schema);
  assert.deepEqual(buildConnection(fields, initialValues(fields)), { port: 5432 });
});

test("a schema with no properties is an empty form", () => {
  assert.deepEqual(fieldsOf({ type: "object" }), []);
  assert.deepEqual(fieldsOf(null), []);
  assert.deepEqual(buildConnection([], {}), {});
});
