<template>
  <div class="secret-queen-art cg-wrap" aria-hidden="true">
    <component :is="'piece'" class="queen white" />
  </div>
</template>

<style scoped>
.secret-queen-art {
  width: 100%;
  aspect-ratio: 1;
}

.secret-queen-art .queen {
  inset: 20%;
  width: 60%;
  height: 60%;
  background-size: contain;
  background-repeat: no-repeat;
}
</style>
