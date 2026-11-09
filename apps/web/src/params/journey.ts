import type { ParamMatcher } from '@sveltejs/kit';

const journeys = new Set(['explore', 'inspect', 'review']);

export const match: ParamMatcher = (param) => journeys.has(param);
