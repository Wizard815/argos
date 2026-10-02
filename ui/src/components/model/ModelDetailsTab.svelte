<script lang="ts">
  import type { Model } from "../../lib/types";
  import { capabilityLabels } from "../../lib/capabilities";
  import { fetchOdysseusModelState, setOdysseusModelProfile, setModelMode, MODEL_MODES, type ModelMode } from "../../stores/api";
  import * as Card from "$lib/components/ui/card/index.js";
  import * as Select from "$lib/components/ui/select/index.js";
  import Tag from "../Tag.svelte";

  interface Props {
    model: Model;
  }

  let { model }: Props = $props();

  let capabilities = $derived.by(() => {
    const caps = model?.capabilities ?? {};
    return Object.entries(caps).filter(([, v]) => v);
  });

  // ---- Odysseus per-model profile picker ----
  // The model's `profile` field is its current choice and `cmd`/`env` are the
  // EFFECTIVE launch (the chosen variant's), so what this card shows is what
  // llama-swap will actually run.
  let odysseus = $state<{ labels: string[]; chosen: string } | null>(null);
  let saving = $state(false);
  let saveError = $state("");
  const NONE = "__none__";

  async function loadOdysseusState(id: string): Promise<void> {
    odysseus = null;
    saveError = "";
    try {
      const state = await fetchOdysseusModelState(id);
      if (state.enabled && (state.labels?.length ?? 0) > 0) {
        odysseus = { labels: state.labels ?? [], chosen: state.chosen ?? "" };
      }
    } catch {
      // the integration being off is normal; stay quiet
    }
  }

  $effect(() => {
    if (model?.id) void loadOdysseusState(model.id);
  });

  async function handleProfileChange(value: string): Promise<void> {
    if (!model?.id) return;
    saving = true;
    saveError = "";
    try {
      await setOdysseusModelProfile(model.id, value === NONE ? null : value);
      // the modelStatus event refreshes cmd/env/profile; re-read the label list
      // in case this was the first choice for the model
      await loadOdysseusState(model.id);
    } catch (e) {
      saveError = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  // ---- GPU toggle mode ----
  // standard is the opt-out: the model is not meant to use either toggle, so
  // its effective launch runs exactly as written.
  let modeSaving = $state(false);
  let modeError = $state("");
  let mode = $derived((model?.mode ?? "standard") as ModelMode);

  async function handleModeChange(next: ModelMode): Promise<void> {
    if (!model?.id || next === mode) return;
    modeSaving = true;
    modeError = "";
    try {
      await setModelMode(model.id, next);
    } catch (e) {
      modeError = e instanceof Error ? e.message : String(e);
    } finally {
      modeSaving = false;
    }
  }
</script>

<Card.Root class="shrink-0 gap-0 overflow-hidden py-0">
  <Card.Header class="border-b px-4 py-2">
    <Card.Title class="text-sm font-semibold">GPU mode</Card.Title>
  </Card.Header>
  <Card.Content class="p-3">
    <div class="flex flex-wrap gap-2">
      {#each MODEL_MODES as opt (opt.value)}
        <button
          type="button"
          class="rounded-md border px-3 py-1.5 text-xs disabled:opacity-50"
          class:bg-muted={mode === opt.value}
          class:font-medium={mode === opt.value}
          disabled={modeSaving}
          onclick={() => void handleModeChange(opt.value)}
        >
          {opt.label}
        </button>
      {/each}
    </div>
    <p class="text-muted-foreground mt-2 text-xs">
      {MODEL_MODES.find((m) => m.value === mode)?.hint ?? ""}
    </p>
    <p class="text-muted-foreground mt-1 text-xs">
      <strong>Standard</strong> leaves the launch as written — pick it for a model
      that is not meant to use either toggle.
    </p>
    {#if modeError}
      <p class="text-destructive mt-2 text-xs">{modeError}</p>
    {/if}
  </Card.Content>
</Card.Root>

<Card.Root class="shrink-0 gap-0 overflow-hidden py-0">
  <Card.Header class="border-b px-4 py-2">
    <Card.Title class="text-sm font-semibold">Odysseus launch profile</Card.Title>
  </Card.Header>
  <Card.Content class="p-3">
    {#if odysseus}
      <div class="flex items-center gap-2">
        <Select.Root
          type="single"
          value={model.profile || NONE}
          onValueChange={(value) => value && void handleProfileChange(value)}
        >
          <Select.Trigger class="w-56" aria-label="Odysseus launch profile" disabled={saving}>
            {model.profile || "llama-swap default"}
          </Select.Trigger>
          <Select.Content>
            <Select.Item value={NONE}>llama-swap default</Select.Item>
            {#each odysseus.labels as label (label)}
              <Select.Item value={label}>{label}</Select.Item>
            {/each}
          </Select.Content>
        </Select.Root>
        {#if saving}
          <span class="text-muted-foreground text-xs">saving…</span>
        {/if}
      </div>
      <p class="text-muted-foreground mt-2 text-xs">
        Pick which of this model's saved Odysseus configs it launches with.
        Other models are unaffected.
      </p>
      {#if saveError}
        <p class="text-destructive mt-2 text-xs">{saveError}</p>
      {/if}
    {:else}
      <span class="text-muted-foreground text-sm">
        No saved Odysseus configs for this model.
      </span>
    {/if}
  </Card.Content>
</Card.Root>

<Card.Root class="shrink-0 gap-0 overflow-hidden py-0">
  <Card.Header class="border-b px-4 py-2">
    <Card.Title class="text-sm font-semibold">Launch command</Card.Title>
  </Card.Header>
  <Card.Content class="p-3">
    {#if model.cmd}
      {#if model.profile}
        <p class="text-muted-foreground mb-2 text-xs">
          Effective launch for profile <strong>{model.profile}</strong>:
        </p>
      {/if}
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
