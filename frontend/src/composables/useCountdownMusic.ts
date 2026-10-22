import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from "vue";
import { useViewerSettings } from "./useViewerSettings";

export function useCountdownMusic(remaining: Ref<number>, active: Ref<boolean>) {
  const { eventMusicEnabled, setEventMusicEnabled } = useViewerSettings();
  const state = ref<"armed" | "playing" | "blocked" | "unavailable">("armed");
  let audio: HTMLAudioElement | null = null;
  let pending = false;
  const label = computed(() => !eventMusicEnabled.value ? "Enable countdown music"
    : state.value === "blocked" ? "Play countdown music"
    : state.value === "unavailable" ? "Retry countdown music"
    : state.value === "playing" ? "Mute countdown music" : "Countdown music on");

  function release(): void {
    const previous = audio;
    audio = null;
    pending = false;
    previous?.pause();
    previous?.removeAttribute("src");
    previous?.load();
    state.value = "armed";
  }

  function prepare(): HTMLAudioElement {
    if (!audio) {
      const prepared = new Audio("/audio/openbench-engine-clash-countdown.wav");
      audio = prepared;
      prepared.preload = "auto";
      prepared.addEventListener("error", () => {
        if (audio === prepared) state.value = "unavailable";
      });
    }
    return audio;
  }

  async function sync(force = false): Promise<void> {
    if (!active.value || remaining.value <= 0 || !eventMusicEnabled.value) {
      release();
      return;
    }
    const current = prepare();
    if (pending) return;
    if (remaining.value > 60_000) {
      current.pause();
      state.value = "armed";
      return;
    }
    if (!force && ["blocked", "unavailable"].includes(state.value)) return;
    const expected = Math.max(0, (60_000 - remaining.value) / 1000);
    if (current.readyState > 0 && Math.abs(current.currentTime - expected) > 1) current.currentTime = expected;
    if (!current.paused) return;
    pending = true;
    try {
      if (current.readyState > 0) current.currentTime = expected;
      await current.play();
      if (audio !== current || !active.value || !eventMusicEnabled.value || remaining.value <= 0 || remaining.value > 60_000) {
        current.pause();
        return;
      }
      const position = Math.max(0, (60_000 - remaining.value) / 1000);
      if (Math.abs(current.currentTime - position) > 1) current.currentTime = position;
      state.value = "playing";
    } catch (cause) {
      if (audio === current) state.value = (cause as { name?: string }).name === "NotAllowedError" ? "blocked" : "unavailable";
    } finally {
      if (audio === current) pending = false;
    }
  }

  async function toggle(): Promise<void> {
    if (eventMusicEnabled.value && !["blocked", "unavailable"].includes(state.value)) {
      setEventMusicEnabled(false);
      release();
      return;
    }
    if (state.value === "unavailable") release();
    setEventMusicEnabled(true);
    if (remaining.value <= 60_000) {
      await sync(true);
      return;
    }
    const current = prepare();
    current.muted = true;
    pending = true;
    try {
      await current.play();
      current.pause();
      current.currentTime = 0;
      if (audio === current) state.value = "armed";
    } catch {
      if (audio === current) state.value = "blocked";
    } finally {
      current.muted = false;
      if (audio === current) pending = false;
    }
  }

  function resume(): void {
    if (document.visibilityState !== "hidden") void sync();
  }
  watch([remaining, active, eventMusicEnabled], () => { void sync(); }, { immediate: true });
  onMounted(() => { document.addEventListener("visibilitychange", resume); });
  onBeforeUnmount(() => { document.removeEventListener("visibilitychange", resume); release(); });
  return { label, state, eventMusicEnabled, toggle };
}
