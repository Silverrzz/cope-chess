export interface ApplicationInputs {
  'agents.set_drain.v1': { draining: boolean }
  'competitions.rename.v1': { name: string }
  'engines.update_metadata.v1': { name: string; author: string }
}

export type ApplicationCommand = keyof ApplicationInputs
