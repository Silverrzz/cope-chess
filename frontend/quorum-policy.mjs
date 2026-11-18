import { createPrivateKey, createPublicKey, randomUUID, sign } from 'node:crypto'
import { link, mkdir, open, readFile, unlink, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fingerprint } from './src/quorum.ts'
import { policyChangeStatement, verifyPolicyChain, witnessPolicy } from './src/quorum-history.ts'

const json = async file => JSON.parse(await readFile(file, 'utf8'))

try {
  const [command, origin, currentFile, proposalFile, ...args] = process.argv.slice(2)
  if (!origin || new URL(origin).origin !== origin || !origin.startsWith('https://') || !currentFile || !proposalFile
    || !(command === 'approve' && args.length === 1 || command === 'assemble' && args.length >= 2)) {
    throw new Error('Usage: quorum-policy.mjs approve <origin> <current.json> <proposed.json> <local-archive>\n'
      + '   or: quorum-policy.mjs assemble <origin> <current.json> <proposed.json> <output.json> <approval.json>...')
  }
  const chain = await verifyPolicyChain(await json(currentFile), origin)
  const next = witnessPolicy(await json(proposalFile))
  const current = await fingerprint(chain.current)
  const proposed = await fingerprint(next)
  if (current === proposed) throw new Error('The proposed policy is identical to the current policy.')
  const statement = policyChangeStatement(origin, chain.head, current, proposed)
  if (command === 'approve') {
    const archive = args[0]
    if (await readFile(join(archive, 'origin'), 'utf8') !== origin
      || await readFile(join(archive, 'witness-policy-head'), 'utf8') !== chain.head) {
      throw new Error('The local verifier is not pinned to the current policy for this COPE origin.')
    }
    const key = createPrivateKey(await readFile(join(archive, 'witness-ed25519.pem')))
    const operator = createPublicKey(key).export({ format: 'der', type: 'spki' }).subarray(-32).toString('hex')
    if (!Object.values(chain.current.operators).includes(operator)) throw new Error('This verifier is not a current operator.')
    const approval = { statement, public_key: operator,
      signature: sign(null, Buffer.from(JSON.stringify(statement)), key).toString('hex') }
    const dir = join(archive, 'policy-approvals')
    await mkdir(dir, { recursive: true, mode: 0o700 })
    const target = join(dir, chain.head + '.json')
    const temporary = join(dir, randomUUID() + '.tmp')
    try {
      const file = await open(temporary, 'wx', 0o600)
      try {
        await file.writeFile(JSON.stringify(approval))
        await file.sync()
      } finally {
        await file.close()
      }
      try {
        await link(temporary, target)
      } catch (error) {
        if (error.code !== 'EEXIST') throw error
        const previous = await json(target)
        if (JSON.stringify(previous) !== JSON.stringify(approval)) {
          throw new Error('This verifier already approved a different successor for this policy.')
        }
      }
      // Do not release a signature until the non-overwritable approval is durable.
      if (process.platform !== 'win32') {
        for (const directory of [dir, archive]) {
          const handle = await open(directory, 'r')
          try { await handle.sync() } finally { await handle.close() }
        }
      }
      console.log(JSON.stringify(approval))
    } finally {
      await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error })
    }
  } else {
    const [output, ...paths] = args
    const signatures = Object.create(null)
    for (const path of paths) {
      const approval = await json(path)
      if (JSON.stringify(approval.statement) !== JSON.stringify(statement)
        || !Object.values(chain.current.operators).includes(approval.public_key)
        || Object.hasOwn(signatures, approval.public_key)) throw new Error('Approval does not match the proposed change or repeats an operator.')
      signatures[approval.public_key] = approval.signature
    }
    const bundle = { initial: chain.bundle.initial, changes: [...chain.bundle.changes, { policy: next, signatures }] }
    await verifyPolicyChain(bundle, origin)
    await writeFile(output, JSON.stringify(bundle, null, 2) + '\n', { flag: 'wx' })
  }
} catch (error) {
  console.error(error.message)
  process.exitCode = 1
}
