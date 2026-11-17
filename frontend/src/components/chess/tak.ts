export const TAK_SIZE = 6
export const TAK_START = 'x6/x6/x6/x6/x6/x6 1 1'
export const TAK_KOMI = 2

export type TakSide = 'white' | 'black'
export type TakStoneKind = 'flat' | 'wall' | 'cap'

export interface TakPosition {
  stacks: string[][]
  turn: 'w' | 'b'
  fullmove: number
}

const directions: Record<string, [number, number]> = { '>': [1, 0], '<': [-1, 0], '+': [0, -1], '-': [0, 1] }
const arrows: Record<string, string> = { '>': '→', '<': '←', '+': '↑', '-': '↓' }
const compass: Record<string, string> = { '>': 'east', '<': 'west', '+': 'north', '-': 'south' }
const placement = /^([FSC]?)([a-f][1-6])$/
const spread = /^([1-6]?)([a-f][1-6])([<>+-])([1-6]*)[SC]?\*?$/
const edges = [
  { name: 'north', dx: 0, dy: -1 },
  { name: 'east', dx: 1, dy: 0 },
  { name: 'south', dx: 0, dy: 1 },
  { name: 'west', dx: -1, dy: 0 },
] as const

export function takStoneKind(stone: string): TakStoneKind {
  return stone.endsWith('C') ? 'cap' : stone.endsWith('S') ? 'wall' : 'flat'
}

export function takStoneName(kind: TakStoneKind): string {
  return kind === 'cap' ? 'capstone' : kind === 'wall' ? 'standing stone' : 'flat stone'
}

export function takStoneSide(stone: string): TakSide {
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
  const kind = takStoneKind(stone)
  const flattened = move.endsWith('*')
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
  const description = direction
    ? `Carry ${count} from ${square} ${compass[direction]}. Drop ${drops.join(', ')}.${flattened ? ' Flattens a standing stone.' : ''}`
    : `Place a ${takStoneName(kind)} on ${square}.`
  return { square, path, drops, stone, kind, count, direction, arrow: direction ? arrows[direction] : undefined, flattened, description }
}

export function takMovePath(move: string): number[] {
  return parseTakMove(move)?.path ?? []
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

function connectingRoad(owners: Array<TakSide | null>, side: TakSide, horizontal: boolean): number[] {
  const coordinate = (square: number) => horizontal ? square % TAK_SIZE : Math.floor(square / TAK_SIZE)
  const queue = owners.flatMap((owner, square) => owner === side && coordinate(square) === 0 ? [square] : [])
  const parents = new Map<number, number | undefined>(queue.map((square) => [square, undefined]))
  for (const square of queue) {
    if (coordinate(square) === TAK_SIZE - 1) {
      const path = []
      for (let current: number | undefined = square; current !== undefined; current = parents.get(current)) path.push(current)
      return path.reverse()
    }
    for (const { dx, dy } of edges) {
      const next = neighborAt(square, dx, dy)
      if (next !== undefined && owners[next] === side && !parents.has(next)) {
        parents.set(next, square)
        queue.push(next)
      }
    }
  }
  return []
}

export function takRoads(position: TakPosition) {
  const owners = position.stacks.map((stack) => {
    const top = stack.at(-1)
    return top && takStoneKind(top) !== 'wall' ? takStoneSide(top) : null
  })
  const mover = position.turn === 'w' ? 'black' : 'white'
  const other = mover === 'black' ? 'white' : 'black'
  let path: number[] = []
  for (const side of [mover, other] as const) {
    path = connectingRoad(owners, side, true)
    if (!path.length) path = connectingRoad(owners, side, false)
    if (path.length) break
  }
  const winner = path.length ? owners[path[0]!] ?? null : null
  const horizontal = path.length > 0 && path[0]! % TAK_SIZE === 0 && path.at(-1)! % TAK_SIZE === TAK_SIZE - 1
  const links = owners.map((side, square) => {
    if (!side) return []
    const pathIndex = path.indexOf(square)
    return edges.flatMap(({ name, dx, dy }) => {
      const target = neighborAt(square, dx, dy)
      if (target !== undefined && owners[target] !== side) return []
      const onPath = pathIndex >= 0 && (target === undefined
        ? (pathIndex === 0 && name === (horizontal ? 'west' : 'north')) ||
          (pathIndex === path.length - 1 && name === (horizontal ? 'east' : 'south'))
        : target === path[pathIndex - 1] || target === path[pathIndex + 1])
      return [{ name, side, onPath }]
    })
  })
  return { links, winner, winningSquares: new Set(path) }
}
