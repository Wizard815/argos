<script lang="ts">
  import type { Model } from "../../lib/types";
  import { capabilityLabels } from "../../lib/capabilities";
  import * as Card from "$lib/components/ui/card/index.js";
  import Tag from "../Tag.svelte";

  interface Props {
    model: Model;
  }

  let { model }: Props = $props();

  let capabilities = $derived.by(() => {
    const caps = model?.capabilities ?? {};
    return Object.entries(caps).filter(([, v]) => v);
  });
</script>

<Card.Root class="shrink-0 gap-0 overflow-hidden py-0">
  <Card.Header class="border-b px-4 py-2">
    <Card.Title class="text-sm font-semibold">Capabilities</Card.Title>
  </Card.Header>
  <Card.Content class="p-3">
    {#if capabilities.length === 0}
      <span class="text-muted-foreground text-sm">No capabilities reported.</span>
    {:else}
      <div class="flex flex-wrap gap-1.5">
        {#each capabilities as [key] (key)}
          <Tag>{capabilityLabels[key] ?? key}</Tag>
        {/each}
      </div>
    {/if}
  </Card.Content>
</Card.Root>

<Card.Root class="shrink-0 gap-0 overflow-hidden py-0">
  <Card.Header class="border-b px-4 py-2">
    <Card.Title class="text-sm font-semibold">Launch command</Card.Title>
  </Card.Header>
  <Card.Content class="p-3">
    {#if model.cmd}
      <pre
        class="bg-muted overflow-x-auto rounded-md p-2 text-xs leading-relaxed break-all whitespace-pre-wrap">{model.cmd}</pre>
    {:else}
      <span class="text-muted-foreground text-sm">No launch command reported for this model.</span>
    {/if}
    {#if model.env && model.env.length > 0}
      <div class="mt-3">
        <span class="text-muted-foreground text-xs">Environment</span>
        <div class="mt-1 flex flex-wrap gap-1.5">
          {#each model.env as entry (entry)}
            <Tag>{entry}</Tag>
          {/each}
        </div>
      </div>
    {/if}
    <p class="text-muted-foreground mt-3 text-xs">
      The <code>&#36;&#123;PORT&#125;</code> macro is replaced with the port llama-swap allocates
      for this model, so this is what is run after substitution.
    </p>
  </Card.Content>
</Card.Root>
