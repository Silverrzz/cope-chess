import "vue-router";

declare module "vue-router" {
  interface RouteMeta {
    title?: string;
    requiresAdmin?: boolean;
    requiresPlatformAccess?: boolean;
    guestOnly?: boolean;
  }
}

export {};
