interface RatedEngineRow {
  engine: { name: string }
  elo?: number | null
}

export function bestVersionRatings<T extends RatedEngineRow>(rows: readonly T[]): T[] {
  const bestByEngine = new Map<string, T>()

  for (const row of rows) {
    const current = bestByEngine.get(row.engine.name)
    if (!current || (row.elo ?? Number.NEGATIVE_INFINITY) > (current.elo ?? Number.NEGATIVE_INFINITY)) {
      bestByEngine.set(row.engine.name, row)
    }
  }

  return rows.filter((row) => bestByEngine.get(row.engine.name) === row)
}
