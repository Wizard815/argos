<script lang="ts">
  import { link } from "svelte-spa-router";
  import { models } from "../../stores/api";

  interface Props {
    model: string;
  }

  let { model }: Props = $props();

  let href = $derived(`/models/${encodeURIComponent(model)}`);
  // A profile variant has its own id but is the same model underneath: show the
  // display name whenever the id is a known model, and the raw id only when it
  // is not.
  let label = $derived($models.find((m) => m.id === model)?.name || model);
</script>

{#if model}
  <a href={href} use:link class="text-primary hover:underline">
    {label}
  </a>
{:else}
  <span class="text-muted-foreground">-</span>
{/if}
