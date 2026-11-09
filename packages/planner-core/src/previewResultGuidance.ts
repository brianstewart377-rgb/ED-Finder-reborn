import type { SimulateBuildResponse } from '@ed-finder/api-client/types';

export type PreviewGuidanceTone = 'good' | 'warn' | 'danger' | 'info';

export interface PreviewGuidance {
  tone: PreviewGuidanceTone;
  title: string;
  items: string[];
}

export function buildPreviewResultGuidance(
  result: SimulateBuildResponse | null,
  isResultStale: boolean,
): PreviewGuidance {
  if (!result) {
    return {
      tone: 'info',
      title: 'Preview not run yet',
      items: [
        'Run Preview when the Build Plan is ready.',
        'Preview will estimate CP, economy, service, and buildability outcomes for the current Build Plan.',
      ],
    };
  }

  if (isResultStale) {
    return {
      tone: 'warn',
      title: 'Preview is stale',
      items: [
        'The Build Plan has changed since this Preview Result was created.',
        'Run Preview again before using this result to decide your next action.',
      ],
    };
  }

  const warnings = result.warnings.length;
  const lowBuildability = result.buildability_score < 55;
  const lowScore = result.final_score < 55;
  const lowConfidence = result.confidence < 0.5;

  if (warnings > 0 || lowBuildability || lowScore) {
    const reasons = [
      lowBuildability ? `Buildability is low (${Math.round(result.buildability_score)}).` : '',
      lowScore ? `Final score is low (${Math.round(result.final_score)}).` : '',
      warnings > 0 ? `${warnings} warning${warnings === 1 ? '' : 's'} reported.` : '',
      lowConfidence ? 'Confidence is limited; treat this as an estimate.' : '',
    ].filter(Boolean);
    return {
      tone: 'warn',
      title: 'Needs work',
      items: [
        `This plan needs review: ${reasons.join(' ')}`,
        'Adjust the Build Plan or generate Suggested Builds, then run Preview again.',
        'After checking in-game, record Observed Evidence and use Validation if you want to compare prediction with what you saw.',
      ],
    };
  }

  if (lowConfidence) {
    return {
      tone: 'info',
      title: 'Viable estimate with limited confidence',
      items: [
        'This plan looks usable, but confidence is limited. Treat the result as an estimate.',
        'Review assumptions and warnings, compare Suggested Builds if useful, and record Observed Evidence after checking in-game.',
      ],
    };
  }

  return {
    tone: 'good',
    title: 'Looks viable',
    items: [
      'This plan looks viable based on the current Preview Result.',
      'Compare Suggested Builds or inspect assumptions before committing in-game.',
      'After checking in-game, record Observed Evidence and use Validation to compare prediction with what you saw.',
    ],
  };
}
