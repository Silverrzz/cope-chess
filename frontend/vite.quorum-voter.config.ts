import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    outDir: 'dist/quorum-voter',
    lib: { entry: 'src/quorum-voter-entry.ts', name: 'CopeQuorumVoter', formats: ['iife'], fileName: () => 'vote.js' },
    sourcemap: false,
  },
})
