import { ref } from "vue";

const CONFETTI_STORAGE_KEY = "cope-confetti-enabled";

function readConfettiEnabled(): boolean {
  try {
    return localStorage.getItem(CONFETTI_STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}

const confettiEnabled = ref(readConfettiEnabled());

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

  return { confettiEnabled, setConfettiEnabled };
}
