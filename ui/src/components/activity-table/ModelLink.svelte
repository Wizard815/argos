<script lang="ts">
  import { link } from "svelte-spa-router";
  import { models } from "../../stores/api";

  interface Props {
    model: string;
  }

  let { model }: Props = $props();

  let entry = $derived($models.find((m) => m.id === model));
  // A profile variant has its own id but is the same model underneath: link to
  // the base model's page, and label it with a name or at least the base id, so
  // one model stays one row in the activity table instead of reading as a
  // second model. Most models have no configured name at all, so the base id is
  // the usual fallback rather than the pretty name.
  let target = $derived(entry?.base_model_id || model);
  let baseEntry = $derived($models.find((m) => m.id === target));
  let href = $derived(`/models/${encodeURIComponent(target)}`);
  let label = $derived(entry?.name || baseEntry?.name || target);
</script>

{#if model}
  <a href={href} use:link class="text-primary hover:underline">
    {label}
  </a>
{:else}
  <span class="text-muted-foreground">-</span>
{/if}
