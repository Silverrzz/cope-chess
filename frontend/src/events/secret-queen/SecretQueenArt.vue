<template>
  <div class="secret-queen-art cg-wrap" aria-hidden="true">
    <div class="queen-frame">
      <component :is="'piece'" class="queen white" />
    </div>
  </div>
</template>

<style scoped>
.secret-queen-art {
  position: relative;
  display: grid;
  place-items: center;
  isolation: isolate;
  min-width: 0;
  background: radial-gradient(ellipse at 50% 49%, #492380, #20122f 39%, transparent 71%);
}

.queen-frame {
  position: relative;
  width: min(89%, 24rem);
  aspect-ratio: 1;
  border: 1px solid #bea8e534;
  background: linear-gradient(145deg, #b39bc016, transparent 58%, #07050a47);
}

.queen-frame .queen {
  inset: 12%;
  width: 76%;
  height: 76%;
  background-size: contain;
  background-repeat: no-repeat;
  filter: drop-shadow(0 .75rem 1.55rem #0b0616);
}

.queen-frame::after {
  position: absolute;
  inset: 0;
  z-index: 3;
  content: "";
  background: linear-gradient(90deg, transparent 65%, #0b0713 65% 69%, #d7c1ff99 69% 69.35%, transparent 69.35% 77%, #0b0713b3 77% 79%, transparent 79%);
  mask-image: linear-gradient(180deg, transparent 14%, #000 35% 58%, transparent 82%);
}
</style>
