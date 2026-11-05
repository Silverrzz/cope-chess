import type { HandBrainPayload } from './types'

export function handBrainStandings(payload: HandBrainPayload) {
  return payload.teams.map(team => {
    const played = payload.matches.filter(m => m.team_ids.includes(team.id) && m.status === 'completed')
    const won = played.filter(m => m.winner_team_id === team.id).length
    const lost = played.length - won
    let points = 0, games = 0
    for (const fixture of payload.matches) {
      const side = fixture.team_ids.indexOf(team.id)
      if (side < 0) continue
      for (const quad of fixture.quads) { points += quad.score[side] ?? 0; games += quad.games.filter(g => g.status === 'finished').length }
    }
    const champion = payload.matches.find(m => m.key === 'F')?.winner_team_id === team.id
    const latest = [...payload.matches].reverse().find(m => m.team_ids.includes(team.id))
    const progress = { 'Play-in': 0, Quarterfinal: 1, Semifinal: 2, Final: 3 }[latest?.stage ?? 'Quarterfinal'] ?? 0
    return { ...team, won, lost, points, games, place: champion ? 1 : latest?.stage === 'Final' ? 2 : latest?.stage === 'Semifinal' ? 3 : latest?.stage === 'Quarterfinal' ? 5 : 9, stage: champion ? 'Champion' : lost ? `Out · ${latest?.stage ?? 'Play-in'}` : latest?.stage ?? 'Quarterfinal bye', order: champion ? 100 : progress * 10 + (lost ? 0 : 5) }
  }).sort((a,b) => b.order-a.order || a.seed-b.seed)
}
