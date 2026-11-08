import { fieldActive, contentKinds, type FormAnswers, type FormErrors, type FormField } from '@/types/forms'

function missing(field: FormField, value: unknown): boolean {
  if (field.kind === 'consent') return value !== true
  if (value === undefined || value === null) return true
  if (typeof value === 'string') return !value.trim()
  if (Array.isArray(value)) {
    if (field.kind === 'ranking') return value.length !== field.choices?.length || value.some(item => !item)
    return value.length < Math.max(1, field.min_items ?? 0)
  }
  if (field.kind === 'matrix') return typeof value !== 'object' || (field.rows ?? []).some(row => !Object.hasOwn(value, row.id))
  return false
}

export function requiredProgress(fields: FormField[], answers: FormAnswers) {
  const errors: FormErrors = {}
  let total = 0
  function visit(blocks: FormField[], values: FormAnswers, path: string[]): void {
    for (const field of blocks) {
      if (contentKinds.has(field.kind) || !fieldActive(field, values)) continue
      const value = values[field.id]
      const position = [...path, field.id]
      if (field.required) {
        total++
        if (missing(field, value)) errors[position.join('.')] = [
          field.kind === 'consent' ? 'Confirm that you agree to continue.' : `Answer “${field.label}”.`,
        ]
      }
      if (field.kind === 'group' && Array.isArray(value)) {
        for (const row of value as FormAnswers[]) visit(field.fields ?? [], row, [...position, String(row._row_id)])
      }
    }
  }
  visit(fields, answers, [])
  return { total, answered: total - Object.keys(errors).length, errors }
}
