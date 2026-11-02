import React from "react";
import { selectOptions } from "./schemaForm.js";

// One datasource type's connection form, built from its JSON Schema (`fieldsOf` in schemaForm.js). Secret fields
// are password inputs with a note to type an ${env:NAME} reference; advanced fields sit behind a disclosure.
const REF_HINT = "Type a reference such as ${env:DB_PASSWORD}; the value stays out of the config file.";

function Field({ field, values, errors, onChange }) {
  const id = `conn-${field.key}`;
  const error = errors[field.key];
  const described = [field.help || field.secret ? `${id}-help` : null, error ? `${id}-err` : null].filter(Boolean).join(" ") || undefined;
  const value = values[field.key];
  let control;
  if (field.kind === "boolean") {
    return (
      <div className="conn-field">
        <label className="check" htmlFor={id}>
          <input id={id} type="checkbox" checked={!!value} onChange={(e) => onChange(field.key, e.target.checked)} />
          <span>{field.label}</span>
        </label>
        {field.help && <p className="conn-help" id={`${id}-help`}>{field.help}</p>}
      </div>
    );
  }
  if (field.kind === "select") {
    control = (
      <select id={id} value={value ?? ""} aria-describedby={described} aria-invalid={error ? true : undefined}
              onChange={(e) => onChange(field.key, e.target.value)}>
        {selectOptions(field).map((o) => <option key={String(o)} value={o}>{o === "" ? "Not set" : String(o)}</option>)}
      </select>
    );
  } else {
    control = (
      <input id={id} type={field.kind === "password" ? "password" : "text"}
             inputMode={field.kind === "number" ? "numeric" : undefined}
             autoComplete={field.kind === "password" ? "new-password" : "off"} spellCheck={false} value={value ?? ""} placeholder={field.example || ""}
             aria-describedby={described} aria-invalid={error ? true : undefined}
             onChange={(e) => onChange(field.key, e.target.value)} />
    );
  }
  return (
    <div className="conn-field">
      <label className="settings-label" htmlFor={id}>
        {field.label}{field.required ? "" : " (optional)"}
      </label>
      {control}
      {(field.help || field.secret) && (
        <p className="conn-help" id={`${id}-help`}>{field.help}{field.secret ? ` ${REF_HINT}` : ""}</p>
      )}
      {error && <p className="conn-error" id={`${id}-err`}>{error}</p>}
    </div>
  );
}

function Fields({ fields, ...rest }) {
  return fields.map((f) =>
    f.kind === "group" ? (
      <fieldset className="conn-group" key={f.key}>
        <legend>{f.label}</legend>
        <Fields fields={f.fields} {...rest} />
      </fieldset>
    ) : (
      <Field key={f.key} field={f} {...rest} />
    ),
  );
}

export default function SchemaForm({ fields, values, errors, onChange }) {
  const main = fields.filter((f) => !f.advanced);
  const advanced = fields.filter((f) => f.advanced);
  return (
    <div className="conn-fields">
      <Fields fields={main} values={values} errors={errors} onChange={onChange} />
      {advanced.length > 0 && (
        <details className="conn-advanced">
          <summary>Advanced</summary>
          <Fields fields={advanced} values={values} errors={errors} onChange={onChange} />
        </details>
      )}
    </div>
  );
}
