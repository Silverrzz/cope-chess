import type { BallotResult, Witness } from '@/quorum'

export const resultLabels: Record<BallotResult['outcome'], string> = {
  report_only: 'Poll results',
  quorum_not_met: 'Quorum not met',
  passed: 'Motion passed',
  rejected: 'Motion rejected',
}

export type WitnessStatus = Witness & { signed_at: string | null }

export function witnessReportUrl(ballotId: string, witness: Witness) {
  return witness.report_origin
    ? witness.report_origin + '/ballots/' + ballotId
    : '/api/quorum/ballots/' + ballotId + '/witness-reports/' + witness.public_key
}
