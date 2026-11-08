self.onmessage = async ({ data }: MessageEvent<{ challenge: string; difficulty: number }>) => {
  const { challenge, difficulty } = data
  if (!crypto.subtle || !Number.isInteger(difficulty) || difficulty < 1 || difficulty > 16) {
    self.postMessage(null)
    return
  }
  const text = new TextEncoder()
  const batchSize = 64
  try {
    for (let first = 0; first < 2 ** 22; first += batchSize) {
      const results = await Promise.all(Array.from({ length: batchSize }, async (_, index) => {
        const digest = new DataView(await crypto.subtle.digest('SHA-256', text.encode(`${challenge}:${first + index}`)))
        return (digest.getUint16(0) >>> (16 - difficulty)) === 0
      }))
      const match = results.indexOf(true)
      if (match >= 0) {
        self.postMessage(first + match)
        return
      }
    }
  } catch { }
  self.postMessage(null)
}
