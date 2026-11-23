<template>
  <div class="secret-queen-art cg-wrap" aria-hidden="true">
    <component :is="'piece'" class="queen white queen-base" />
    <component :is="'piece'" class="queen white queen-slice queen-slice--left" />
    <component :is="'piece'" class="queen white queen-slice queen-slice--middle" />
    <component :is="'piece'" class="queen white queen-slice queen-slice--right" />
  </div>
</template>

<style scoped>
.secret-queen-art {
  width: 100%;
  aspect-ratio: 1;
  isolation: isolate;
  filter: drop-shadow(0 0 1rem #a871ed58);
}

.secret-queen-art .queen {
  inset: 20%;
  width: 60%;
  height: 60%;
  background-size: contain;
  background-repeat: no-repeat;
}

.queen-base {
  mask-image: linear-gradient(90deg, #000 0 25%, transparent 25% 30%, #000 30% 53%, transparent 53% 56%, #000 56% 68%, transparent 68% 75%, #000 75%);
}

.queen-slice {
  transform: translate(var(--slice-x), var(--slice-y));
  animation: queen-interference 8s steps(1, end) infinite;
}

.queen-slice--left {
  --slice-x: -7%;
  --slice-y: 9%;
  --burst-x: -13%;
  --burst-y: 15%;
  clip-path: inset(0 70% 0 25%);
  opacity: .78;
  filter: drop-shadow(0 .3rem .3rem #a372ff);
}

.queen-slice--middle {
  --slice-x: 1%;
  --slice-y: 7%;
  --burst-x: -3%;
  --burst-y: -5%;
  clip-path: inset(0 44% 0 53%);
  opacity: .5;
}

.queen-slice--right {
  --slice-x: 8%;
  --slice-y: -10%;
  --burst-x: 15%;
  --burst-y: -14%;
  clip-path: inset(0 25% 0 68%);
  filter: drop-shadow(0 -.3rem .35rem #b788ff);
}

@keyframes queen-interference {
  0%, 86%, 95%, 100% { transform: translate(var(--slice-x), var(--slice-y)); }
  87%, 92% { transform: translate(var(--burst-x), var(--burst-y)); }
  90% { transform: translate(calc(var(--slice-x) / 2), calc(var(--slice-y) / 2)); }
}

@media (prefers-reduced-motion: reduce) {
  .queen-slice { animation: none; }
}
</style>
