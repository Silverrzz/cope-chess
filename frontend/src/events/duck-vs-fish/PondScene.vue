<script setup lang="ts">
import { useId } from "vue";
import duck from "@/assets/duck.svg";
import fish from "@/assets/fairy-stockfish-event.png";

const id = useId();
</script>

<template>
  <div class="pond-scene" aria-hidden="true">
    <svg class="pond-water" viewBox="0 0 1200 700" preserveAspectRatio="xMidYMid slice">
      <defs>
        <radialGradient :id="`${id}-leaf`"><stop stop-color="#788951" /><stop offset="1" stop-color="#354b35" /></radialGradient>
        <g :id="`${id}-pad`"><path d="M0 0 45-45A64 64 0 1 0 58 27Z" :fill="`url(#${id}-leaf)`" stroke="#b2c18a" stroke-opacity=".18" /><path d="M0 0-49-12M0 0-29 43M0 0 18 49M0 0-16-49" stroke="#c2ce99" stroke-opacity=".16" fill="none" /></g>
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
        <image :href="duck" x="145" y="185" width="55" height="45" transform="rotate(-8 172 207)" />
        <image :href="duck" x="1020" y="340" width="45" height="37" transform="translate(2085 0) scale(-1 1)" />
        <image :href="fish" x="900" y="160" width="65" height="65" transform="rotate(-20 932 192)" />
        <image :href="fish" x="240" y="530" width="80" height="80" transform="rotate(25 280 570)" />
        <image :href="fish" x="1110" y="490" width="44" height="44" transform="rotate(-35 1132 512)" />
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
