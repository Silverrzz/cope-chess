import { ref } from "vue";

console.log("useBoardTheme loaded", Math.random());

const STORAGE_KEY = "cope-board-theme";

export interface BoardTheme {
  light: string;
  dark: string;
}

export const boardPresets: Record<string, BoardTheme> = {
  brown: { light: "#f0d9b5", dark: "#b58863" },
  blue:  { light: "#dee3e6", dark: "#8ca2ad" },
  green: { light: "#ffffdd", dark: "#86a666" },
  slate: { light: "#e8eaed", dark: "#6b7280" },
};

function readStored(): BoardTheme | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<BoardTheme>;
    if (typeof parsed.light === "string" && typeof parsed.dark === "string") {
      return { light: parsed.light, dark: parsed.dark };
    }
  } catch {
    // fall through to the default
  }
  return null;
}

const theme = ref<BoardTheme>(readStored() ?? boardPresets.brown!);

function applyBoardTheme(next: BoardTheme): void {
  const root = document.documentElement;
  root.style.setProperty("--board-light", next.light);
  root.style.setProperty("--board-dark", next.dark);
}

applyBoardTheme(theme.value);

export function useBoardTheme() {
  function previewBoardTheme(next: BoardTheme): void {
    applyBoardTheme(next);
  }

  function revertBoardTheme(): void {
    applyBoardTheme(theme.value)
  }

  function setBoardTheme(next: BoardTheme): void {
    theme.value = { ...next };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(theme.value));
    } catch {
    }
    applyBoardTheme(theme.value);
  }

  function resetBoardTheme(): void {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
    }
    theme.value = { ...boardPresets.brown! };
    applyBoardTheme(theme.value);
  }

  return { theme, boardPresets, previewBoardTheme, revertBoardTheme, setBoardTheme, resetBoardTheme };
}
