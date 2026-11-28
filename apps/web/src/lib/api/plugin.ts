// Keys for ED-Finder's EDMC plugin ("Haul — design", step 2a).
// Offered to the app through ./client, like everything in this folder.
import type { NewPluginKey, PluginKey } from './generated';
import {
  createPluginKey as createPluginKeyCall,
  listPluginKeys as listPluginKeysCall,
  revokePluginKey as revokePluginKeyCall,
} from './generated/sdk.gen';

export type { NewPluginKey, PluginKey };

export const getPluginKeys = async (
  signal?: AbortSignal,
): Promise<readonly PluginKey[]> =>
  (await listPluginKeysCall({ throwOnError: true, signal })).data;

/** The answer holds the key itself, once: show it, then forget it. */
export const makePluginKey = async (label: string): Promise<NewPluginKey> =>
  (await createPluginKeyCall({ throwOnError: true, body: { label } })).data;

export const cancelPluginKey = async (pluginKeyId: string): Promise<void> => {
  await revokePluginKeyCall({
    throwOnError: true,
    path: { plugin_key_id: pluginKeyId },
  });
};
