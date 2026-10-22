<script setup lang="ts">
import { useId } from "vue";

const id = useId();
</script>

<template>
  <div class="pond-scene" aria-hidden="true">
    <svg class="pond-water" viewBox="0 0 1200 700" preserveAspectRatio="xMidYMid slice">
      <defs>
        <radialGradient :id="`${id}-leaf`"><stop stop-color="#788951" /><stop offset="1" stop-color="#354b35" /></radialGradient>
        <g :id="`${id}-pad`"><path d="M0 0 45-45A64 64 0 1 0 58 27Z" :fill="`url(#${id}-leaf)`" stroke="#b2c18a" stroke-opacity=".18" /><path d="M0 0-49-12M0 0-29 43M0 0 18 49M0 0-16-49" stroke="#c2ce99" stroke-opacity=".16" fill="none" /></g>
        <g :id="`${id}-fish`" fill="#85aaa0"><path d="M-34 0-53-16-49 0-53 16Z" /><path d="M-37 0Q-8-23 29-3Q35 0 29 3Q-8 23-37 0Z" /><path d="M-11-10-19-22 6-12M-11 10-19 22 6 12" /><circle cx="22" cy="-2" r="1.8" fill="#1b403c" /></g>
        <g :id="`${id}-duck`"><path d="M-35 7-48-3-41 17Q-20 35 13 22Q26 17 25 4L19-9 8-7 5 7Q-9-2-35 7Z" fill="#9b9e8a" /><path d="M-28 9Q-9 0 7 13Q-5 23-26 17Z" fill="#667d74" /><path d="M7 9 8-8 21-10 24 6 19 16Z" fill="#92674d" /><path d="M9-8 10-14 20-15 22-9Z" fill="#d2d0b1" /><path d="M9-14Q2-31 15-34Q29-35 28-23L23-14Z" fill="#447b58" /><path d="M27-26 43-22 27-19Z" fill="#bba569" /><circle cx="22" cy="-27" r="1.6" fill="#172e28" /></g>
      </defs>
      <g class="pond-pads">
        <use :href="`#${id}-pad`" transform="translate(45 105) scale(.85 .38) rotate(-35)" />
        <use :href="`#${id}-pad`" transform="translate(1125 100) scale(.8 .36) rotate(65)" />
        <use :href="`#${id}-pad`" transform="translate(1080 610) scale(1.2 .58) rotate(-50)" />
        <use :href="`#${id}-pad`" transform="translate(100 600) scale(1.25 .6) rotate(115)" />
        <use :href="`#${id}-pad`" transform="translate(1175 420) scale(.7 .32) rotate(170)" />
        <use :href="`#${id}-pad`" transform="translate(480 685) scale(.7 .34) rotate(25)" />
      </g>
      <g class="pond-creatures">
        <use :href="`#${id}-duck`" transform="translate(172 207) scale(.7)" />
        <use :href="`#${id}-duck`" transform="translate(1042 358) scale(-.6 .6)" />
        <use :href="`#${id}-fish`" transform="translate(932 192) rotate(-24) scale(.8)" />
        <use :href="`#${id}-fish`" transform="translate(280 570) rotate(155)" />
        <use :href="`#${id}-fish`" transform="translate(1132 512) rotate(-35) scale(.6)" />
      </g>
      <g class="pond-pieces" fill="#a9c2ad" text-anchor="middle" font-family="Georgia, serif" font-size="54">
        <text x="310" y="150" transform="rotate(-18 310 130)">&#9822;</text>
        <text x="940" y="600" transform="rotate(15 940 580)">&#9820;</text>
        <text x="70" y="395" transform="rotate(-12 70 375)">&#9823;</text>
        <text x="740" y="665" transform="rotate(20 740 645)">&#9815;</text>
      </g>
    </svg>
  </div>
</template>

<style scoped>
.pond-scene { position: absolute; z-index: -1; inset: 0; overflow: hidden; pointer-events: none; }
.pond-water { width: 100%; height: 100%; }
.pond-pads { opacity: .28; }
.pond-creatures { opacity: .12; }
.pond-pieces { opacity: .1; }
</style>
