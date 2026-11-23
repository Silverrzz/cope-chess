<template>
  <div class="queen-signal" aria-hidden="true">
    <div class="queen-signal__piece">
      <i class="queen-signal__crown"></i>
      <i class="queen-signal__body"></i>
      <i class="queen-signal__base"></i>
    </div>
    <div class="queen-signal__interference"></div>
  </div>
</template>

<style scoped>
.queen-signal {
  position: absolute;
  inset: var(--queen-signal-inset, 0);
  overflow: hidden;
  pointer-events: none;
  isolation: isolate;
  background:
    radial-gradient(ellipse at 54% 42%, #a02bff 0%, #5918a6 24%, #200b3e 44%, transparent 70%),
    radial-gradient(ellipse at 83% 70%, #4a0c7b, transparent 58%);
  mask-image: linear-gradient(90deg, transparent, #000 20%, #000 88%, transparent);
}

.queen-signal__piece {
  position: absolute;
  inset: 6% auto auto 54%;
  width: min(63%, 29rem);
  height: 100%;
  transform: translateX(-50%) rotate(-8deg) scaleY(1.02);
  filter: drop-shadow(-.28rem 0 .24rem #e4d0ff) drop-shadow(-1.1rem -.12rem 1.05rem #9f47ff);
}

.queen-signal__piece i {
  position: absolute;
  left: 0;
  right: 0;
  display: block;
  background: linear-gradient(100deg, #eee5f8, #8b7c9f 13%, #22162e 26%, #0a0710 45%, #050407 75%, #726184 100%);
}

.queen-signal__piece .queen-signal__crown {
  top: 3%;
  height: 37%;
  background:
    radial-gradient(circle closest-side, #f9f6ff 0%, #afa1bf 44%, #1d1228 88%, transparent 100%) 2% 27% / 17% 34% no-repeat,
    radial-gradient(circle closest-side, #fbf7ff 0%, #a893c2 42%, #241430 86%, transparent 100%) 25% 11% / 17% 36% no-repeat,
    radial-gradient(circle closest-side, #fef9ff 0%, #bfabd8 36%, #2d153b 84%, transparent 100%) 50% 0% / 20% 42% no-repeat,
    radial-gradient(circle closest-side, #e4d4ed 0%, #9875b5 40%, #1c1228 88%, transparent 100%) 75% 11% / 17% 36% no-repeat,
    radial-gradient(circle closest-side, #c4b0d8 0%, #9d79ba 38%, #1f102c 88%, transparent 100%) 98% 27% / 17% 34% no-repeat;
}

.queen-signal__crown::after {
  position: absolute;
  inset: 19% 0 0;
  content: "";
  clip-path: polygon(10% 22%, 30% 62%, 29% 10%, 42% 51%, 50% 0%, 59% 51%, 71% 10%, 70% 62%, 90% 22%, 78% 84%, 72% 100%, 28% 100%, 22% 84%);
  background: linear-gradient(102deg, #ece6fa, #8e79a7 24%, #100b15 43%, #08070c 75%, #5a466d);
}

.queen-signal__piece .queen-signal__body {
  inset: 36% 4% auto;
  height: 47%;
  clip-path: polygon(28% 0, 72% 0, 76% 8%, 71% 15%, 65% 21%, 66% 43%, 71% 65%, 80% 80%, 91% 91%, 92% 100%, 8% 100%, 9% 91%, 20% 80%, 29% 65%, 34% 43%, 35% 21%, 29% 15%, 24% 8%);
  background:
    linear-gradient(180deg, #d4c4ed 0%, transparent 13%, #8f75a42e 15%, transparent 23%, transparent 85%, #e6d8fc75 95%),
    linear-gradient(100deg, #f6f0fc, #c1accf 18%, #43314f 34%, #111017 51%, #030206 73%, #50445e 100%);
}

.queen-signal__piece .queen-signal__base {
  inset: 81% 1% auto;
  height: 13%;
  border-radius: 45% 45% 27% 27% / 34% 34% 23% 23%;
  background:
    linear-gradient(180deg, #d8c6df9c, transparent 29%, #d7b5ee2b 56%, #0b080d 70%),
    linear-gradient(95deg, #f7edf9, #84748e 20%, #18121e 50%, #08060b 78%, #70637b);
}

.queen-signal__interference {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(0deg, #08060c 0%, transparent 23%, transparent 86%, #08060c 102%),
    linear-gradient(90deg, transparent 22%, #09060c 22.3% 23.4%, transparent 23.6% 35%, #f9f2ffad 35.2% 35.4%, transparent 35.7% 42%, #100619 42.1% 44.5%, transparent 44.7% 53%, #eee0fcaa 53.2% 53.6%, #100b16 53.8% 55.8%, transparent 56% 60.5%, #0b070f 60.8% 63%, transparent 63.2% 70%, #faf2ffb8 70.1% 70.4%, #160820 70.6% 72.2%, transparent 72.4% 81%, #08060c 81.3% 84%, transparent 84.1%),
    repeating-linear-gradient(90deg, #e7cfff29 0 1px, transparent 1px 4px, #08050c85 4px 8px, transparent 8px 17px, #0a060eb3 17px 19px, transparent 19px 27px),
    repeating-linear-gradient(90deg, transparent 0 11px, #6b33b14f 11px 13px, transparent 13px 37px, #faf1ff4d 37px 38px, transparent 38px 43px),
    repeating-linear-gradient(0deg, transparent 0 4px, #08050c33 4px 5px);
}

.queen-signal__interference::after {
  position: absolute;
  inset: 0;
  content: "";
  opacity: .72;
  background:
    repeating-linear-gradient(90deg, transparent 0 8px, #faf5ff92 9px 10px, #060408 11px 14px, transparent 14px 21px),
    repeating-linear-gradient(90deg, transparent 0 26px, #f7edff85 27px 28px, #08060c 29px 31px, transparent 32px 51px);
  mask-image: linear-gradient(180deg, #000, transparent 32%, #000 55%, transparent 70%, #000 96%);
}
</style>
