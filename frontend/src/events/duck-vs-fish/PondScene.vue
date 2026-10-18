<script setup lang="ts">
import duck from "@/assets/duck.svg";
</script>

<template>
  <div class="pond-scene" aria-hidden="true">
    <svg class="pond-water" viewBox="0 0 1200 700" preserveAspectRatio="xMidYMid slice">
      <defs>
        <radialGradient id="pond-water"><stop stop-color="#548f7c" /><stop offset="1" stop-color="#163f3d" /></radialGradient>
        <radialGradient id="pond-leaf"><stop stop-color="#a8b969" /><stop offset="1" stop-color="#526e39" /></radialGradient>
        <linearGradient id="pond-fish" x2=".8" y2="1"><stop stop-color="#d2e9d4" /><stop offset="1" stop-color="#639c99" /></linearGradient>
        <g id="pond-pad"><path d="M0 0 43-30A65 44 0 1 0 48 25Z" fill="url(#pond-leaf)" stroke="#bfd08b" stroke-opacity=".25" /><path d="M0 0-53-10M0 0-32 28M0 0 10 38M0 0-15-37" stroke="#d3dba1" stroke-opacity=".24" fill="none" /></g>
      </defs>
      <path fill="url(#pond-water)" d="M0 0h1200v700H0z" />
      <g fill="none" stroke="#b1d9be" stroke-opacity=".12">
        <ellipse cx="825" cy="330" rx="285" ry="110" /><ellipse cx="825" cy="330" rx="230" ry="85" /><ellipse cx="825" cy="330" rx="170" ry="58" />
        <ellipse cx="60" cy="130" rx="110" ry="35" /><ellipse cx="1130" cy="100" rx="135" ry="42" /><ellipse cx="1090" cy="590" rx="150" ry="45" />
        <path d="M20 300q140-22 260-5m-140 310q230-34 380-5m490-400q100 10 180-5M240 85q100 15 220 0M660 660q120-20 210-5" />
      </g>
      <g class="pond-pads"><use href="#pond-pad" transform="translate(32 125) rotate(-20)" /><use href="#pond-pad" transform="translate(1130 92) rotate(20) scale(.9)" /><use href="#pond-pad" transform="translate(1055 605) rotate(-25) scale(1.15)" /><use href="#pond-pad" transform="translate(94 613) rotate(50) scale(1.3)" /><use href="#pond-pad" transform="translate(1180 440) rotate(130) scale(.65)" /><use href="#pond-pad" transform="translate(500 680) rotate(-10) scale(.6)" /></g>
      <g transform="translate(1105 80)" fill="#f2dfd4"><path d="M0 6Q-25-12-16-23Q0-19 0 6M0 6Q25-12 16-23Q0-19 0 6" /><path d="M0 8Q-13-14 0-30Q13-14 0 8" /><ellipse cy="7" rx="13" ry="4" fill="#edc653" /></g>
      <g class="pond-fish" transform="translate(960 400) rotate(-18)"><path d="M-75 0Q-25-65 50-8L94-39 82 0 94 39 50 8Q-25 65-75 0Z" fill="url(#pond-fish)" stroke="#315957" stroke-width="3" /><path d="M-15-28-5-52 21-25M-25 28-6 48 10 26" fill="#80b2a2" stroke="#315957" stroke-width="3" /><path d="M-40-17Q-25 0-40 17" fill="none" stroke="#426e66" stroke-width="3" /><circle cx="-52" cy="-5" r="5" fill="#203c35" /><circle cx="-53" cy="-7" r="1.5" fill="#f6eed6" /></g>
      <g fill="none" stroke="#9fb371" stroke-width="5" stroke-linecap="round"><path d="M5 700q25-100 5-190M21 700q5-70 43-165M1190 700q-40-90-22-190M1170 700q-6-90-45-140" /></g>
    </svg>
    <img class="pond-duck" :src="duck" alt="" />
    <span class="pond-spark pond-spark--one"></span><span class="pond-spark pond-spark--two"></span>
  </div>
</template>

<style scoped>
.pond-scene { position: absolute; inset: 0; overflow: hidden; pointer-events: none; }
.pond-water { width: 100%; height: 100%; }
.pond-duck { position: absolute; top: 30%; left: 58%; width: clamp(7rem, 16vw, 13rem); filter: drop-shadow(0 15px 8px #112d3660); transform: rotate(-9deg); animation: pond-bob 6s ease-in-out infinite; }
.pond-fish { opacity: .85; }
.pond-spark { position: absolute; width: .35rem; height: .35rem; border-radius: 50%; background: #f1db9b; box-shadow: 0 0 12px #f2dc99; animation: pond-glow 5s ease-in-out infinite; }
.pond-spark--one { top: 24%; left: 45%; }.pond-spark--two { top: 70%; right: 12%; animation-delay: -2s; }
@keyframes pond-bob { 50% { transform: translateY(-9px) rotate(-5deg); } }
@keyframes pond-glow { 50% { opacity: .2; } }
@media (max-width: 44rem) { .pond-duck { top: 14%; left: auto; right: 12%; width: 7rem; opacity: .75; } }
@media (prefers-reduced-motion: reduce) { .pond-duck, .pond-spark { animation: none; } }
</style>
