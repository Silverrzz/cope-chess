<template>
  <nav class="form-tabs" aria-label="Form types">
    <RouterLink :to="{ name: 'admin-forms' }">Ballot forms</RouterLink>
    <RouterLink :to="{ name: 'admin-form-collections' }">Response collections</RouterLink>
  </nav>
</template>
