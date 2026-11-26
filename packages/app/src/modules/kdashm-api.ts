import type { DiscoveryApi, FetchApi } from '@backstage/core-plugin-api';

let requestFetch: typeof fetch = (...args) => window.fetch(...args);

export function configureKDashMFetch(fetchApi: FetchApi, discoveryApi: DiscoveryApi) {
  requestFetch = async (input, init) => {
    const originalUrl = input instanceof Request
      ? input.url
      : input instanceof URL
        ? input.toString()
        : String(input);

    if (originalUrl.startsWith('/api/')) {
      const baseUrl = await discoveryApi.getBaseUrl('kdashm');
      return fetchApi.fetch(`${baseUrl}${originalUrl.slice(4)}`, init);
    }

    return fetchApi.fetch(input, init);
  };
}

export function resetKDashMFetch() {
  requestFetch = (...args) => window.fetch(...args);
}

export function kdashmFetch(input: RequestInfo | URL, init?: RequestInit) {
  return requestFetch(input, init);
}