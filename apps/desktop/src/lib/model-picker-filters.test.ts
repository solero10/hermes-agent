import { describe, expect, it } from 'vitest'

import { CHATGPT_OAUTH_CODEX_PROVIDER_SLUG, filterChatGptOAuthCodexProviders } from './model-picker-filters'

describe('model picker filters', () => {
  it('keeps only configured ChatGPT OAuth Codex providers and preserves model ids', () => {
    expect(
      filterChatGptOAuthCodexProviders([
        {
          models: ['gpt-5.5', 'gpt-5.6-codex-beta'],
          name: 'OpenAI Codex',
          slug: CHATGPT_OAUTH_CODEX_PROVIDER_SLUG
        },
        { models: [], name: 'Empty Codex', slug: CHATGPT_OAUTH_CODEX_PROVIDER_SLUG },
        { models: ['claude-sonnet-4.6'], name: 'Anthropic', slug: 'anthropic' },
        { models: ['gpt-5.5'], name: 'OpenAI API', slug: 'openai-api' }
      ])
    ).toEqual([
      {
        models: ['gpt-5.5', 'gpt-5.6-codex-beta'],
        name: 'OpenAI Codex',
        slug: CHATGPT_OAUTH_CODEX_PROVIDER_SLUG
      }
    ])
  })
})
