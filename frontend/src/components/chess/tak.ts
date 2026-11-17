export const TAK_SIZE = 6
export const TAK_START = 'x6/x6/x6/x6/x6/x6 1 1'

export interface TakPosition {
  stacks: string[][]
  turn: 'w' | 'b'
  fullmove: number
}

const directions: Record<string, [number, number]> = { '>': [1, 0], '<': [-1, 0], '+': [0, -1], '-': [0, 1] }
const placement = /^([FSC]?)([a-f][1-6])$/
const spread = /^([1-6]?)([a-f][1-6])([<>+-])([1-6]*)[SC]?\*?$/

export function parseTakPosition(tps?: string | null): TakPosition {
  const [board, side, number] = (!tps || tps === 'startpos' ? TAK_START : tps).split(/\s+/)
  const stacks = board?.split('/').flatMap((row) => row.split(',').flatMap((cell) => cell.startsWith('x')
    ? Array.from({ length: Number(cell.slice(1) || 1) }, () => [] as string[])
    : [cell.match(/[12][SC]?/g) ?? []])) ?? []
  return { stacks, turn: side === '2' ? 'b' : 'w', fullmove: Number(number) || 1 }
}

export function takPositionText(position: TakPosition): string {
  const ranks = Array.from({ length: TAK_SIZE }, (_, index) => position.stacks
    .slice(index * TAK_SIZE, (index + 1) * TAK_SIZE)
    .map((stack) => stack.join('') || 'x').join(',')
    .replace(/x(?:,x)+/g, (empty) => `x${empty.split(',').length}`))
  return `${ranks.join('/')} ${position.turn === 'w' ? 1 : 2} ${position.fullmove}`
}

function takTurn(move: string) {
  const put = placement.exec(move)
  const slide = put ? null : spread.exec(move)
  const square = put?.[2] ?? slide?.[2]
  if (!square) return null
  let x = square.charCodeAt(0) - 97
  let y = TAK_SIZE - Number(square[1])
  const path = [y * TAK_SIZE + x]
  const drops = slide ? Array.from(slide[4] || slide[1] || '1', Number) : []
  if (slide) {
    const [dx, dy] = directions[slide[3]!]!
    for (const _drop of drops) {
      x += dx
      y += dy
      if (x < 0 || x >= TAK_SIZE || y < 0 || y >= TAK_SIZE) return null
      path.push(y * TAK_SIZE + x)
    }
  }
  return { path, drops, stone: put?.[1] || '' }
}

export function takMovePath(move: string): number[] {
  return takTurn(move)?.path ?? []
}

export function applyTakMove(position: TakPosition, move: string): TakPosition {
  const parsed = takTurn(move)
  if (!parsed || position.stacks.length !== TAK_SIZE ** 2) return position
  const { path, drops, stone } = parsed
  const [from, ...destinations] = path as [number, ...number[]]
  const stacks = position.stacks.map((stack) => [...stack])
  if (!drops.length) {
    if (stacks[from]!.length) return position
    const white = (position.turn === 'w') !== (position.fullmove === 1)
    stacks[from]!.push(`${white ? 1 : 2}${stone === 'F' ? '' : stone}`)
  } else {
    const carry = drops.reduce((sum, drop) => sum + drop, 0)
    if (stacks[from]!.length < carry) return position
    const stones = stacks[from]!.splice(-carry)
    for (const [index, destination] of destinations.entries()) {
      const stack = stacks[destination]!
      const top = stack.at(-1)
      if (top?.endsWith('S')) stack[stack.length - 1] = top[0]!
      stack.push(...stones.splice(0, drops[index]!))
    }
  }
  return { stacks, turn: position.turn === 'w' ? 'b' : 'w', fullmove: position.fullmove + Number(position.turn === 'b') }
}

export function buildTakPositions(tps: string | null | undefined, moves: string[]): TakPosition[] {
  let position = parseTakPosition(tps)
  return [position, ...moves.map((move) => (position = applyTakMove(position, move)))]
}

export function takReserves(position: TakPosition) {
  const reserves = { white: { flats: 30, caps: 1 }, black: { flats: 30, caps: 1 } }
  for (const stack of position.stacks) {
    for (const stone of stack) {
      const reserve = stone.startsWith('1') ? reserves.white : reserves.black
      if (stone.endsWith('C')) reserve.caps--
      else reserve.flats--
    }
  }
  return reserves
}
