<template>
  <div class="hb-mark" aria-hidden="true">
    <svg class="hb-cog"
  xmlns="http://www.w3.org/2000/svg"
  width="24"
  height="24"
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
>
  <path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" />
  <circle cx="12" cy="12" r="3" />
</svg>

    <svg class="hb-hand"
  xmlns="http://www.w3.org/2000/svg"
  width="24"
  height="24"
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
>
  <path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2" />
  <path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2" />
  <path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8" />
  <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />
</svg>

    <svg class="hb-brain"
  xmlns="http://www.w3.org/2000/svg"
  width="24"
  height="24"
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
>
  <path d="M12 18V5" />
  <path d="M15 13a4.17 4.17 0 01-3-4 4.17 4.17 0 01-3 4" />
  <path d="M17.707 6.294A3 3 0 1012 5a3 3 0 10-5.707 1.294" />
  <path d="M17.996 5.125a4 4 0 012.526 5.77" />
  <path d="M18 18a4 4 0 002-7.465" />
  <path d="M19.967 17.482a4 4 0 11-7.967.517 4 4 0 11-7.967-.517" />
  <path d="M6 18a4 4 0 01-2-7.465" />
  <path d="M6.004 5.125a4 4 0 00-2.526 5.77" />
</svg>

  </div>
</template>

<style scoped>
.hb-mark { position: relative; width: 100%; aspect-ratio: 1; color: #ff8ca8; }
.hb-cog { position: absolute; inset: 0; width: 100%; height: 100%; opacity: .12; stroke-width: .7; transform: rotate(12deg); }
.hb-hand, .hb-brain { position: absolute; width: 43%; height: 43%; stroke-width: 1.15; }
.hb-hand { left: 9%; top: 37%; transform: rotate(-12deg); }
.hb-brain { right: 8%; top: 19%; }
</style>
