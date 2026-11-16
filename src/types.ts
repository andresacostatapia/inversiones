export type Currency = 'CLP' | 'USD'
export type AssetType = 'accion' | 'etf' | 'indice' | 'divisa' | 'otro'
export type Side = 'compra' | 'venta'
export type Channel = 'push' | 'correo' | 'telegram'
export type AlertKind = 'precio_sobre' | 'precio_bajo' | 'cruce_medias' | 'variacion_dia' | 'indicador'

export interface Asset {
  id: string // = símbolo
  name: string
  type: AssetType
  exchange: string
  currency: Currency // moneda en que cotiza
  providerSymbol?: string // símbolo en Yahoo Finance; sin definir = se deduce del mercado; '' = precio manual
}

export interface Broker {
  id: string
  name: string
}

export interface Trade {
  id: string
  date: string // AAAA-MM-DD
  assetId: string
  side: Side
  quantity: number
  price: number
  fee: number
  tax: number
  currency: Currency
  fxRate: number // CLP por 1 USD en la fecha de la operación
  brokerId: string
  note: string
  importBatchId?: string
  fingerprint: string // para detectar duplicados
}

export type NewTrade = Omit<Trade, 'id' | 'fingerprint'>

export interface ImportBatch {
  id: string
  brokerId: string
  fileName: string
  fileHash: string
  importedAt: string
  rowCount: number
}

export interface Quote {
  assetId: string
  price: number
  prevClose: number
  asOf: string
  auto?: boolean // true = vino del proveedor; falso o ausente = ingresado a mano
}

export interface AlertRule {
  id: string
  assetId: string
  kind: AlertKind
  level?: number // precio_sobre / precio_bajo
  pct?: number // variacion_dia (valor absoluto)
  fast?: number // cruce_medias
  slow?: number
  indicator?: 'RSI' | 'MACD' // indicador
  op?: 'menor' | 'mayor'
  threshold?: number
  channels: Channel[]
  muted: boolean
  createdAt: string
}

export interface AlertEvent {
  id: string
  ruleId: string
  assetId: string
  triggeredAt: string
  value: number
  valueText?: string // valor ya formateado (lo envía el servidor)
  message: string
}

export interface Settings {
  displayCurrency: Currency
  lastBrokerId: string
  lastCurrency: Currency
  autoRefreshMin: number // 0 = desactivado
  theme: 'auto' | 'claro' | 'oscuro'
  autoLockMin: number // minutos en segundo plano antes de pedir el PIN; 0 = siempre, -1 = nunca
  notifyPush: boolean
  notifyEmail: boolean
  notifyTelegram: boolean
}
