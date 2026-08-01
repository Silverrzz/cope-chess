import { engineName, statusLabel } from './format'
import type { GameRecord, Identifier, TournamentDetailResponse } from './types'

interface MarkdownColumn<T> {
  heading: string
  align?: 'left' | 'center' | 'right'
  value: (row: T, index: number) => string
}

function tableCell(value: string | number): string {
  return String(value).replaceAll('|', '¦').replaceAll('\n', ' ')
}

function markdownTable<T>(columns: MarkdownColumn<T>[], rows: T[]): string[] {
  const values = rows.map((row, index) => columns.map((column) => tableCell(column.value(row, index))))
  const widths = columns.map((column, columnIndex) => Math.max(
    column.heading.length,
    ...values.map((row) => row[columnIndex]!.length),
  ))
  const padded = (value: string, width: number, align: MarkdownColumn<T>['align']) => {
    if (align === 'right') return value.padStart(width)
    if (align === 'center') {
      const remaining = width - value.length
      const left = Math.floor(remaining / 2)
      return `${' '.repeat(left)}${value}${' '.repeat(remaining - left)}`
    }
    return value.padEnd(width)
  }
  const line = (values: string[]) => `| ${values.map((value, index) => padded(value, widths[index]!, columns[index]!.align)).join(' | ')} |`
  return [
    '```text',
    line(columns.map((column) => column.heading)),
    `|-${widths.map((width) => '-'.repeat(width)).join('-|-')}-|`,
    ...values.map(line),
    '```',
  ]
}

function sameId(left: Identifier, right: Identifier): boolean {
  return String(left) === String(right)
}

function record(games: GameRecord[], engineId: Identifier): string {
  let wins = 0
  let draws = 0
  let losses = 0
  for (const game of games) {
    if (!game.result || (!sameId(game.white_engine_id, engineId) && !sameId(game.black_engine_id, engineId))) continue
    if (game.result === '1/2-1/2') draws += 1
    else if (
      (game.result === '1-0' && sameId(game.white_engine_id, engineId))
      || (game.result === '0-1' && sameId(game.black_engine_id, engineId))
    ) wins += 1
    else losses += 1
  }
  return `${wins}-${draws}-${losses}`
}

function roundedElo(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : Math.round(value).toLocaleString('en-US')
}

function signedElo(value: number): string {
  const rounded = Math.round(value)
  return `${rounded >= 0 ? '+' : ''}${rounded.toLocaleString('en-US')}`
}

function tournamentFormat(data: TournamentDetailResponse): string {
  const value = data.tournament.config?.format
  const format = typeof value === 'string' ? value : value?.value
  return format || ''
}

function setting(data: TournamentDetailResponse, label: string): string {
  const row = (data.settings || []).find((item) => {
    const rowLabel = Array.isArray(item) ? item[0] : item.label
    return String(rowLabel).toLowerCase() === label.toLowerCase()
  })
  if (!row) return ''
  return String(Array.isArray(row) ? row[1] : row.value)
}

export function buildTournamentSummaryMarkdown(data: TournamentDetailResponse, origin: string): string {
  const format = tournamentFormat(data)
  const completedGames = data.games.filter((game) => Boolean(game.result))
  const whiteWins = completedGames.filter((game) => game.result === '1-0').length
  const draws = completedGames.filter((game) => game.result === '1/2-1/2').length
  const blackWins = completedGames.filter((game) => game.result === '0-1').length
  const standings = data.standings || []
  const lines = [
    `# ${data.tournament.name}`,
    '',
    `- **Format:** ${format ? statusLabel(format) : 'Tournament'}`,
  ]
  const timeControl = setting(data, 'Time control')
  if (timeControl) lines.push(`- **Time control:** ${timeControl}`)
  lines.push(
    `- **Games:** ${completedGames.length}`,
    `- **Results:** ${whiteWins} White wins, ${draws} draws, ${blackWins} Black wins`,
  )
  if (data.tournament.finished_at) lines.push(`- **Finished:** ${data.tournament.finished_at.slice(0, 10)}`)

  lines.push('', '## Standings', '')
  const standingColumns: MarkdownColumn<(typeof standings)[number]>[] = [
    { heading: '#', align: 'right', value: (_row, index) => String(index + 1) },
    { heading: 'Engine', value: (row) => row.name },
    { heading: 'Score', align: 'right', value: (row) => String(row.points) },
    { heading: 'Played', align: 'right', value: (row) => String(row.played) },
    { heading: 'W-D-L', align: 'center', value: (row) => record(data.games, row.engine_id) },
  ]
  if (format === 'swiss') {
    standingColumns.push({ heading: 'Buchholz', align: 'right', value: (row) => String(row.buchholz ?? 0) })
  }
  if (standings.some((row) => Boolean(row.bye_points))) {
    standingColumns.push({ heading: 'Bye points', align: 'right', value: (row) => String(row.bye_points ?? 0) })
  }
  if (format === 'knockout') {
    standingColumns.push({ heading: 'Stage', align: 'right', value: (row) => String(row.stage ?? 0) })
  }
  lines.push(...markdownTable(standingColumns, standings))

  for (const ratingSummary of data.rating_summaries || []) {
    const rank = new Map(standings.map((row, index) => [String(row.engine_id), index]))
    const rows = [...ratingSummary.rows].sort(
      (left, right) => (rank.get(String(left.engine_id)) ?? Number.MAX_SAFE_INTEGER)
        - (rank.get(String(right.engine_id)) ?? Number.MAX_SAFE_INTEGER),
    )
    lines.push(
      '',
      `## Elo - ${ratingSummary.rating_list_name}`,
      '',
      `**Average competitor Elo:** ${roundedElo(ratingSummary.average_competitor_elo)}`,
      '',
      ...markdownTable(
        [
          { heading: 'Engine', value: (row) => engineName(data.engines, row.engine_id) },
          { heading: 'Score', align: 'right', value: (row) => `${row.score}/${row.games}` },
          { heading: 'Performance', align: 'right', value: (row) => roundedElo(row.performance_elo) },
          { heading: 'Avg. opponent', align: 'right', value: (row) => roundedElo(row.average_opponent_elo) },
          { heading: 'Elo before', align: 'right', value: (row) => roundedElo(row.elo_before) },
          { heading: 'Elo after', align: 'right', value: (row) => roundedElo(row.elo_after) },
          { heading: 'Change', align: 'right', value: (row) => signedElo(row.elo_change) },
        ],
        rows,
      ),
    )
  }

  lines.push('', `[View tournament](${origin}/tournaments/${encodeURIComponent(String(data.tournament.id))})`)
  return lines.join('\n')
}
