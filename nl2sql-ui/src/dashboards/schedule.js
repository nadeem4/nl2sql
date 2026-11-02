// A dashboard's schedule: how often, and what to say about it. The server holds the minimum
// (`dashboards.min_refresh_minutes`); a value under it comes back as ScheduleTooFrequent (422) with its own sentence.
export function everyLabel(minutes) {
  if (minutes === 1440) return "every day";
  if (minutes === 60) return "every hour";
  if (minutes > 60 && minutes % 60 === 0) return `every ${minutes / 60} h`;
  return `every ${minutes} min`;
}

export function parseMinutes(text) {
  const trimmed = String(text).trim();
  return /^\d+$/.test(trimmed) && Number(trimmed) >= 1 ? Number(trimmed) : null;
}

export function scheduleNote(schedule) {
  if (!schedule) return "No schedule.";
  if (schedule.state === "stopped") {
    const why = schedule.error && schedule.error.message;
    return why ? `Stopped: ${why}` : "Stopped.";
  }
  return `Refreshes ${everyLabel(schedule.every_minutes)}.`;
}
