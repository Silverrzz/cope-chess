import HandBrainControlRoom from "./hand-brain/HandBrainControlRoom.vue";
import HandBrainHero from "./hand-brain/HandBrainHero.vue";
import HandBrainPage from "./hand-brain/HandBrainPage.vue";
import type { Component } from "vue";
import DuckVsFishArchiveHero from "./duck-vs-fish/DuckVsFishArchiveHero.vue";
import DuckVsFishBroadcast from "./duck-vs-fish/DuckVsFishBroadcast.vue";
import DuckVsFishControlRoom from "./duck-vs-fish/DuckVsFishControlRoom.vue";
import EngineRelayArchiveHero from "./engine-relay/EngineRelayArchiveHero.vue";
import EngineRelayBroadcast from "./engine-relay/EngineRelayBroadcast.vue";
import EngineRelayControlRoom from "./engine-relay/EngineRelayControlRoom.vue";
import PuzzleGauntletArchiveHero from "./puzzle-gauntlet/PuzzleGauntletArchiveHero.vue";
import PuzzleGauntletBroadcast from "./puzzle-gauntlet/PuzzleGauntletBroadcast.vue";
import PuzzleGauntletControlRoom from "./puzzle-gauntlet/PuzzleGauntletControlRoom.vue";

import type { EventHandlerState } from "@/types/events";

interface EventComponents {
  public?: Component;
  publicPresentation?: "embedded" | "immersive";
  archive?: Component;
  admin?: Component;
}

const components = new Map<string, EventComponents>();

function registerEventComponents(handlerKey: string, version: number, views: EventComponents): void {
  const key = `${handlerKey}@${version}`;
  if (components.has(key)) throw new Error(`Duplicate event components for ${key}.`);
  components.set(key, views);
}

export function eventComponents(handler: EventHandlerState): EventComponents | undefined {
  return handler.current ? components.get(`${handler.key}@${handler.required_version}`) : undefined;
}

registerEventComponents("engine-relay", 1, {
  public: EngineRelayBroadcast,
  publicPresentation: "immersive",
  archive: EngineRelayArchiveHero,
  admin: EngineRelayControlRoom,
});

registerEventComponents("engine-relay-finale", 1, {
  public: EngineRelayBroadcast,
  publicPresentation: "immersive",
  archive: EngineRelayArchiveHero,
  admin: EngineRelayControlRoom,
});

registerEventComponents("puzzle-gauntlet", 1, {
  public: PuzzleGauntletBroadcast,
  publicPresentation: "immersive",
  archive: PuzzleGauntletArchiveHero,
  admin: PuzzleGauntletControlRoom,
});

registerEventComponents("duck-vs-fish", 1, {
  public: DuckVsFishBroadcast,
  publicPresentation: "immersive",
  archive: DuckVsFishArchiveHero,
  admin: DuckVsFishControlRoom,
});

registerEventComponents("engine-hand-brain", 1, {
  public: HandBrainPage,
  publicPresentation: "immersive",
  archive: HandBrainHero,
  admin: HandBrainControlRoom,
});
