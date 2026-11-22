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

function stored(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
const evaluationPerspective = ref<'white' | 'side-to-move'>(stored('cope-eval-perspective', 'white') === 'side-to-move' ? 'side-to-move' : 'white');
const kibitzerEnabled = ref(stored('cope-kibitzer-enabled', 'true') !== 'false');
const boardAnnotationsEnabled = ref(stored('cope-board-annotations-enabled', 'true') !== 'false');
const chatGameLinksVisible = ref(stored('cope-chat-game-links-visible', 'true') !== 'false');

export function useViewerSettings() {
  function setEvaluationPerspective(value: 'white' | 'side-to-move'): void {
    evaluationPerspective.value = value;
    try { localStorage.setItem('cope-eval-perspective', value); } catch {}
  }
  function setKibitzerEnabled(value: boolean): void {
    kibitzerEnabled.value = value;
    try { localStorage.setItem('cope-kibitzer-enabled', String(value)); } catch {}
  }
  function setBoardAnnotationsEnabled(value: boolean): void {
    boardAnnotationsEnabled.value = value;
    try { localStorage.setItem('cope-board-annotations-enabled', String(value)); } catch {}
  }
  function setChatGameLinksVisible(value: boolean): void {
    chatGameLinksVisible.value = value;
    try { localStorage.setItem('cope-chat-game-links-visible', String(value)); } catch {}
  }
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

  return {
    evaluationPerspective, setEvaluationPerspective,
    kibitzerEnabled, setKibitzerEnabled,
    boardAnnotationsEnabled, setBoardAnnotationsEnabled,
    chatGameLinksVisible, setChatGameLinksVisible,
    confettiEnabled, setConfettiEnabled,
    eventMusicEnabled, setEventMusicEnabled,
  };
}
