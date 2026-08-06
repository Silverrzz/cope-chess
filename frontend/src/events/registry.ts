import type { Component } from "vue";
import EngineRelayBroadcast from "./engine-relay/EngineRelayBroadcast.vue";
import EngineRelayControlRoom from "./engine-relay/EngineRelayControlRoom.vue";

const publicComponents: Record<string, Component> = {};
const adminComponents: Record<string, Component> = {};

export function registerEventComponents(
  handlerKey: string,
  components: { public?: Component; admin?: Component },
): void {
  const key = handlerKey.trim();
  if (!key) throw new Error("Event handler key is required.");
  if (components.public) publicComponents[key] = components.public;
  if (components.admin) adminComponents[key] = components.admin;
}

export function publicEventComponent(handlerKey: string): Component | null {
  return publicComponents[handlerKey] ?? null;
}

export function adminEventComponent(handlerKey: string): Component | null {
  return adminComponents[handlerKey] ?? null;
}

registerEventComponents("engine-relay", {
  public: EngineRelayBroadcast,
  admin: EngineRelayControlRoom,
});
