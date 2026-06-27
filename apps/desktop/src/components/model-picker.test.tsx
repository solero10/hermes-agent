import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getGlobalModelOptions = vi.fn()
const startManualOnboarding = vi.fn()

vi.mock('@/hermes', () => ({
  getGlobalModelOptions: () => getGlobalModelOptions()
}))

vi.mock('@/store/onboarding', () => ({
  startManualOnboarding: () => startManualOnboarding()
}))

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()

  class MockResizeObserver {
    disconnect = vi.fn()
    observe = vi.fn()
    unobserve = vi.fn()
  }

  globalThis.ResizeObserver = MockResizeObserver as never
})

beforeEach(() => {
  getGlobalModelOptions.mockResolvedValue({
    providers: [
      {
        authenticated: true,
        models: ['gpt-5.5', 'gpt-5.6-codex-beta'],
        name: 'OpenAI Codex',
        slug: 'openai-codex'
      },
      {
        authenticated: true,
        models: ['claude-sonnet-4.6'],
        name: 'Anthropic',
        slug: 'anthropic'
      }
    ]
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderModelPickerDialog() {
  const { ModelPickerDialog } = await import('./model-picker')
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={queryClient}>
      <ModelPickerDialog
        currentModel="claude-sonnet-4.6"
        currentProvider="anthropic"
        onOpenChange={vi.fn()}
        onSelect={vi.fn()}
        open
      />
    </QueryClientProvider>
  )
}

describe('ModelPickerDialog Codex provider restriction', () => {
  it('renders only ChatGPT OAuth Codex model rows while preserving future Codex model ids', async () => {
    await renderModelPickerDialog()

    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    expect(await screen.findByText('OpenAI Codex')).toBeTruthy()
    expect(await screen.findByText('gpt-5.6-codex-beta')).toBeTruthy()
    expect(screen.queryByText('Anthropic')).toBeNull()
    expect(screen.queryByText('claude-sonnet-4.6')).toBeNull()
    expect(screen.getByRole('button', { name: /add provider/i })).toBeTruthy()
  })
})
