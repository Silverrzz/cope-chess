export const TAK_SIZE = 6
export const TAK_START = 'x6/x6/x6/x6/x6/x6 1 1'
export const TAK_KOMI = 2

export type TakSide = 'white' | 'black'

export interface TakPosition {
  stacks: string[][]
  turn: 'w' | 'b'
  fullmove: number
}

const directions: Record<string, [number, number]> = { '>': [1, 0], '<': [-1, 0], '+': [0, -1], '-': [0, 1] }
const placement = /^([FSC]?)([a-f][1-6])$/
const spread = /^([1-6]?)([a-f][1-6])([<>+-])([1-6]*)[SC]?\*?$/
const edges = [
  { dx: 0, dy: -1 },
  { dx: 1, dy: 0 },
  { dx: 0, dy: 1 },
  { dx: -1, dy: 0 },
] as const

function takStoneKind(stone: string) {
  return stone.endsWith('C') ? 'cap' : stone.endsWith('S') ? 'wall' : 'flat'
}

function takStoneSide(stone: string): TakSide {
  return stone.startsWith('1') ? 'white' : 'black'
}

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

export function parseTakMove(move: string) {
  const put = placement.exec(move)
  const slide = put ? null : spread.exec(move)
  const square = put?.[2] ?? slide?.[2]
  if (!square) return null
  let x = square.charCodeAt(0) - 97
  let y = TAK_SIZE - Number(square[1])
  const path = [y * TAK_SIZE + x]
  const direction = slide?.[3]
  const count = Number(slide?.[1] || 1)
  const drops = slide ? Array.from(slide[4] || String(count), Number) : []
  const stone = put?.[1] || ''
  if (direction) {
    if (drops.reduce((total, drop) => total + drop, 0) !== count) return null
    const [dx, dy] = directions[direction]!
    for (let step = 0; step < drops.length; step++) {
      x += dx
      y += dy
      if (x < 0 || x >= TAK_SIZE || y < 0 || y >= TAK_SIZE) return null
      path.push(y * TAK_SIZE + x)
    }
  }
  return { path, drops, stone, direction }
}

export function takMovePath(move: string): number[] {
  return parseTakMove(move)?.path ?? []
}

export function takSquareName(index: number): string {
  return `${'abcdef'[index % TAK_SIZE]}${TAK_SIZE - Math.floor(index / TAK_SIZE)}`
}

export function applyTakMove(position: TakPosition, move: string): TakPosition {
  const parsed = parseTakMove(move)
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
      const reserve = reserves[takStoneSide(stone)]
      if (stone.endsWith('C')) reserve.caps--
      else reserve.flats--
    }
  }
  return reserves
}

export function takFlatCounts(position: TakPosition): Record<TakSide, number> {
  const counts = { white: 0, black: 0 }
  for (const stack of position.stacks) {
    const top = stack.at(-1)
    if (top && takStoneKind(top) === 'flat') counts[takStoneSide(top)]++
  }
  return counts
}

function neighborAt(square: number, dx: number, dy: number): number | undefined {
  const x = square % TAK_SIZE + dx
  const y = Math.floor(square / TAK_SIZE) + dy
  return x >= 0 && y >= 0 && x < TAK_SIZE && y < TAK_SIZE ? y * TAK_SIZE + x : undefined
}

function hasConnectingRoad(owners: Array<TakSide | null>, side: TakSide, horizontal: boolean): boolean {
  const coordinate = (square: number) => horizontal ? square % TAK_SIZE : Math.floor(square / TAK_SIZE)
  const queue = owners.flatMap((owner, square) => owner === side && coordinate(square) === 0 ? [square] : [])
  const visited = new Set(queue)
  for (const square of queue) {
    if (coordinate(square) === TAK_SIZE - 1) return true
    for (const { dx, dy } of edges) {
      const next = neighborAt(square, dx, dy)
      if (next !== undefined && owners[next] === side && !visited.has(next)) {
        visited.add(next)
        queue.push(next)
      }
    }
  }
  return false
}

export function takRoadWinner(position: TakPosition): TakSide | null {
  const owners = position.stacks.map((stack) => {
    const top = stack.at(-1)
    return top && takStoneKind(top) !== 'wall' ? takStoneSide(top) : null
  })
  const mover = position.turn === 'w' ? 'black' : 'white'
  const other = mover === 'black' ? 'white' : 'black'
  for (const side of [mover, other] as const) {
    if (hasConnectingRoad(owners, side, true) || hasConnectingRoad(owners, side, false)) return side
  }
  return null
}
