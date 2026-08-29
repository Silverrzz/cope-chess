import { ref } from "vue";

const CONFETTI_STORAGE_KEY = "cope-confetti-enabled";
const EVENT_MUSIC_STORAGE_KEY = "cope-event-music-enabled";

function readConfettiEnabled(): boolean {
  try {
    return localStorage.getItem(CONFETTI_STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}

function readEventMusicEnabled(): boolean {
  try {
    return localStorage.getItem(EVENT_MUSIC_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

const confettiEnabled = ref(readConfettiEnabled());
const eventMusicEnabled = ref(readEventMusicEnabled());

export function useViewerSettings() {
  function setConfettiEnabled(enabled: boolean): void {
    confettiEnabled.value = enabled;
    try {
      if (enabled) localStorage.removeItem(CONFETTI_STORAGE_KEY);
      else localStorage.setItem(CONFETTI_STORAGE_KEY, "false");
    } catch {
      return;
    }
  }

  function setEventMusicEnabled(enabled: boolean): void {
    eventMusicEnabled.value = enabled;
    try {
      localStorage.setItem(EVENT_MUSIC_STORAGE_KEY, String(enabled));
    } catch {
      return;
    }
  }

  return { confettiEnabled, eventMusicEnabled, setConfettiEnabled, setEventMusicEnabled };
}
