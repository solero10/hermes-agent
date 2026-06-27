export const CHATGPT_OAUTH_CODEX_PROVIDER_SLUG = 'openai-codex' as const

export function isChatGptOAuthCodexProvider(provider: { slug: string; models?: string[] }) {
  return provider.slug === CHATGPT_OAUTH_CODEX_PROVIDER_SLUG && (provider.models ?? []).length > 0
}

export function filterChatGptOAuthCodexProviders<T extends { slug: string; models?: string[] }>(providers: T[]): T[] {
  return providers.filter(isChatGptOAuthCodexProvider)
}
