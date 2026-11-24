import { describe, expect, it } from 'vitest'
import { CATEGORIES, searchCatalog } from './catalog'
import { investability } from './portfolio'

const none = new Set<string>()
const syms = (q: string) => searchCatalog(q, none).map((r) => r.symbol)

describe('catálogo de populares', () => {
  it('encuentra por nombre en español, sin tildes y por código', () => {
    expect(syms('oro')).toEqual(expect.arrayContaining(['GLD', 'IAU', 'GC=F']))
    expect(syms('oro')).not.toContain('CONCHATORO.SN') // coincide al comienzo de una palabra, no en medio
    expect(syms('concha')).toEqual(['CONCHATORO.SN'])
    expect(syms('cobre')).toEqual(expect.arrayContaining(['CPER', 'HG=F']))
    expect(syms('petróleo')).toEqual(expect.arrayContaining(['USO', 'CL=F']))
    expect(syms('s&p 500')).toEqual(expect.arrayContaining(['VOO', 'SPY', 'IVV', '^GSPC']))
    expect(syms('samsung')).toEqual(['SMSN.IL', '005930.KS'])
    expect(syms('nvda')[0]).toBe('NVDA')
    expect(syms('quinenco')).toEqual(['QUINENCO.SN'])
    expect(syms('x')).toEqual([]) // menos de 2 letras: nada
  })

  it('marca lo que el perfil no permite comprar', () => {
    const by = Object.fromEntries(searchCatalog('oro', none).map((r) => [r.symbol, r]))
    expect(by.GLD.note).toBeNull()
    expect(by['GC=F'].note).toMatch(/derivado/)
    const sam = Object.fromEntries(searchCatalog('samsung', none).map((r) => [r.symbol, r]))
    expect(sam['SMSN.IL'].note).toBeNull() // en USD: se puede llevar en la cartera
    expect(sam['005930.KS'].note).toMatch(/KRW/)
    expect(searchCatalog('s&p 500', new Set(['VOO'])).find((r) => r.symbol === 'VOO')!.in_watchlist).toBe(true)
  })

  it('cada ETF y acción del catálogo es comprable; cada índice y futuro no', () => {
    for (const cat of CATEGORIES) {
      for (const it of cat.items) {
        const note = investability(it.symbol, it.kind, (it as { currency?: string }).currency ?? null, it.name)
        const shouldBlock = ['INDEX', 'FUTURE'].includes(it.kind) || (it as { currency?: string }).currency === 'KRW'
        expect([it.symbol, note !== null]).toEqual([it.symbol, shouldBlock])
      }
    }
  })
})
