import type { BallotResult } from '@/quorum'

export const resultLabels: Record<BallotResult['outcome'], string> = {
  report_only: 'Poll results',
  quorum_not_met: 'Quorum not met',
  passed: 'Motion passed',
  rejected: 'Motion rejected',
}
