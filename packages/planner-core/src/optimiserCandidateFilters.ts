import type { SuggestedBuildScale } from './optimiserQuality';

export type SuggestedBuildScaleFilter = 'starter' | 'expansion' | 'full';

export interface GeneratedCandidateParams {
  targetArchetype: string;
  maxCandidates: number;
  allowEstimatedData: boolean;
  scale: SuggestedBuildScaleFilter;
}

export function scaleMatchesFilter(
  scale: SuggestedBuildScale,
  filter: SuggestedBuildScaleFilter,
): boolean {
  if (filter === 'starter') return scale === 'starter' || scale === 'bootstrap';
  if (filter === 'expansion') return scale === 'starter' || scale === 'expansion' || scale === 'full';
  return scale === 'expansion' || scale === 'full';
}

export function scaleLabel(scale: SuggestedBuildScaleFilter): string {
  if (scale === 'starter') return 'Starter';
  if (scale === 'expansion') return 'Expansion';
  return 'Full / Ambitious';
}

export function candidateGenerationControlsChanged(
  generated: GeneratedCandidateParams | null,
  current: GeneratedCandidateParams,
): boolean {
  return Boolean(generated && (
    generated.targetArchetype !== current.targetArchetype
    || generated.maxCandidates !== current.maxCandidates
    || generated.allowEstimatedData !== current.allowEstimatedData
    || generated.scale !== current.scale
  ));
}
