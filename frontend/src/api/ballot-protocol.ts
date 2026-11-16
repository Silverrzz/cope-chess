import type { BallotOpening, Cell, Envelope, EqualityProof, Manifest, Openings, Receipt, SelectionProof, VoterProof } from './ballots'

const prime = BigInt('0x' +
  'ffffffffffffffffadf85458a2bb4a9aafdc5620273d3cf1' +
  'd8b9c583ce2d3695a9e13641146433fbcc939dce249b3ef9' +
  '7d2fe363630c75d8f681b202aec4617ad3df1ed5d5fd6561' +
  '2433f51f5f066ed0856365553ded1af3b557135e7f57c935' +
  '984f0c70e0e68b77e2a689daf3efe8721df158a136ade735' +
  '30acca4f483a797abc0ab182b324fb61d108a94bb2c8e3fb' +
  'b96adab760d7f4681d4f42a3de394df4ae56ede76372bb19' +
  '0b07a7c8ee0a6d709e02fce1cdf7e2ecc03404cd28342f61' +
  '9172fe9ce98583ff8e4f1232eef28183c3fe3b1b4c6fad73' +
  '3bb5fcbc2ec22005c58ef1837d1683b2c6f34a26c1b2effa' +
  '886b423861285c97ffffffffffffffff')
const order = (prime - 1n) / 2n
const generator = 2n
const inverseGenerator = (prime + 1n) / 2n
const protocol = 'cope-secret-ballot-v1'

function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') {
    const entries = Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
    return '{' + entries.map(([key, item]) => JSON.stringify(key) + ':' + canonical(item)).join(',') + '}'
  }
  return JSON.stringify(value)
}

function encoded(value: unknown): Uint8Array<ArrayBuffer> {
  return new TextEncoder().encode(canonical(value))
}

function hex(bytes: ArrayBuffer | Uint8Array): string {
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('')
}

function bytes(value: string): Uint8Array<ArrayBuffer> {
  if (!/^(?:[a-f0-9]{2})+$/.test(value)) throw new Error('This proof has an invalid encoding.')
  return Uint8Array.from(value.match(/../g)!, part => Number.parseInt(part, 16))
}

function number(value: bigint): string {
  return value.toString(16)
}

function integer(value: string): bigint {
  return BigInt('0x' + value)
}

function power(base: bigint, exponent: bigint): bigint {
  let value = 1n
  while (exponent > 0n) {
    if (exponent & 1n) value = value * base % prime
    base = base * base % prime
    exponent >>= 1n
  }
  return value
}

function scalar(nonzero = false): bigint {
  while (true) {
    const randomness = crypto.getRandomValues(new Uint8Array(256))
    randomness[0] = randomness[0]! & 127
    const value = integer(hex(randomness))
    if (value < order && (!nonzero || value > 0n)) return value
  }
}

export async function digest(value: unknown): Promise<string> {
  return hex(await crypto.subtle.digest('SHA-256', encoded(value)))
}

async function challenge(context: unknown[], ...elements: bigint[]): Promise<bigint> {
  return integer(await digest([protocol, context, ...elements.map(number)]))
}

async function proveEqual(context: unknown[], base: bigint, publicValue: bigint, factor: bigint,
                          secret: bigint): Promise<EqualityProof> {
  const nonce = scalar(true)
  const c = await challenge(context, base, publicValue, factor, power(generator, nonce), power(base, nonce))
  return [number(c), number((nonce + c * secret) % order)]
}

async function proveSelection(context: unknown[], key: bigint, alpha: bigint, beta: bigint,
                              bit: number, secret: bigint): Promise<SelectionProof> {
  const nonce = scalar(true)
  const falseChallenge = scalar()
  const falseResponse = scalar()
  const exponent = (order - falseChallenge) % order
  const otherFactor = bit === 0 ? beta * inverseGenerator % prime : beta
  const simulated = [
    power(generator, falseResponse) * power(alpha, exponent) % prime,
    power(key, falseResponse) * power(otherFactor, exponent) % prime,
  ]
  const actual = [power(generator, nonce), power(key, nonce)]
  const commitments = bit === 0 ? [...actual, ...simulated] : [...simulated, ...actual]
  const total = await challenge(context, key, alpha, beta, ...commitments)
  const trueChallenge = (total + order - falseChallenge) % order
  const trueResponse = (nonce + trueChallenge * secret) % order
  const [c0, c1, z0, z1] = bit === 0
    ? [trueChallenge, falseChallenge, trueResponse, falseResponse]
    : [falseChallenge, trueChallenge, falseResponse, trueResponse]
  return [number(c0!), number(c1!), number(z0!), number(z1!)]
}

