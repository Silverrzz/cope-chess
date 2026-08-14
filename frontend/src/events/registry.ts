import type { Component } from "vue";
import EngineRelayBroadcast from "./engine-relay/EngineRelayBroadcast.vue";
import EngineRelayControlRoom from "./engine-relay/EngineRelayControlRoom.vue";

const publicComponents: Record<string, Component> = {};
const publicPresentations: Record<string, "embedded" | "immersive"> = {};
const adminComponents: Record<string, Component> = {};

export function registerEventComponents(
  handlerKey: string,
  components: { public?: Component; publicPresentation?: "embedded" | "immersive"; admin?: Component },
): void {
  const key = handlerKey.trim();
  if (!key) throw new Error("Event handler key is required.");
  if (components.public) {
    publicComponents[key] = components.public;
    publicPresentations[key] = components.publicPresentation ?? "embedded";
  }
  if (components.admin) adminComponents[key] = components.admin;
}

export function publicEventComponent(handlerKey: string): Component | null {
  return publicComponents[handlerKey] ?? null;
}

export function publicEventPresentation(handlerKey: string): "embedded" | "immersive" {
  return publicPresentations[handlerKey] ?? "embedded";
}

export function adminEventComponent(handlerKey: string): Component | null {
  return adminComponents[handlerKey] ?? null;
}

registerEventComponents("engine-relay", {
  public: EngineRelayBroadcast,
  publicPresentation: "immersive",
  admin: EngineRelayControlRoom,
});

registerEventComponents("engine-relay-finale", {
  public: EngineRelayBroadcast,
  publicPresentation: "immersive",
  admin: EngineRelayControlRoom,
});
