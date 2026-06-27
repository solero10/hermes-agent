import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { DropdownMenu, DropdownMenuContent } from '@/components/ui/dropdown-menu'
import { $activeSessionId, setCurrentModel, setCurrentProvider } from '@/store/session'

const getGlobalModelOptions = vi.fn()
const getMoaModels = vi.fn()

vi.mock('@/hermes', () => ({
  getGlobalModelOptions: () => getGlobalModelOptions(),
  getMoaModels: () => getMoaModels()
}))

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

beforeEach(() => {
  $activeSessionId.set(null)
  setCurrentModel('')
  setCurrentProvider('')
  getMoaModels.mockResolvedValue({ presets: {} })
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
  $activeSessionId.set(null)
  setCurrentModel('')
  setCurrentProvider('')
})

async function renderModelMenuPanel() {
  const { ModelMenuPanel } = await import('./model-menu-panel')
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={queryClient}>
      <DropdownMenu open>
        <DropdownMenuContent>
          <ModelMenuPanel gateway={undefined} onSelectModel={vi.fn()} requestGateway={vi.fn()} />
        </DropdownMenuContent>
      </DropdownMenu>
    </QueryClientProvider>
  )
}

describe('ModelMenuPanel Codex provider restriction', () => {
  it('renders only ChatGPT OAuth Codex provider rows while preserving future Codex model ids', async () => {
    await renderModelMenuPanel()

    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    expect(await screen.findByText('OpenAI Codex')).toBeTruthy()
    expect(await screen.findByText(/gpt-5\.6-codex-beta/i)).toBeTruthy()
    expect(screen.queryByText('Anthropic')).toBeNull()
    expect(screen.queryByText(/claude-sonnet-4\.6/i)).toBeNull()
  })
})
