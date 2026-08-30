import type { Component } from "vue";
import EngineRelayArchiveHero from "./engine-relay/EngineRelayArchiveHero.vue";
import EngineRelayBroadcast from "./engine-relay/EngineRelayBroadcast.vue";
import EngineRelayControlRoom from "./engine-relay/EngineRelayControlRoom.vue";
import PuzzleGauntletArchiveHero from "./puzzle-gauntlet/PuzzleGauntletArchiveHero.vue";
import PuzzleGauntletBroadcast from "./puzzle-gauntlet/PuzzleGauntletBroadcast.vue";
import PuzzleGauntletControlRoom from "./puzzle-gauntlet/PuzzleGauntletControlRoom.vue";

const publicComponents: Record<string, Component> = {};
const publicPresentations: Record<string, "embedded" | "immersive"> = {};
const archiveComponents: Record<string, Component> = {};
const adminComponents: Record<string, Component> = {};

export function registerEventComponents(
  handlerKey: string,
  components: { public?: Component; publicPresentation?: "embedded" | "immersive"; archive?: Component; admin?: Component },
): void {
  const key = handlerKey.trim();
  if (!key) throw new Error("Event handler key is required.");
  if (components.public) {
    publicComponents[key] = components.public;
    publicPresentations[key] = components.publicPresentation ?? "embedded";
  }
  if (components.archive) archiveComponents[key] = components.archive;
  if (components.admin) adminComponents[key] = components.admin;
}

export function publicEventComponent(handlerKey: string): Component | null {
  return publicComponents[handlerKey] ?? null;
}

export function publicEventPresentation(handlerKey: string): "embedded" | "immersive" {
  return publicPresentations[handlerKey] ?? "embedded";
}

export function archiveEventComponent(handlerKey: string): Component | null {
  return archiveComponents[handlerKey] ?? null;
}

export function adminEventComponent(handlerKey: string): Component | null {
  return adminComponents[handlerKey] ?? null;
}

registerEventComponents("engine-relay", {
  public: EngineRelayBroadcast,
  publicPresentation: "immersive",
  archive: EngineRelayArchiveHero,
  admin: EngineRelayControlRoom,
});

registerEventComponents("engine-relay-finale", {
  public: EngineRelayBroadcast,
  publicPresentation: "immersive",
  archive: EngineRelayArchiveHero,
  admin: EngineRelayControlRoom,
});

registerEventComponents("puzzle-gauntlet", {
  public: PuzzleGauntletBroadcast,
  publicPresentation: "immersive",
  archive: PuzzleGauntletArchiveHero,
  admin: PuzzleGauntletControlRoom,
});
