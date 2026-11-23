import { describe, expect, it } from 'vitest'
import { day, money, num, pct, price, signClass } from './format'

describe('formato chileno', () => {
  it('usa punto de miles y coma decimal', () => {
    expect(num(1234567.891, 2)).toBe('1.234.567,89')
    expect(num(null)).toBe('—')
    expect(num(NaN)).toBe('—')
  })
  it('precios CLP grandes sin decimales, USD con 2', () => {
    expect(price(49780, 'CLP')).toBe('49.780')
    expect(price(188.35, 'CLP')).toBe('188,35')
    expect(price(722.5, 'USD')).toBe('722,50')
    expect(money(722.5, 'USD')).toBe('US$ 722,50')
    expect(money(1016, 'CLP')).toBe('$ 1.016')
  })
  it('porcentajes con signo', () => {
    expect(pct(2.014, 2)).toBe('+2,01 %')
    expect(pct(-0.45, 2)).toBe('-0,45 %')
    expect(pct(0)).toBe('0,0 %')
    expect(pct(12.3, 1, false)).toBe('12,3 %')
  })
  it('fechas y clases de signo', () => {
    expect(day('2026-11-23')).toMatch(/23 nov\.? 2026/)
    expect(signClass(1)).toBe('gain')
    expect(signClass(-1)).toBe('loss')
    expect(signClass(0)).toBe('')
  })
})

import { clp, parseNum } from './format'

describe('lectura de números escritos a mano', () => {
  it.each([
    ['48.500', 48500],
    ['1.234,56', 1234.56],
    ['1,234.56', 1234.56],
    ['262,10', 262.1],
    ['650.5', 650.5],
    ['$ 1.000.000', 1000000],
    ['abc', null],
  ])('%s → %s', (raw, expected) => {
    expect(parseNum(raw)).toBe(expected)
  })
  it('formatea CLP con signo', () => {
    expect(clp(-1234567.4)).toBe('-$ 1.234.567')
    expect(clp(500, true)).toBe('+$ 500')
  })
})
