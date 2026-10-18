<script setup lang="ts">
import { useId } from "vue";

const id = useId();
</script>

<template>
  <svg viewBox="0 0 128 112" aria-hidden="true">
    <defs>
      <linearGradient :id="`${id}-body`" x1=".2" y1="0" x2=".65" y2="1"><stop stop-color="#d7bc4a" /><stop offset=".3" stop-color="#519b78" /><stop offset=".65" stop-color="#2c8496" /><stop offset="1" stop-color="#352553" /></linearGradient>
      <linearGradient :id="`${id}-face`" x1=".5" y1="0" x2=".25" y2="1"><stop stop-color="#b99745" /><stop offset=".35" stop-color="#bc4960" /><stop offset=".75" stop-color="#8c2e67" /><stop offset="1" stop-color="#472349" /></linearGradient>
      <linearGradient :id="`${id}-gold`" x1="0" y1="1" x2=".8" y2="0"><stop stop-color="#a04f2a" /><stop offset=".45" stop-color="#d1a637" /><stop offset="1" stop-color="#ead46b" /></linearGradient>
      <linearGradient :id="`${id}-fin`" x1="0" y1="1" x2="1" y2="0"><stop stop-color="#49315e" /><stop offset=".4" stop-color="#b23b78" /><stop offset="1" stop-color="#e0af46" /></linearGradient>
      <pattern :id="`${id}-scales`" width="12" height="14" patternUnits="userSpaceOnUse"><path d="M0 0Q6 13 12 0M-6 7Q0 20 6 7M6 7Q12 20 18 7" fill="none" stroke="#102c36" stroke-width="1.4" /><path d="M2 2Q5 7 8 4M8 9Q11 14 14 11" fill="none" stroke="#c6dfb2" stroke-opacity=".45" stroke-width=".8" /></pattern>
      <clipPath :id="`${id}-body-clip`"><path d="M24 40C44 28 70 37 86 53C98 67 99 88 111 103C84 110 58 100 39 84C24 75 16 55 24 40Z" /></clipPath>
    </defs>
    <g stroke="#202322" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round">
      <path d="M25 41C32 31 24 26 33 25C50 22 65 30 72 42L55 49Z" :fill="`url(#${id}-fin)`" />
      <path d="M30 29Q49 27 65 39M30 34Q47 30 59 41" fill="none" stroke="#e1b456" stroke-width="1.1" />
      <path d="M77 52C91 29 108 31 126 4C125 25 117 37 104 45L124 34C121 46 109 54 98 57L122 53C116 66 107 69 94 68L116 76C105 84 94 79 85 72L106 96C90 94 78 75 77 52Z" fill="#26283a" />
      <path d="M77 50C90 29 108 31 126 4C121 27 103 45 84 56C79 59 74 56 77 50Z" :fill="`url(#${id}-gold)`" />
      <path d="M80 58C94 47 110 48 124 34C119 49 102 58 83 62Z" fill="#a18735" />
      <path d="M81 62C95 56 107 59 122 53C113 66 99 68 83 66Z" fill="#43988f" />
      <path d="M83 67C97 67 104 72 116 76C103 85 92 78 84 72Z" fill="#b13e6b" />
      <path d="M83 72C92 77 98 88 106 96C92 93 81 80 80 73Z" :fill="`url(#${id}-fin)`" />
      <path d="M82 50Q106 37 120 17M85 59Q107 53 118 43M87 64Q106 65 116 58M86 71Q100 78 109 77M85 77Q94 89 101 92" fill="none" stroke="#ead68a" stroke-opacity=".65" stroke-width="1" />
      <path d="M24 40C44 28 70 37 86 53C98 67 99 88 111 103C84 110 58 100 39 84C24 75 16 55 24 40Z" :fill="`url(#${id}-body)`" />
      <path d="M17 27H115V112H17Z" :fill="`url(#${id}-scales)`" :clip-path="`url(#${id}-body-clip)`" stroke="none" />
      <path d="M37 69C33 80 37 92 48 104C34 101 24 88 29 74Z" :fill="`url(#${id}-fin)`" />
      <path d="M34 77Q30 90 43 99M38 76Q38 91 46 100" fill="none" stroke="#e5ae5c" stroke-opacity=".6" stroke-width="1" />
      <path d="M10 43C19 34 33 33 44 40C56 47 59 60 49 72C39 84 20 79 11 66C3 57 2 50 10 43Z" :fill="`url(#${id}-face)`" />
      <path d="M13 42Q31 36 40 44M35 43C49 50 51 65 35 73M14 65Q22 76 34 74" fill="none" stroke="#d4b45d" stroke-width="1.5" />
      <ellipse cx="12" cy="55" rx="7" ry="12" transform="rotate(-35 12 55)" fill="#cba64e" />
      <ellipse cx="12" cy="55" rx="3.5" ry="8" transform="rotate(-35 12 55)" fill="#3c2534" stroke-width="1.2" />
      <circle cx="29" cy="47" r="6" fill="#d4b34c" /><circle cx="29" cy="47" r="3.6" fill="#151f22" stroke="none" /><circle cx="30" cy="45.5" r="1.1" fill="#f3e8b9" stroke="none" />
      <path d="M18 40L20 39M24 38L26 38" fill="none" stroke="#f1d3ba" stroke-width="1.1" />
    </g>
  </svg>
</template>
