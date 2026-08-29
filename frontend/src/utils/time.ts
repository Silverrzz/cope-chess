const DATE_TIME_PATTERN = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/
const TIMEZONE_PATTERN = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i

const clientTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone

export function timestampDate(value: string): Date {
  const trimmed = value.trim()
  if (!DATE_TIME_PATTERN.test(trimmed)) return new Date(trimmed)

  let normalized = `${trimmed.slice(0, 10)}T${trimmed.slice(11)}`
  if (/[+-]\d{2}$/.test(normalized)) normalized += ':00'
  if (!TIMEZONE_PATTERN.test(normalized)) normalized += 'Z'
  return new Date(normalized)
}

export function localDateFormatter(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(undefined, {
    ...options,
    ...(clientTimeZone ? { timeZone: clientTimeZone } : {}),
  })
}
