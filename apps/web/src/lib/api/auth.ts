// Who is signed in, the site's health, and signing in and out.
// Offered to the app through ./client, like everything in this folder.
import type {
  AuthSessionResponse,
  ExternalIdentityResponse,
  HealthResponse,
} from './generated';
import {
  authLogoutApiV1AuthLogoutPost,
  authSessionApiV1AuthSessionGet,
  claimOwnerApiV1AuthOwnerClaimPost,
  frontierLinkApiV1AuthFrontierLinkPost,
  healthApiHealthGet,
  listIdentitiesApiV1AuthIdentitiesGet,
  unlinkIdentityApiV1AuthIdentitiesExternalIdentityIdDelete,
} from './generated/sdk.gen';

export const getHealth = async (
  signal?: AbortSignal,
): Promise<HealthResponse> =>
  (await healthApiHealthGet({ throwOnError: true, signal })).data;

export type AuthIdentity = ExternalIdentityResponse;

export const getAuthSession = async (
  signal?: AbortSignal,
): Promise<AuthSessionResponse> =>
  (await authSessionApiV1AuthSessionGet({ throwOnError: true, signal })).data;

export const getAuthIdentities = async (
  signal?: AbortSignal,
): Promise<readonly AuthIdentity[]> =>
  (await listIdentitiesApiV1AuthIdentitiesGet({ throwOnError: true, signal }))
    .data;

export const startFrontierLink = async (returnTo: string): Promise<string> => {
  const { data } = await frontierLinkApiV1AuthFrontierLinkPost({
    throwOnError: true,
    query: { return_to: returnTo },
  });
  return data.authorization_url;
};

export const unlinkAuthIdentity = async (
  externalIdentityId: string,
): Promise<AuthSessionResponse> =>
  (
    await unlinkIdentityApiV1AuthIdentitiesExternalIdentityIdDelete({
      throwOnError: true,
      path: { external_identity_id: externalIdentityId },
    })
  ).data;

export const authLogout = async <T = AuthSessionResponse>(): Promise<T> =>
  (await authLogoutApiV1AuthLogoutPost({ throwOnError: true }))
    .data as unknown as T;

export const claimOwner = async <T = AuthSessionResponse>(
  adminToken: string,
): Promise<T> =>
  (
    await claimOwnerApiV1AuthOwnerClaimPost({
      throwOnError: true,
      body: { admin_token: adminToken },
    })
  ).data as unknown as T;

export const frontierLoginUrl = (returnTo: string): string =>
  `/api/v1/auth/frontier/login?return_to=${encodeURIComponent(returnTo)}`;
