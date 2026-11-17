<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

import AppIcon from "@/components/ui/AppIcon.vue";
import BaseButton from "@/components/ui/BaseButton.vue";
import SettingsDrawer from "@/components/ui/SettingsDrawer.vue";
import { useSessionStore } from "@/stores/session";

const route = useRoute();
const session = useSessionStore();
const menuOpen = ref(false);
const compactGameLayout = computed(() => route.name === "tournament" || route.name === "event-arena");
const repositoryUrl = computed(() => session.session?.repository_url ?? "");

const navItems = [
  { label: "Ratings", to: "/ratings" },
  { label: "Tournaments", to: "/tournaments" },
  { label: "Engines", to: "/engines" },
  { label: "Events", to: "/events" },
  { label: "Donors", to: "/donors" },
  { label: "Forms", to: "/forms" },
];

function navActive(path: string): boolean {
  return path === "/" ? route.path === path : route.path === path || route.path.startsWith(`${path}/`);
}

watch(
  () => route.fullPath,
  () => (menuOpen.value = false),
);
</script>

<template>
  <div class="public-shell" :class="{ 'public-shell--game': compactGameLayout }">
    <a class="skip-link" href="#main-content">Skip to content</a>
    <header class="public-header" :class="{ 'public-header--compact': compactGameLayout }">
      <div class="page-container public-header__inner">
        <RouterLink class="brand" to="/" aria-label="COPE Chess home">
          <span class="brand__mark"><AppIcon name="logo" :size="25" /></span>
          <span class="brand__text">COPE</span>
        </RouterLink>

        <nav id="public-navigation" class="public-nav" :class="{ 'public-nav--open': menuOpen }" aria-label="Primary navigation">
          <RouterLink
            v-for="item in navItems"
            :key="item.to"
            class="public-nav__link"
            :class="{ 'public-nav__link--active': navActive(item.to) }"
            :to="item.to"
            active-class=""
            exact-active-class=""
          >
            {{ item.label }}
          </RouterLink>
          <RouterLink class="public-nav__link public-nav__admin-mobile" to="/admin">Admin</RouterLink>
        </nav>

        <div class="public-header__actions">
          <BaseButton
            class="public-header__homelab"
            variant="ghost"
            size="small"
            href="https://www.gofundme.com/f/cope-homelab"
            target="_blank"
            rel="noopener noreferrer"
          >
            <span class="homelab-label">Homelab Project</span>
          </BaseButton>
          <BaseButton
            v-if="repositoryUrl"
            variant="ghost"
            size="small"
            icon-only
            :href="repositoryUrl"
            target="_blank"
            rel="noopener noreferrer"
            title="GitHub"
            aria-label="Open COPE Chess on GitHub"
          >
            <template #icon><AppIcon name="github" :size="18" /></template>
            GitHub
          </BaseButton>
          <BaseButton
            variant="ghost"
            size="small"
            icon-only
            href="https://discord.gg/4Wpd9pJHdq"
            target="_blank"
            rel="noopener noreferrer"
            title="Discord"
            aria-label="Join COPE Chess on Discord"
          >
            <template #icon><AppIcon name="discord" :size="18" /></template>
            Discord
          </BaseButton>
          <SettingsDrawer />
          <BaseButton class="public-header__admin" variant="ghost" size="small" to="/admin">
            Admin
          </BaseButton>
          <BaseButton
            class="public-header__menu"
            variant="ghost"
            size="small"
            icon-only
            :aria-expanded="menuOpen"
            aria-controls="public-navigation"
            :aria-label="menuOpen ? 'Close navigation' : 'Open navigation'"
            @click="menuOpen = !menuOpen"
          >
            <template #icon><AppIcon :name="menuOpen ? 'close' : 'menu'" :size="20" /></template>
            {{ menuOpen ? "Close navigation" : "Open navigation" }}
          </BaseButton>
        </div>
      </div>
    </header>

    <main id="main-content" class="public-main" :class="{ 'public-main--game': compactGameLayout }" tabindex="-1">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.public-shell {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
}

.public-header {
  position: sticky;
  z-index: 50;
  top: 0;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-header);
  backdrop-filter: blur(14px);
}

.public-header__inner {
  min-height: var(--header-height);
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-6);
}

.public-header--compact .public-header__inner {
  min-height: 2.85rem;
}

.public-header--compact .public-nav__link {
  padding-block: 0.38rem;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text);
  font-weight: 760;
  letter-spacing: 0.04em;
  text-decoration: none;
}

.brand__mark {
  color: var(--color-accent);
}

.public-nav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.public-nav__link {
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  padding: 0.5rem 0.7rem;
  font-size: 0.875rem;
  font-weight: 620;
  text-decoration: none;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.public-nav__link:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.public-nav__link--active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.public-header__actions {
  display: flex;
  align-items: center;
  justify-self: end;
  gap: var(--space-1);
}

.public-header__actions .public-header__homelab {
  padding-inline: var(--space-2);
  color: var(--color-warning);
  white-space: nowrap;
}

@media (prefers-reduced-motion: no-preference) and (forced-colors: none) {
  @supports (background-clip: text) and (color: color-mix(in srgb, white, black)) {
    .homelab-label {
      background-image: linear-gradient(
        108deg,
        var(--color-warning) 44%,
        color-mix(in srgb, var(--color-warning) 92%, white) 50%,
        var(--color-warning) 56%
      );
      background-size: 250% 100%;
      background-clip: text;
      color: transparent;
      animation: homelab-glint 24s linear infinite;
    }
  }
}

@keyframes homelab-glint {
  0%, 93% {
    background-position: 100% 50%;
  }

  99%, 100% {
    background-position: 0% 50%;
  }
}

.public-header__menu {
  display: none;
}

.public-nav__admin-mobile {
  display: none;
}

.public-main {
  min-width: 0;
  flex: 1 0 auto;
  padding-block: clamp(var(--space-6), 4vw, var(--space-12));
}

.public-main:focus {
  outline: none;
}

.public-main--game {
  padding-block: 0;
}

@media (max-width: 62rem) {
  .public-header__inner {
    grid-template-columns: auto minmax(0, 1fr);
    gap: var(--space-3);
  }

  .public-nav {
    position: absolute;
    top: calc(100% + 1px);
    right: var(--space-4);
    left: var(--space-4);
    display: none;
    align-items: stretch;
    flex-direction: column;
    gap: var(--space-1);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-lg);
    background: var(--color-surface-raised);
    box-shadow: var(--shadow-md);
    padding: var(--space-2);
  }

  .public-nav--open {
    display: flex;
  }

  .public-nav__link {
    padding: 0.7rem var(--space-3);
  }

  .public-nav__admin-mobile {
    display: block;
    border-top: 1px solid var(--color-border);
    border-radius: 0 0 var(--radius-md) var(--radius-md);
    margin-top: var(--space-1);
  }

  .public-header__menu {
    display: inline-flex;
  }

  .public-header__admin {
    display: none;
  }

}

@media (max-width: 25rem) {
  .public-header__inner {
    column-gap: var(--space-2);
  }

  .brand__text {
    display: none;
  }

  .public-header__actions {
    gap: 0;
  }
}
</style>