export function voterProof(value: unknown, slug: string): VoterProof {
  const proof = value as Partial<VoterProof> | null
  if (proof?.type !== 'cope-voter-proof-v1' || proof.slug !== slug ||
      typeof proof.public !== 'string' || !/^[a-f0-9]{64}$/.test(proof.public) ||
      typeof proof.private !== 'string' || !/^302e020100300506032b657004220420[a-f0-9]{64}$/.test(proof.private)) {
    throw new Error('Choose a private voter proof created for this form.')
  }
  return proof as VoterProof
}

export async function generateVoterProof(slug: string): Promise<VoterProof> {
  const keys = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']) as CryptoKeyPair
  return {
    type: 'cope-voter-proof-v1',
    slug,
    public: hex(await crypto.subtle.exportKey('raw', keys.publicKey)),
    private: hex(await crypto.subtle.exportKey('pkcs8', keys.privateKey)),
  }
}

export async function verifyReceipt(manifest: Manifest, receipt: Receipt): Promise<void> {
  if (await digest(manifest) !== receipt.manifest) throw new Error('This receipt belongs to another form.')
  const key = await crypto.subtle.importKey('raw', bytes(manifest.receipt_key), 'Ed25519', false, ['verify'])
  const valid = await crypto.subtle.verify(
    'Ed25519', key, bytes(receipt.signature), encoded([protocol, 'accepted', receipt.manifest, receipt.fingerprint]),
  )
  if (!valid) throw new Error('This receipt does not have a valid Cope signature.')
}

export async function sealBallot(manifest: Manifest, proof: VoterProof, choices: number[]) {
  const manifestId = await digest(manifest)
  const credential = proof.public
  if (!manifest.roster.includes(credential)) throw new Error('Your enrollment code was not registered for this form.')
  const key = integer(manifest.key)
  const privateKey = await crypto.subtle.importKey('pkcs8', bytes(proof.private), 'Ed25519', false, ['sign'])
  const publicKey = await crypto.subtle.importKey('raw', bytes(credential), 'Ed25519', false, ['verify'])
  const questions: Envelope['questions'] = []
  const openings: Openings = []
  for (const [index, question] of manifest.questions.entries()) {
    const choice = choices[index]
    if (choice === undefined || choice < 0 || choice >= question.options.length) {
      throw new Error('Select one option for each question.')
    }
    const context = ['selection', manifestId, credential, index]
    const row: Cell[] = []
    const secrets: string[] = []
    let alpha = 1n
    let beta = 1n
    let combined = 0n
    for (const [option] of question.options.entries()) {
      const secret = scalar(true)
      const bit = Number(option === choice)
      const a = power(generator, secret)
      const b = power(key, secret) * generator ** BigInt(bit) % prime
      const validity = await proveSelection([...context, option], key, a, b, bit, secret)
      row.push([number(a), number(b), validity])
      secrets.push(number(secret))
      alpha = alpha * a % prime
      beta = beta * b % prime
      combined = (combined + secret) % order
    }
    questions.push({
      cells: row,
      proof: await proveEqual([...context, 'exactly-one'], key, alpha, beta * inverseGenerator % prime, combined),
    })
    openings.push([choice, secrets])
  }
  const message = encoded([protocol, 'ballot', manifestId, credential, questions])
  const signature = await crypto.subtle.sign('Ed25519', privateKey, message)
  if (!await crypto.subtle.verify('Ed25519', publicKey, signature, message)) {
    throw new Error('The private voter proof does not match its enrollment code.')
  }
  const envelope: Envelope = { manifest: manifestId, credential, questions, signature: hex(signature) }
  const opening: BallotOpening = { manifest: manifestId, fingerprint: await digest(envelope), openings }
  return { envelope, opening }
}
