import { getContext, setContext } from 'svelte';

import { applicationStores } from './stores';

const PERSISTENCE_CONTEXT = Symbol('ed-finder-persistence');

export type PersistenceContext = typeof applicationStores;

/** Install once at the application shell; children receive typed services, not Storage globals. */
export function providePersistenceContext(): PersistenceContext {
  setContext(PERSISTENCE_CONTEXT, applicationStores);
  return applicationStores;
}

export function usePersistenceContext(): PersistenceContext {
  const services = getContext<PersistenceContext | undefined>(
    PERSISTENCE_CONTEXT,
  );
  if (!services) throw new Error('Persistence context has not been provided');
  return services;
}
