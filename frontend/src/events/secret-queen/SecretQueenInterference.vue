<script setup lang="ts">
defineProps<{ announcement?: boolean }>();
</script>

<template>
  <div class="queen-interference" :class="{ 'queen-interference--announcement': announcement }" aria-hidden="true">
    <div class="queen-interference-field"></div>
  </div>
</template>

<style scoped>
.queen-interference {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.queen-interference-field {
  position: absolute;
  inset: -15% 0 -12% 37%;
  background:
    linear-gradient(90deg, transparent 22%, #14091c90 22% 27%, transparent 27% 42%, #10071bba 42% 46%, transparent 46% 61%, #14091b8c 61% 68%, transparent 68% 80%, #13091b95 80% 84%, transparent 84%),
    radial-gradient(ellipse at 63% 46%, #7330b8 0%, #411578 22%, #210d42 43%, transparent 72%);
  mask-image: radial-gradient(ellipse at 61% 48%, #000 8%, #000d 40%, transparent 75%);
}

.queen-interference-field::before {
  position: absolute;
  inset: 0;
  content: "";
  background:
    linear-gradient(0deg, transparent, #e4cdffb8 32%, #b17df4bb 76%, transparent) 41.6% 22% / 1px 72% no-repeat,
    linear-gradient(180deg, #9f5fff00, #9f5fff91 32%, #e2c6ff7a 73%, transparent) 46.8% 43% / 3px 66% no-repeat,
    linear-gradient(180deg, transparent, #0c0619 23% 81%, transparent) 52.7% 15% / 2.4% 88% no-repeat,
    linear-gradient(180deg, transparent 8%, #ead6ffb8 32%, #c193ffa8 68%, transparent 91%) 58.6% 51% / 2px 84% no-repeat,
    linear-gradient(180deg, transparent, #0d0618b8 19% 67%, transparent) 64.1% 47% / 3.3% 91% no-repeat,
    linear-gradient(180deg, transparent, #e4c9ffab 30%, #a45bf77a 72%, transparent) 68.5% 26% / 1px 71% no-repeat,
    linear-gradient(180deg, transparent 2%, #a054f294 36%, #7533a35c 77%, transparent) 75.6% 63% / 2px 94% no-repeat,
    linear-gradient(180deg, transparent, #f2e2ff82 43%, #af71ff5e 59%, transparent) 82.2% 32% / 1px 64% no-repeat;
}

.queen-interference-field::after {
  position: absolute;
  inset: 12% 11% 6% 22%;
  content: "";
  opacity: .64;
  background:
    repeating-linear-gradient(90deg, transparent 0 13px, #c794fd39 13px 14px, transparent 14px 29px, #0a0315ad 29px 33px, transparent 33px 43px),
    repeating-linear-gradient(90deg, transparent 0 27px, #caaafa48 27px 28px, transparent 28px 51px, #7c3db93b 51px 54px, transparent 54px 63px);
  mask-image: linear-gradient(180deg, transparent 7%, #000 20%, transparent 43%, #000 52%, #000b 64%, transparent 91%);
  animation: interference-drift 8s steps(1, end) infinite;
}

.queen-interference--announcement .queen-interference-field {
  inset: -35% 13% 19%;
  opacity: .72;
  mask-image: radial-gradient(ellipse at 56% 50%, #000, #000b 30%, transparent 68%);
}

@keyframes interference-drift {
  0%, 86%, 95%, 100% { transform: translate(0); }
  87%, 92% { transform: translate(-1.1%, 3%); }
  90% { transform: translate(.8%, -3%); }
}

@media (max-width: 700px) {
  .queen-interference-field { inset: -14% -25% 3% 0; }
  .queen-interference--announcement .queen-interference-field { inset: -24% -38% 24%; }
}

@media (prefers-reduced-motion: reduce) {
  .queen-interference-field::after { animation: none; }
}
</style>
