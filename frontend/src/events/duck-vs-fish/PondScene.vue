<template>
  <div class="pond-scene" aria-hidden="true">
    <svg class="pond-water" viewBox="0 0 1200 700" preserveAspectRatio="xMidYMid slice">
      <defs>
        <radialGradient id="pond-water"><stop stop-color="#244537" /><stop offset="1" stop-color="#080f13" /></radialGradient>
        <radialGradient id="pond-leaf"><stop stop-color="#798c49" /><stop offset="1" stop-color="#35462b" /></radialGradient>
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
      <g fill="none" stroke="#9fb371" stroke-width="5" stroke-linecap="round"><path d="M5 700q25-100 5-190M21 700q5-70 43-165M1190 700q-40-90-22-190M1170 700q-6-90-45-140" /></g>
    </svg>
  </div>
</template>

<style scoped>
.pond-scene { position: absolute; z-index: -1; inset: 0; overflow: hidden; pointer-events: none; opacity: .38; }
.pond-water { width: 100%; height: 100%; }
.pond-pads { opacity: .7; }
</style>
