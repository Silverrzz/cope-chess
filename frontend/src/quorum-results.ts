import type { BallotResult, Witness } from '@/quorum'
import type { FeedbackTone } from '@/types/ui'

export const resultLabels: Record<BallotResult['outcome'], string> = {
  report_only: 'Poll results',
  quorum_not_met: 'Quorum not met',
  passed: 'Motion passed',
  rejected: 'Motion rejected',
}

export const resultTones: Record<BallotResult['outcome'], FeedbackTone> = {
  report_only: 'info',
  quorum_not_met: 'warning',
  passed: 'success',
  rejected: 'danger',
}

export type WitnessStatus = Witness & { signed_at: string | null }

export function witnessReportUrl(ballotId: string, witness: Witness) {
  return witness.report_origin
    ? witness.report_origin + '/ballots/' + ballotId
    : '/api/quorum/ballots/' + ballotId + '/witness-reports/' + witness.public_key
}
