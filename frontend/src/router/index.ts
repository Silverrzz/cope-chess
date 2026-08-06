import { createRouter, createWebHistory } from "vue-router";

import AdminLayout from "@/components/layout/AdminLayout.vue";
import AuthLayout from "@/components/layout/AuthLayout.vue";
import PublicLayout from "@/components/layout/PublicLayout.vue";
import { useSessionStore } from "@/stores/session";

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: "/",
      component: PublicLayout,
      children: [
        {
          path: "",
          name: "home",
          component: () => import("@/pages/public/HomePage.vue"),
          meta: { title: "Home" },
        },
        {
          path: "events",
          name: "events",
          component: () => import("@/pages/public/EventsPage.vue"),
          meta: { title: "Events" },
        },
        {
          path: "events/:slug",
          name: "event",
          component: () => import("@/pages/public/EventPage.vue"),
          props: true,
          meta: { title: "Event" },
        },
        {
          path: "tournaments",
          name: "tournaments",
          component: () => import("@/pages/public/TournamentsPage.vue"),
          meta: { title: "Tournaments" },
        },
        {
          path: "tournaments/:id",
          name: "tournament",
          component: () => import("@/pages/public/TournamentPage.vue"),
          props: true,
          meta: { title: "Tournament" },
        },
        {
          path: "ratings",
          name: "ratings",
          component: () => import("@/pages/public/RatingsPage.vue"),
          meta: { title: "Ratings" },
        },
        {
          path: "engines",
          name: "engines",
          component: () => import("@/pages/public/EnginesPage.vue"),
          meta: { title: "Engines" },
        },
        {
          path: "engines/:id",
          name: "engine",
          component: () => import("@/pages/public/EnginePage.vue"),
          props: true,
          meta: { title: "Engine" },
        },
        {
          path: ":pathMatch(.*)*",
          name: "not-found",
          component: () => import("@/pages/NotFoundPage.vue"),
          meta: { title: "Page not found" },
        },
      ],
    },
    {
      path: "/auth",
      component: AuthLayout,
      children: [
        {
          path: "/admin/login",
          name: "admin-login",
          component: () => import("@/pages/admin/LoginPage.vue"),
          meta: { title: "Admin sign in", guestOnly: true },
        },
      ],
    },
    {
      path: "/admin",
      component: AdminLayout,
      meta: { requiresAdmin: true },
      children: [
        {
          path: "",
          name: "admin-dashboard",
          component: () => import("@/pages/admin/DashboardPage.vue"),
          meta: { title: "Dashboard" },
        },
        {
          path: "events",
          name: "admin-events",
          component: () => import("@/pages/admin/AdminEventsPage.vue"),
          meta: { title: "Manage events" },
        },
        {
          path: "events/:id",
          name: "admin-event",
          component: () => import("@/pages/admin/EventControlRoomPage.vue"),
          props: true,
          meta: { title: "Event control room" },
        },
        {
          path: "tournaments",
          name: "admin-tournaments",
          component: () => import("@/pages/admin/AdminTournamentsPage.vue"),
          meta: { title: "Manage tournaments" },
        },
        {
          path: "ratings",
          name: "admin-ratings",
          component: () => import("@/pages/admin/AdminRatingsPage.vue"),
          meta: { title: "Manage ratings" },
        },
        {
          path: "ratings/:id",
          name: "admin-rating-list",
          component: () => import("@/pages/admin/AdminRatingListPage.vue"),
          props: true,
          meta: { title: "Rating list" },
        },
        {
          path: "tournaments/new",
          name: "admin-tournament-new",
          component: () => import("@/pages/admin/TournamentFormPage.vue"),
          meta: { title: "Create tournament" },
        },
        {
          path: "tournaments/:id",
          name: "admin-tournament",
          component: () => import("@/pages/admin/AdminTournamentPage.vue"),
          props: true,
          meta: { title: "Manage tournament" },
        },
        {
          path: "engines",
          name: "admin-engines",
          component: () => import("@/pages/admin/EnginesPage.vue"),
          meta: { title: "Engines" },
        },
        {
          path: "engines/new",
          name: "admin-engine-new",
          component: () => import("@/pages/admin/EngineFormPage.vue"),
          meta: { title: "Register engine" },
        },
        {
          path: "engines/:id",
          name: "admin-engine",
          component: () => import("@/pages/admin/EngineFormPage.vue"),
          props: true,
          meta: { title: "Engine" },
        },
        {
          path: "engines/:id/versions/new",
          name: "admin-engine-version-new",
          component: () => import("@/pages/admin/EngineVersionCreatePage.vue"),
          props: true,
          meta: { title: "New engine version" },
        },
        {
          path: "engine-versions/:versionId",
          name: "admin-engine-version",
          component: () => import("@/pages/admin/EngineVersionPage.vue"),
          props: true,
          meta: { title: "Engine version" },
        },
        {
          path: "openings",
          name: "admin-openings",
          component: () => import("@/pages/admin/OpeningSuitesPage.vue"),
          meta: { title: "Opening suites" },
        },
        {
          path: "openings/new",
          name: "admin-opening-new",
          component: () => import("@/pages/admin/OpeningSuiteFormPage.vue"),
          meta: { title: "Create opening suite" },
        },
        {
          path: "openings/:id",
          name: "admin-opening-edit",
          component: () => import("@/pages/admin/OpeningSuiteFormPage.vue"),
          props: true,
          meta: { title: "Edit opening suite" },
        },
        {
          path: "workers",
          name: "admin-workers",
          component: () => import("@/pages/admin/WorkersPage.vue"),
          meta: { title: "Workers" },
        },
        {
          path: "workers/:id",
          name: "admin-worker",
          component: () => import("@/pages/admin/WorkerPage.vue"),
          props: true,
          meta: { title: "Worker" },
        },
        {
          path: "updates",
          name: "admin-updates",
          component: () => import("@/pages/admin/DeploymentsPage.vue"),
          meta: { title: "Release center" },
        },
        {
          path: "chat",
          name: "admin-chat",
          component: () => import("@/pages/admin/ChatAdminPage.vue"),
          meta: { title: "Chat moderation" },
        },
        {
          path: "settings",
          name: "admin-settings",
          component: () => import("@/pages/admin/SettingsPage.vue"),
          meta: { title: "Settings" },
        },
      ],
    },
  ],
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition;
    if (to.hash) {
      return {
        el: to.hash,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      };
    }
    if (to.path === from.path) return false;
    return { top: 0 };
  },
});

const assetReloadKey = "cope:stale-asset-reload";

function reloadForStaleAsset() {
  if (sessionStorage.getItem(assetReloadKey) === window.location.href) return;
  sessionStorage.setItem(assetReloadKey, window.location.href);
  window.location.reload();
}

window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();
  reloadForStaleAsset();
});

router.onError((error) => {
  const message = error instanceof Error ? error.message : String(error);
  if (/dynamically imported module|module script failed/i.test(message)) {
    reloadForStaleAsset();
  }
});

router.beforeEach(async (to) => {
  const session = useSessionStore();
  if (!session.initialized) await session.bootstrap();

  if (to.meta.requiresAdmin && !session.authenticated) {
    return {
      name: "admin-login",
      query: { redirect: to.fullPath },
    };
  }

  if (to.meta.guestOnly && session.authenticated) return { name: "admin-dashboard" };
  return true;
});

router.afterEach((to) => {
  sessionStorage.removeItem(assetReloadKey);
  document.title = to.meta.title ? `${to.meta.title} | COPE Chess` : "COPE Chess";
});

export default router;
