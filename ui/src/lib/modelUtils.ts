import type { Model } from "./types";

export interface GroupedModels {
  local: Model[];
  localMatching: Model[];
}

export function modelServerPath(modelId: string): string {
  if (modelId === "comfyui_auto") return "/comfyui/";
  return `/upstream/${encodeURIComponent(modelId)}/`;
}

export function matchesCapabilities(model: Model, required: string[], matchAny = false): boolean {
  if (!required.length) return true;
  if (!model.capabilities) return false;
  const caps = model.capabilities as Record<string, boolean>;
  if (matchAny) {
    return required.some((cap) => caps[cap] === true);
  }
  return required.every((cap) => caps[cap] === true);
}

export function groupModels(models: Model[], capabilities?: string[], matchAny = false): GroupedModels {
  const available = models.filter((m) => !m.unlisted);
  const local = available;

  let localMatching: Model[] = [];
  let localRest: Model[] = [];

  if (capabilities && capabilities.length > 0) {
    for (const model of local) {
      if (matchesCapabilities(model, capabilities, matchAny)) {
        localMatching.push(model);
      } else {
        localRest.push(model);
      }
    }
  } else {
    localRest = local;
  }

  return { local: localRest, localMatching };
}

/** How Argos shows a model id: a profile <base>--<label> by its label (what apps
 * see too), a multi_model copy <id>--mmN as "<label> · copy N". */
export function shortModelId(id: string): string {
  const copy = id.match(/^(.*)--mm(\d+)$/);
  if (copy) return `${shortModelId(copy[1])} · copy ${copy[2]}`;
  const at = id.indexOf("--");
  return at > 0 && at + 2 < id.length ? id.slice(at + 2) : id;
}
