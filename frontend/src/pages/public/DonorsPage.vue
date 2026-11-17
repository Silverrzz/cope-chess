<script setup lang="ts">
import DonorRankings from '@/components/public/DonorRankings.vue'
import PageHeader from '@/components/ui/PageHeader.vue'
</script>

<template>
  <div class="page-container page-stack">
    <PageHeader title="Donors" description="Hardware contributions to Cope’s engine testing, ranked by estimated CPU hours." />
    <DonorRankings />
  </div>
</template>
