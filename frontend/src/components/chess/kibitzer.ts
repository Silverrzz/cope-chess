export interface KibitzerEvaluation {
  gameId: string
  ply: number
  fen: string
  kind: 'cp' | 'mate'
  value: number
  depth: number
  side: 'white' | 'black'
}
